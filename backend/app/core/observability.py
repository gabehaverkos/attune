"""What the app reports about itself: opt-in traces, and content-free log events.

Traces go to the private Langfuse OTLP endpoint. A private provider avoids
exporting unrelated libraries' global instrumentation. Export is batched,
bounded, and best-effort; no connection check gates startup, and Langfuse may
come and go while the app runs: traces are dropped while it is unreachable and
export resumes on its own when it returns.

Events are JSON lines on stdout for following chat and memory through Docker
logs. Callers supply metadata only: never prompts, source text, or exception
messages.
"""

import asyncio
import base64
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, UTC
from functools import wraps
import json
import logging
import os
import sys
from typing import Any, Callable, Coroutine, ParamSpec, TypeVar

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import get_current_span, SpanKind, Status, StatusCode
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings



# --- Tracing ---------------------------------------------------------------------

logger = logging.getLogger(__name__)

# Langfuse Cloud, where traces go once a project's two keys are set.
LANGFUSE_URL = "https://cloud.langfuse.com"
_provider: TracerProvider | None = None
P = ParamSpec("P")
R = TypeVar("R")

# Baseline metadata allowlist. Content requires a separate explicit opt-in;
# exception messages/stack traces, resource environment and span events stay out.
_SAFE_ATTRIBUTES = frozenset({
    "gen_ai.system", "gen_ai.provider.name", "gen_ai.operation.name",
    "gen_ai.request.model", "gen_ai.response.model", "gen_ai.tool.name",
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.details.cached_tokens", "gen_ai.usage.details.reasoning_tokens",
    "attune.operation", "error.type",
    "gen_ai.response.id", "gen_ai.response.finish_reasons",
    "gen_ai.request.temperature", "gen_ai.request.max_tokens", "gen_ai.request.top_p",
    "gen_ai.usage.cost", "gen_ai.usage.details.cost_details.upstream_inference_cost",
    "gen_ai.response.provider", "gen_ai.request.seed",
    "gen_ai.agent.name", "gen_ai.agent.call.id", "gen_ai.tool.type",
    "attune.time_to_first_text_ms",
})


# Deliberately enumerate content fields: never forward arbitrary headers, environment,
# exception text, or object dumps (which may contain credentials).
_CONTENT_ATTRIBUTES = frozenset({
    "gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions",
    "gen_ai.tool.definitions", "gen_ai.tool.call.arguments", "gen_ai.tool.call.result",
    "gen_ai.tool.call.id", "all_messages", "final_result", "tool_arguments", "tool_response",
    "model_request_parameters", "pydantic_ai.all_messages",
    "langfuse.observation.input", "langfuse.observation.output",
    "langfuse.session.id", "langfuse.trace.input", "langfuse.trace.output",
})


def content_tracing_enabled() -> bool:
    return os.environ.get("LANGFUSE_INCLUDE_CONTENT", "false").lower() == "true"


def record_turn_content(*, session_id: str | None = None, input: str | None = None) -> None:
    if _provider is None or not content_tracing_enabled():
        return
    span = get_current_span()
    for key, value in {"langfuse.session.id": session_id, "langfuse.trace.input": input,
                       "langfuse.observation.input": input}.items():
        if value is not None:
            span.set_attribute(key, value)


def record_content(span: Any = None, *, input: Any = None, output: Any = None) -> None:
    """Show what a step read and returned on its span in Langfuse, when content
    tracing is on. Anything that is not text is written as JSON."""
    if _provider is None or not content_tracing_enabled():
        return
    span = span or get_current_span()
    for key, value in {"langfuse.observation.input": input, "langfuse.observation.output": output}.items():
        if value is not None:
            span.set_attribute(key, value if isinstance(value, str) else json.dumps(value, default=str))


