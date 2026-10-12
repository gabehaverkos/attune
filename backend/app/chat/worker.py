"""The chat worker, and the one agent it runs.

The worker claims queued runs under a lease and drives each to an outcome.
Ownership is renewed every few seconds. If renewal fails the run is abandoned
locally at once, and every write it might still attempt is fenced off by the
database. Recovery settles runs whose worker vanished; it never replays them.

One agent understands the turn, calls tools when it needs them, and writes the
reply. Multi-step tool use is still one run; nothing rewrites its prose. Only
public text and fixed tool labels leave the agent: no reasoning, no tool
arguments, no tool results.

A tool can defer its result to the user's decision on a card. The agent then
stops with the cards it waits on and its messages so far; the run is not settled
but held, and leaves the worker. Once the cards are decided it comes back
through the queue and picks up from those messages, with the decisions as the
tools' results.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
import os
import socket
import time
from typing import Any
import uuid

import httpx
from opentelemetry.trace import get_current_span
from pydantic import BaseModel
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, UsageLimits
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent, ModelMessagesTypeAdapter, PartDeltaEvent, PartStartEvent, TextPart,
    TextPartDelta,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from sqlalchemy import select

from app import config
from app.chat.tools import ChatToolbox, register_tools, TurnDeps
from app.chat import runs
from app.chat.context import build_turn_context, load_snapshot, TurnContext
from app.chat.runs import ActivityLabels, ConversationRun, RUN_QUEUED, RunActivity, RunFencedError
from app.config import CHAT_MODEL, CHAT_PROVIDER_ROUTING, ModelScope, ModelUnconfigured
from app.core.database import SessionLocal
from app.core.observability import event, openrouter_trace_body, record_turn_content, traced
from app.core.queue import wait_for_wakeup, wakeups


logger = logging.getLogger(__name__)


# --- The agent -------------------------------------------------------------------

# Budgets for one run. Hitting one ends the run as `budget_exhausted`; the run is
# never silently restarted.
REQUEST_LIMIT = 7
TOOL_CALL_LIMIT = 9
OUTPUT_TOKEN_LIMIT = 6000
PROVIDER_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# Text is written in small batches rather than per token.
TEXT_FLUSH_SECONDS = 0.075


def single_spaced(opened: bool) -> Callable[[str], str]:
    """A reply's text as it is published: never more than one blank line running.

    Each step of a reply starts a paragraph, and the chat model opens and closes
    its own steps with blank lines too (a step that only calls a tool is often
    nothing else), which together left a wide gap around every tool call. A reply
    not yet `opened` has its leading line breaks dropped as well.
    """
    breaks = 0 if opened else 2

    def squeeze(text: str) -> str:
        nonlocal breaks
        kept = []
        for character in text:
            if character == "\n":
                breaks += 1
                if breaks > 2:
                    continue
            elif not character.isspace():
                breaks = 0
            kept.append(character)
        return "".join(kept)

    return squeeze


def without_emoji(value: str) -> str:
    """Enforce the no-emoji product voice on model output."""
    return "".join(
        character
        for character in value
        if not (
            "\U0001f000" <= character <= "\U0001faff"
            or "\U0001f1e6" <= character <= "\U0001f1ff"
            or "☀" <= character <= "➿"
            or "\U0001f3fb" <= character <= "\U0001f3ff"
            or character in {"‍", "️", "⃣"}
        )
    )


class AssistantUnconfigured(RuntimeError):
    pass


class RunPublisher:
    """Batches reply text and writes it, with activity, to the run's event log.

    Writes are ordered: each one waits for the previous. The actual write is
    passed in by the worker.
    """

    def __init__(self, write: Callable[[str, list[BaseModel]], Awaitable[None]]):
        self._write = write
        self._buffer: list[str] = []
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task | None = None
        self.first_text_at: float | None = None
        self.text_characters = 0
        self._error: BaseException | None = None

    def _raise_deferred(self) -> None:
        if self._error is not None:
            error, self._error = self._error, None
            raise error

    async def text(self, delta: str) -> None:
        self._raise_deferred()
        if not delta:
            return
        if self.first_text_at is None:
            self.first_text_at = time.perf_counter()
        self._buffer.append(delta)
        self.text_characters += len(delta)
        if self._timer is None or self._timer.done():
            self._timer = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(TEXT_FLUSH_SECONDS)
        try:
            await self.flush()
        except Exception as error:
            # Surfaced on the run's own path, at its next write.
            self._error = error

    async def flush(self, *events: BaseModel) -> None:
        self._raise_deferred()
        async with self._lock:
            text = "".join(self._buffer)
            self._buffer.clear()
            if text or events:
                await self._write(text, list(events))

    async def activity(self, kind: str, label: str, tool_call_id: str | None = None) -> None:
        await self.flush(RunActivity(kind=kind, label=label, tool_call_id=tool_call_id))  # type: ignore[arg-type]

    async def take_pending(self) -> str:
        """Unwritten text, for the terminal write to include, after any write in flight."""
        async with self._lock:
            if self._timer is not None and not self._timer.done():
                self._timer.cancel()
            text = "".join(self._buffer)
            self._buffer.clear()
            return text


def build_agent(
    context: TurnContext, model: Model | str,
) -> tuple[Agent[TurnDeps, str | DeferredToolRequests], ChatToolbox]:
    # The instructions travel in the message history, in order; see TurnContext.
    agent: Agent[TurnDeps, str | DeferredToolRequests] = Agent(
        model, deps_type=TurnDeps, output_type=[str, DeferredToolRequests], retries=2,
    )
    tools = ChatToolbox(agent)
    register_tools(tools, context.enabled_interfaces)
    return agent, tools


def chat_model(scope: ModelScope, http_client: httpx.AsyncClient) -> OpenAIChatModel:
    """The chat model for this run, or a failure the run can report as its own."""
    try:
        return config.openrouter_model(scope, http_client)
    except ModelUnconfigured as error:
        raise AssistantUnconfigured("The assistant service is not configured.") from error


@dataclass
class AgentOutcome:
    usage: dict[str, Any] = field(default_factory=dict)
    # Set when the run stopped to wait on cards: what `ConversationRun.hold` keeps.
    hold: dict[str, Any] | None = None


async def run_agent(
    context: TurnContext, deps: TurnDeps, publisher: RunPublisher, meter: dict[str, int] | None = None,
) -> AgentOutcome:
    """Run the turn. `meter` holds the usage of the requests finished so far, so a
    run stopped or failing partway is still charged for them."""
    scope = CHAT_MODEL
    started = time.perf_counter()
    span = get_current_span()
    called: list[str] = []
    deps.loaded_interfaces = set(context.loaded_interfaces)
    resumption = context.resumption
    # A resumed reply's first new text starts a paragraph after what it already said.
    continues_text = bool(resumption and resumption.continues_text)
    spaced = single_spaced(opened=continues_text)
    resumed = context.resumed_messages() if resumption else []
    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as http_client:
        agent, tools = build_agent(context, chat_model(scope, http_client))
        async with agent.iter(
            None if resumption else context.user_prompt(), deps=deps,
            message_history=[*context.message_history(), *resumed],
            deferred_tool_results=DeferredToolResults(calls=dict(resumption.decisions)) if resumption else None,
            model_settings=scope.model_settings(
                temperature=0.5, extra_body={
                    **openrouter_trace_body(deps.thread_id, "chat.agent"),
                    "provider": CHAT_PROVIDER_ROUTING,
                },
            ),
            usage_limits=UsageLimits(
                request_limit=REQUEST_LIMIT, tool_calls_limit=TOOL_CALL_LIMIT,
                output_tokens_limit=OUTPUT_TOKEN_LIMIT,
            ),
        ) as agent_run:
            async for node in agent_run:
                if meter is not None:
                    meter.update(input_tokens=agent_run.usage.input_tokens,
                                 cache_read_tokens=agent_run.usage.cache_read_tokens,
                                 output_tokens=agent_run.usage.output_tokens)
                if Agent.is_model_request_node(node):
                    await publisher.activity("thinking", ActivityLabels.THINKING)
                    # Text from a later step starts a new paragraph, not the end of the last one.
                    first_text = True
                    async with node.stream(agent_run.ctx) as stream:
                        async for event in stream:
                            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                                if first_text and (publisher.text_characters or continues_text):
                                    await publisher.text(spaced("\n\n"))
                                first_text = continues_text = False
                                await publisher.text(spaced(without_emoji(event.part.content)))
                            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                                await publisher.text(spaced(without_emoji(event.delta.content_delta)))
                    await publisher.flush()
                elif Agent.is_call_tools_node(node):
                    async with node.stream(agent_run.ctx) as stream:
                        async for event in stream:
                            if isinstance(event, FunctionToolCallEvent):
                                called.append(event.part.tool_name)
                                await publisher.activity(
                                    "tool", tools.label(event.part.tool_name), event.part.tool_call_id,
                                )
            usage = agent_run.usage
            assert agent_run.result is not None
            output = agent_run.result.output
            hold = None
            if isinstance(output, DeferredToolRequests):
                hold = {
                    "calls": {call.tool_call_id: output.metadata[call.tool_call_id]["artifact_id"]
                              for call in output.calls},
                    "messages": ModelMessagesTypeAdapter.dump_python([
                        *resumed, *agent_run.result.new_messages(),
                    ], mode="json"),
                }
    if publisher.first_text_at is not None:
        span.set_attribute("attune.time_to_first_text_ms", round((publisher.first_text_at - started) * 1000))
    return AgentOutcome(hold=hold, usage={
        "model": scope.id,
        "requests": usage.requests, "tool_calls": usage.tool_calls, "tools": called,
        # Kept loaded for the rest of the conversation (`context.loaded_interfaces`).
        "loaded": sorted(deps.loaded_interfaces),
        # For memory, resolving what the user's message referred to.
        **({"looked_up": deps.looked_up} if any(deps.looked_up.values()) else {}),
        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "first_text_ms": round((publisher.first_text_at - started) * 1000) if publisher.first_text_at else None,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "text_characters": publisher.text_characters,
    })


# --- The worker ------------------------------------------------------------------

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
MAX_CONCURRENT_RUNS = 4
# The whole run, context included. Provider calls have their own timeouts inside.
RUN_TIMEOUT_SECONDS = 180
LEASE_RENEW_SECONDS = 10
MAINTENANCE_SECONDS = 5
PRUNE_SECONDS = 3600
# How often a busy worker checks for stop requests, and how long an idle one sleeps
# when no notification arrives.
CANCEL_POLL_SECONDS = 0.25
IDLE_POLL_SECONDS = 2.0


# Runs this worker was asked to stop, as opposed to runs cut short by shutdown.
stopping: set[str] = set()


def _failure_code(error: BaseException) -> str:
    if isinstance(error, UsageLimitExceeded):
        return "budget_exhausted"
    if isinstance(error, TimeoutError):
        return "generation_timed_out"
    if isinstance(error, AssistantUnconfigured):
        return "assistant_unconfigured"
    if isinstance(error, ModelHTTPError) and error.status_code == 429:
        return "model_rate_limited"
    if isinstance(error, (ModelAPIError, httpx.HTTPError)):
        return "model_unavailable"
    return "generation_failed"


async def _keep_lease(run_id: str, owner: asyncio.Task) -> None:
    while True:
        await asyncio.sleep(LEASE_RENEW_SECONDS)
        if not await asyncio.to_thread(runs.renew_lease, run_id, WORKER_ID):
            owner.cancel()
            return


@traced("chat.turn")
async def execute_run(run_id: str) -> None:
    """Drive one claimed run to an outcome."""
    with SessionLocal() as db:
        snapshot = load_snapshot(db, run_id)
    deps = TurnDeps(
        user_id=snapshot.user_id, thread_id=snapshot.thread_id, run_id=run_id, worker_id=WORKER_ID,
        assistant_message_id=snapshot.assistant_message_id, timezone_name=snapshot.timezone_name,
        enabled_interfaces=snapshot.enabled_interfaces,
    )

    async def write(text: str, events: list[BaseModel]) -> None:
        await asyncio.to_thread(runs.publish, run_id, WORKER_ID, snapshot.assistant_message_id, text, events)

    publisher = RunPublisher(write)
    lease = asyncio.create_task(_keep_lease(run_id, asyncio.current_task()))  # type: ignore[arg-type]
    outcome, code, usage, hold = "completed", None, None, None
    meter: dict[str, int] = {}
    started = time.perf_counter()
    try:
        async with asyncio.timeout(RUN_TIMEOUT_SECONDS):
            context = await build_turn_context(snapshot)
            await asyncio.to_thread(runs.record_context_stats, run_id, WORKER_ID, context.stats)
            record_turn_content(session_id=snapshot.thread_id, input=context.prompt)
            result = await run_agent(context, deps, publisher, meter)
            usage, hold = result.usage, result.hold
    except asyncio.CancelledError:
        if run_id in stopping:
            outcome, code = "canceled", "response_stopped"
        else:
            outcome, code = "failed", "generation_worker_lost"
    except RunFencedError:
        # Asked to stop mid-write, or ownership is gone; finishing sorts out which.
        outcome, code = "canceled", "response_stopped"
    except BaseException as error:
        if not isinstance(error, Exception):
            raise
        outcome, code = "failed", _failure_code(error)
        logger.warning("Chat run failed: %s", type(error).__name__)
    finally:
        lease.cancel()
        stopping.discard(run_id)
        pending = await publisher.take_pending()
        if usage is None and meter:
            usage = {**meter, "partial": True}
        held = outcome == "completed" and hold is not None and usage is not None and await asyncio.to_thread(
            runs.hold_run, run_id, WORKER_ID, text=pending, usage=usage, hold=hold,
        )
        if held:
            outcome, settled = "held", True
        else:
            if hold is not None:
                # It could not wait: the user asked it to stop as it paused, or it is no longer ours.
                outcome, code = "canceled", "response_stopped"
            settled = await asyncio.to_thread(
                runs.finish_run, run_id, WORKER_ID, outcome=outcome, code=code, text=pending, usage=usage,
            )
        event("chat.run_finished", user_id=snapshot.user_id, thread_id=snapshot.thread_id, run_id=run_id,
              outcome=outcome if settled else "fenced", code=code,
              duration_ms=round((time.perf_counter() - started) * 1000),
              requests=(usage or {}).get("requests"), tool_calls=(usage or {}).get("tool_calls"),
              first_text_ms=(usage or {}).get("first_text_ms"))


def _cancel_requested(run_ids: list[str]) -> list[str]:
    with SessionLocal() as db:
        return list(db.scalars(select(ConversationRun.id).where(
            ConversationRun.id.in_(run_ids), ConversationRun.cancel_requested_at.is_not(None),
        )))


async def generation_loop() -> None:
    tasks: dict[str, asyncio.Task] = {}
    last_maintenance = last_prune = 0.0
    try:
        with wakeups.subscription(RUN_QUEUED) as queued:
            while True:
                try:
                    now = time.monotonic()
                    if now - last_maintenance >= MAINTENANCE_SECONDS:
                        last_maintenance = now
                        await asyncio.to_thread(runs.recover_expired_runs)
                    if now - last_prune >= PRUNE_SECONDS:
                        last_prune = now
                        await asyncio.to_thread(runs.prune_events)
                    for run_id, task in list(tasks.items()):
                        if task.done():
                            del tasks[run_id]
                            if not task.cancelled() and task.exception():
                                logger.warning("Chat run task crashed: %s", type(task.exception()).__name__)
                    if tasks:
                        for run_id in await asyncio.to_thread(_cancel_requested, list(tasks)):
                            if run_id not in stopping:
                                stopping.add(run_id)
                                tasks[run_id].cancel()
                    while len(tasks) < MAX_CONCURRENT_RUNS:
                        run_id = await asyncio.to_thread(runs.claim_next_run, WORKER_ID)
                        if run_id is None:
                            break
                        tasks[run_id] = asyncio.create_task(execute_run(run_id))
                except Exception:
                    logger.warning("Chat worker iteration failed", exc_info=False)
                await wait_for_wakeup(queued, CANCEL_POLL_SECONDS if tasks else IDLE_POLL_SECONDS)
    finally:
        for task in tasks.values():
            task.cancel()
        # Each canceled run settles itself as interrupted before shutdown finishes.
        await asyncio.gather(*tasks.values(), return_exceptions=True)
