"""Which model does which job, how to reach it, and everything else configurable.

A scope names one kind of model work and the model that serves it; the two
builders at the bottom turn a scope into a client. Their imports are deferred
because migrations and every module that defines tables import this file and
need none of it.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pydantic_ai.settings import ModelSettings


# The dev overlay publishes the stack's Postgres on the loopback default port, so a
# process started outside Compose reaches the same database the containers use.
DEFAULT_DATABASE_URL = "postgresql://attune:attune-development@localhost:5432/attune"

# Every model call goes through OpenRouter, so no one provider's model lineup
# reaches this code. The key is the only part of this that is a secret, so it is
# the only part that comes from the environment; which model does which job is a
# decision this file makes, the same way VOYAGE_MODEL is decided in memory/graph.py.
#
# The cost is that a retired model id needs a code change rather than an env edit.
# That is the intended trade: the previous default was "openrouter/auto", which
# moved the decision to a dashboard, and the result was invisible — the router
# served two different models to the same workload, prompt caching never held
# because a cached prefix cannot survive a provider reassignment, and the odd turn
# ran several times longer than its neighbours for no reason the traces could
# explain. A name that rots loudly beats a router that varies quietly.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Reasoning effort, as OpenRouter's top-level `reasoning` field spells it. "off"
# disables reasoning outright; an effort level caps it. None sends nothing, which
# leaves whatever the chosen model does by default.
REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high"})

# How long a background pass waits for one response. The OpenAI client restarts a
# request that times out, so a short limit does not cut the wait short — it pays
# for the same answer several times. One 148-second overview was a 28-second
# answer behind two abandoned 60-second attempts.
BACKGROUND_REQUEST_TIMEOUT = 180


@dataclass(frozen=True)
class ModelScope:
    """One kind of model work: which model serves it, and how far it may reason.

    The scopes exist because these calls want different things. Chat calls
    tools and writes the reply in one run; the background passes compress text or
    write facts against a schema. Decisions, which pick among known options rather
    than write, go to a different kind of model altogether (`DECISION_MODEL`). One
    setting for all of them can only be right for one of them, so each is named
    and set separately below.
    """

    id: str
    reasoning: str | None = None

    def model_settings(self, **overrides: object) -> "ModelSettings":
        """Per-request settings for this scope, with caller overrides applied."""
        settings: dict[str, object] = dict(overrides)
        if self.reasoning is None:
            return cast("ModelSettings", settings)
        if self.reasoning == "off":
            reasoning: dict[str, object] = {"enabled": False}
        elif self.reasoning in REASONING_EFFORTS:
            reasoning = {"effort": self.reasoning}
        else:
            # An unreadable value must not silently become a different budget.
            raise ValueError(f"Unknown reasoning setting: {self.reasoning!r}")
        body: dict[str, object] = dict(cast(dict[str, object], settings.get("extra_body") or {}))
        body["reasoning"] = reasoning
        settings["extra_body"] = body
        return cast("ModelSettings", settings)


# The model a scope uses unless it names another. Named once so that changing
# them all at once stays one edit, and so a scope that deliberately differs is
# visible as a scope that does not mention it.
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"

# Chat: the one agent that reads the turn, calls tools, and writes the reply. The
# user waits on it, so reasoning stays off: it would cost the user latency.
CHAT_MODEL = ModelScope(id=DEFAULT_MODEL, reasoning="off")
# OpenRouter deprioritizes endpoints below this threshold; it is not a hard
# exclusion. Prefer throughput over price, retaining slower outage fallbacks.
CHAT_PROVIDER_ROUTING = {"preferred_min_throughput": 50, "sort": "throughput"}
# Background passes off the chat path: thread summaries, and writing what memory
# learns.
# Its reasoning has been measured rather than assumed: hundreds of reasoning
# tokens to produce a few dozen tokens of structured output, and responses slow
# enough to trip their own timeout. Nothing here works anything out.
BACKGROUND_MODEL = ModelScope(id=DEFAULT_MODEL, reasoning="off")
# Decisions: choices among known options, never text. Jev, TypeSafe's "System One"
# model, answers many at once through OpenRouter's decisions endpoint in a fraction
# of a second, with calibrated probabilities; memory's triage, verification and
# filing ask it. The decisions endpoint is not the chat one.
DECISION_MODEL = "typesafe/jev-1.13"
DECISIONS_URL = "https://openrouter.ai/api/v1/systemone"



@dataclass(frozen=True)
class Price:
    """What a model costs, in dollars per million tokens, as OpenRouter lists it."""

    input: float
    cached_input: float
    output: float

    def cost(self, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        """Dollars for one call. `input_tokens` includes the cached ones."""
        return ((input_tokens - cached_tokens) * self.input + cached_tokens * self.cached_input
                + output_tokens * self.output) / 1_000_000


# Prices for the text models, read from OpenRouter on 2026-10-07. Traces show each
# call's cost from these, and the daily budget charges by them. A model missing
# here fails at import for chat, and shows no cost in traces for anything else.
# Jev is absent on purpose: its endpoint reports the real cost of each call.
MODEL_PRICES = {
    DEFAULT_MODEL: Price(input=0.30, cached_input=0.006, output=1.20),
}
CHAT_PRICE = MODEL_PRICES[CHAT_MODEL.id]

# What chat may cost in a rolling day from one address, across every account used
# from it, so opening more demo accounts buys nothing. Memory's work follows chat
# and has measured at about a sixth of its cost. The spending limit on the
# OpenRouter key is the backstop across all addresses.
DAILY_CHAT_DOLLARS_PER_ADDRESS = 0.12
# What a run is charged when it never reported its full usage: stopped, failed or
# still going. About one ordinary turn's input, so stopping every reply saves nothing.
UNMEASURED_RUN_DOLLARS = CHAT_PRICE.cost(10_000, 0, 0)
# A review of memory is charged to the same allowance, since a visitor can ask for
# one. This is what it counts for until it reports its cost: about one look over
# a middling graph.
UNMEASURED_REVIEW_DOLLARS = MODEL_PRICES[BACKGROUND_MODEL.id].cost(8_000, 0, 1_500)


def load_backend_env() -> None:
    """Load the repository's .env without overriding the process environment.

    Compose passes everything a container needs directly, so this only matters
    to something run by hand — a test, a migration, a worker from a shell.
    """
    root = Path(__file__).resolve().parents[2]
    for key, value in _env_values(root / ".env").items():
        os.environ.setdefault(key, value)


def _env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    database_url: str
    openrouter_api_key: str | None
    voyage_api_key: str | None


def _normalize_database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def get_settings() -> Settings:
    """Return typed settings from the current environment.

    The result is intentionally not cached so local tooling can safely override
    environment variables before importing or invoking application code.
    """
    load_backend_env()
    return Settings(
        database_url=_normalize_database_url(
            os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
        ),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
        voyage_api_key=os.environ.get("VOYAGE_API_KEY") or None,
    )


# --- Model clients ---------------------------------------------------------------

class ModelUnconfigured(RuntimeError):
    """No OpenRouter key: nothing that needs a model can run."""


def openrouter_model(scope: "ModelScope", http_client, *, max_retries: int = 1):
    """A chat model for one caller's HTTP client, pointed at OpenRouter."""
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    settings = get_settings()
    if not settings.openrouter_api_key:
        raise ModelUnconfigured("OPENROUTER_API_KEY is not configured.")
    return OpenAIChatModel(scope.id, provider=OpenAIProvider(openai_client=AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL, api_key=settings.openrouter_api_key,
        http_client=http_client, max_retries=max_retries,
    )))


@asynccontextmanager
async def background_model(scope: "ModelScope"):
    """A model for one background request.

    No retries: the client restarts a timed-out request from scratch and is billed
    for it again, which is how one slow answer became three.
    """
    import httpx

    async with httpx.AsyncClient(timeout=BACKGROUND_REQUEST_TIMEOUT) as client:
        yield openrouter_model(scope, client, max_retries=0)