def openrouter_trace_body(session_id: str, generation_name: str) -> dict[str, Any]:
    """Opaque correlation IDs only; no additional network calls or paid generations."""
    body: dict[str, Any] = {"session_id": session_id}
    context = get_current_span().get_span_context()
    if context.is_valid:
        body["trace"] = {"trace_id": format(context.trace_id, "032x"),
                         "trace_name": "chat.turn", "generation_name": generation_name}
    return body


def _with_cost(attributes: dict[str, Any]) -> dict[str, Any]:
    """Give a model call its cost, from its tokens at the listed price, for Langfuse
    to total. pydantic-ai drops the cost OpenRouter reports; a call that already
    carries its real cost (a decision) keeps it. Only call spans get one, so an
    agent run's totals are not counted twice."""
    from app.config import MODEL_PRICES

    if attributes.get("gen_ai.operation.name") != "chat" or "gen_ai.usage.cost" in attributes:
        return attributes
    model = attributes.get("gen_ai.response.model") or attributes.get("gen_ai.request.model")
    price = MODEL_PRICES.get(str(model))
    if price is None or attributes.get("gen_ai.usage.input_tokens") is None:
        return attributes
    return {**attributes, "gen_ai.usage.cost": price.cost(
        int(attributes["gen_ai.usage.input_tokens"]),
        int(attributes.get("gen_ai.usage.cache_read.input_tokens") or 0),
        int(attributes.get("gen_ai.usage.output_tokens") or 0),
    )}


class MetadataOnlyExporter(SpanExporter):
    """Allow approved metadata and optional content, never SDK error events."""

    def __init__(self, delegate: SpanExporter, service_name: str, *, include_content: bool = False):
        self.allowed_attributes = _SAFE_ATTRIBUTES | (_CONTENT_ATTRIBUTES if include_content else frozenset())
        self.delegate = delegate
        self.resource = Resource({"service.name": service_name})
        self.reachable = True

    def export(self, spans) -> SpanExportResult:
        try:
            sanitized = [ReadableSpan(
                name=span.name,
                context=span.context,
                parent=span.parent,
                resource=self.resource,
                attributes=_with_cost({key: value for key, value in (span.attributes or {}).items()
                                       if key in self.allowed_attributes}),
                kind=span.kind,
                status=Status(span.status.status_code),
                start_time=span.start_time,
                end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            ) for span in spans]
        except Exception:
            logger.warning("Tracing export failed; batch dropped.")
            return SpanExportResult.FAILURE
        try:
            result = self.delegate.export(sanitized)
        except OSError:
            # Langfuse runs as its own stack and may be started or stopped at
            # any time. Note each transition once rather than every dropped batch.
            if self.reachable:
                self.reachable = False
                logger.info("Langfuse is unreachable; dropping traces until it returns.")
            return SpanExportResult.FAILURE
        except Exception:
            logger.warning("Tracing export failed; batch dropped.")
            return SpanExportResult.FAILURE
        if not self.reachable:
            self.reachable = True
            logger.info("Langfuse is reachable again; exporting traces.")
        return result

    def shutdown(self) -> None:
        self.delegate.shutdown()


def configure_tracing(service_name: str) -> None:
    """Turn tracing on for this process when Langfuse is configured, and say so.

    Each process states whether it traces and where to, once, so an empty Langfuse
    can be told apart from a process that was never sending to it.
    """
    global _provider
    if _provider is not None:
        return
    from app.config import load_backend_env

    load_backend_env()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    # Configured project keys are the opt-in; with neither set, tracing is off.
    if not public_key and not secret_key:
        event("tracing.off", service=service_name, reason="no Langfuse project keys")
        return
    if not public_key or not secret_key:
        logger.warning("Tracing disabled: a Langfuse project key is missing.")
        event("tracing.off", service=service_name, reason="a Langfuse project key is missing")
        return
    provider = None
    try:
        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        exporter = MetadataOnlyExporter(OTLPSpanExporter(
            endpoint=f"{LANGFUSE_URL}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"}, timeout=2,
        ), service_name, include_content=content_tracing_enabled())
        provider = TracerProvider(resource=Resource({"service.name": service_name}), shutdown_on_exit=False)
        provider.add_span_processor(BatchSpanProcessor(
            exporter, max_queue_size=512, max_export_batch_size=64,
            schedule_delay_millis=1000, export_timeout_millis=2000,
        ))
        Agent.instrument_all(InstrumentationSettings(
            tracer_provider=provider, include_content=content_tracing_enabled(), include_binary_content=False,
            version=3,
        ))
        _provider = provider
        event("tracing.on", service=service_name, endpoint=LANGFUSE_URL, content=content_tracing_enabled())
    except Exception:
        Agent.instrument_all(False)
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:
                pass
        logger.warning("Tracing initialization failed; continuing without tracing.")


async def shutdown_tracing() -> None:
    global _provider
    provider, _provider = _provider, None
    if provider is None:
        return
    Agent.instrument_all(False)
    try:
        await asyncio.to_thread(provider.shutdown)
    except Exception:
        logger.warning("Tracing shutdown failed; pending traces may be lost.")


@contextmanager
def operation(name: str):
    """Static operation names only; arguments and results are never captured."""
    if _provider is None:
        yield
        return
    tracer = _provider.get_tracer("attune")
    with tracer.start_as_current_span(
        name, attributes={"attune.operation": name},
        record_exception=False, set_status_on_exception=False,
    ) as span:
        try:
            yield
        except BaseException as error:
            span.set_status(StatusCode.ERROR)
            span.set_attribute("error.type", type(error).__name__)
            raise


def traced(name: str):
    """Trace an async operation without recording its inputs or outputs."""
    def decorate(function: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(function)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with operation(name):
                return await function(*args, **kwargs)
        return wrapped
    return decorate


def _present(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attributes.items() if value is not None}


@contextmanager
def _model_call(operation_name: str, provider: str, model: str | None):
    assert _provider is not None
    tracer = _provider.get_tracer("attune")
    with tracer.start_as_current_span(
        f"{operation_name} {model}", kind=SpanKind.CLIENT,
        attributes=_present({
            "gen_ai.operation.name": operation_name, "gen_ai.provider.name": provider,
            "gen_ai.system": provider, "gen_ai.request.model": model,
        }),
        record_exception=False, set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as error:
            span.set_status(StatusCode.ERROR)
            span.set_attribute("error.type", type(error).__name__)
            raise


@contextmanager
def decision_call(model: str):
    """A span for one decision-model request, or None while tracing is off. The
    caller records usage on it."""
    if _provider is None:
        yield None
        return
    with _model_call("decision", "openrouter", model) as span:
        yield span


def trace_embeddings(client: Any) -> None:
    """Record model, token usage, and timing for one Voyage client's embedding calls."""
    embed = client.embed

    @wraps(embed)
    async def traced_embed(*args: Any, **kwargs: Any) -> Any:
        if _provider is None:
            return await embed(*args, **kwargs)
        with _model_call("embeddings", "voyage", kwargs.get("model")) as span:
            result = await embed(*args, **kwargs)
            span.set_attributes(_present({
                "gen_ai.response.model": kwargs.get("model"),
                "gen_ai.usage.input_tokens": getattr(result, "total_tokens", None),
            }))
            return result

    client.embed = traced_embed


# --- Events ----------------------------------------------------------------------

job_context: ContextVar[str | None] = ContextVar("memory_job_id", default=None)
event_logger = logging.getLogger("attune.memory")


def configure_memory_logging() -> None:
    """Configure our event_logger; leave the rest unchanged."""
    if not event_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        event_logger.addHandler(handler)
    event_logger.setLevel(logging.INFO)
    event_logger.propagate = False


def event(name: str, *, level: int = logging.INFO, **fields: Any) -> None:
    # Callers supply metadata only: never prompts, source text, or exception messages.
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": name,
        "level": logging.getLevelName(level),
        **fields,
    }
    if job_context.get() is not None:
        payload["job_id"] = job_context.get()
    event_logger.log(level, json.dumps(payload, default=str))
