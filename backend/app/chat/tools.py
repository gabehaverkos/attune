"""Everything the agent can call, and what every tool is built on.

The toolbox comes first: what a tool receives, which tools a turn gets, and how
tools write. Then drafting, the one `propose_<type>` tool each artifact type gets.
Then the tools every turn has: long-term memory, earlier conversations, and
opening an interface or proposing to turn one on or off. The tools an interface
adds are in its own package (`app/interfaces/<key>/chat.py`).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pydantic import BaseModel
from pydantic_ai import Agent, CallDeferred, ModelRetry, RunContext
from pydantic_ai.tools import ToolDefinition, ToolPrepareFunc
from sqlalchemy import DateTime, ForeignKey, func, JSON, select, String
from sqlalchemy.orm import Mapped, mapped_column, Session
from types import ModuleType
from typing import Any, Literal, TYPE_CHECKING, TypedDict

from app.chat.runs import ArtifactCommitted
from app.chat.artifacts import (
    apply_edit, artifact_to_response, ArtifactConflictError, ArtifactDefinition, ArtifactDefinitionError,
    ArtifactInterfaceDisabledError, ArtifactUncertainty, ArtifactValidationError, check_blocker,
    ConversationArtifact, ConversationMessageArtifactEvent, create_artifact, idle_expired,
    supersede_artifact, ValidatedArtifactCommand,
)
from app.chat.threads import ConversationMessage, ConversationThread, ordered_thread_messages
from app.core.database import Base, SessionLocal
from app.core.observability import event
from app.core.time import user_timezone, utc_now
from app.interfaces.descriptor import InterfaceDescriptor
from app.memory import public as memory


# === Toolbox =======================================================================
#
# What chat tools receive, which ones a turn gets, and how they write.
#
# `register_tools` gives a turn its tools, each with a fixed activity label, which
# is all a viewer is ever told about a call. An interface's tools are registered
# but hidden until the agent opens the interface (see Interfaces below). Tools that write go through `committed_write`, which
# proves the run is still owned, saves the result under the tool call's ID, and
# commits in one short transaction of its own. An interface's tools recheck that
# it is still enabled with `require_enabled`, since a turn can outlive a toggle.

# --- Tables ----------------------------------------------------------------------

class ConversationToolReceipt(Base):
    """The saved result of a tool call that wrote something.

    Keyed by run and tool call, so a repeated delivery of the same call returns
    what the first one did instead of writing twice.
    """

    __tablename__ = "conversation_tool_receipts"
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation_runs.id", ondelete="CASCADE"), primary_key=True
    )
    tool_call_id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.chat.runs import ConversationRun


@dataclass
class TurnDeps:
    user_id: str
    thread_id: str
    run_id: str
    worker_id: str
    assistant_message_id: str
    timezone_name: str = "UTC"
    enabled_interfaces: frozenset[str] = frozenset()
    # Interfaces whose tools the agent can see: those loaded earlier in the
    # conversation, and any it loads during this turn.
    loaded_interfaces: set[str] = field(default_factory=set)
    # What the turn looked up to answer: beliefs it found, and stretches of earlier
    # conversations it read. Memory reads it to resolve what a message refers to.
    looked_up: dict[str, list] = field(default_factory=lambda: {"beliefs": [], "passages": []})
    # Reads may run side by side; writes for one run go one at a time. Tools run
    # in worker threads, so this is a thread lock, and no session is shared.
    write_lock: threading.Lock = field(default_factory=threading.Lock)


class ChatToolbox:
    """Registers tools on one agent and remembers each one's activity label.

    While `interface` is set, each tool registered belongs to that interface and
    is shown to the model only once the interface is loaded.
    """

    def __init__(self, agent: Agent[TurnDeps, Any]):
        self.agent = agent
        self.labels: dict[str, str] = {}
        self.interface: str | None = None

    def tool(self, function: Callable[..., Any], *, label: str, name: str | None = None,
             timeout: float | None = None, opens: str | None = None) -> None:
        """`opens` marks the tool that opens an interface, shown only until it is open."""
        tool_name = name or function.__name__
        if not label.strip():
            raise ValueError(f"Chat tool {tool_name} needs an activity label.")
        self.labels[tool_name] = label
        prepare: ToolPrepareFunc[TurnDeps] | None = None
        if (interface := self.interface) is not None:
            async def once_open(ctx: RunContext[TurnDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
                return tool_def if interface in ctx.deps.loaded_interfaces else None
            prepare = once_open
        elif opens is not None:
            async def until_open(ctx: RunContext[TurnDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
                return None if opens in ctx.deps.loaded_interfaces else tool_def
            prepare = until_open
        self.agent.tool(name=tool_name, timeout=timeout, prepare=prepare)(_without_stray_arguments(function))

    def label(self, tool_name: str) -> str:
        return self.labels.get(tool_name, "Working…")


def _without_stray_arguments(function: Callable[..., Any]) -> Callable[..., Any]:
    """A tool that takes no arguments, made to ignore any it is sent.

    The chat model never sends an empty object to such a tool: it makes up an
    argument (`{"no": ""}`, `{"action": "open"}`), which would fail validation
    again on every retry and end the turn.
    """
    if len(inspect.signature(function).parameters) != 1:
        return function

    async def ignoring_async(ctx: RunContext[TurnDeps], **_: Any) -> Any:
        return await function(ctx)

    def ignoring(ctx: RunContext[TurnDeps], **_: Any) -> Any:
        return function(ctx)

    wrapper = ignoring_async if inspect.iscoroutinefunction(function) else ignoring
    wrapper.__name__, wrapper.__doc__ = function.__name__, function.__doc__
    return wrapper


def interface_module(key: str) -> ModuleType | None:
    """What an interface adds to chat, named by the interface's key.

    `app/interfaces/<key>/chat.py` holds its artifact types (`ARTIFACTS`) and its
    tools (`register`); an interface without one adds nothing.
    """
    name = f"app.interfaces.{key}.chat"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        return None


def interfaces_with_tools(enabled_interfaces: Iterable[str]) -> list[str]:
    """The enabled interfaces that have tools, in catalog order."""
    from app.interfaces.registry import INTERFACE_CATALOG

    enabled = set(enabled_interfaces)
    return [descriptor.key for descriptor in INTERFACE_CATALOG if descriptor.key in enabled
            and interface_module(descriptor.key) is not None]


def register_tools(tools: ChatToolbox, enabled_interfaces: frozenset[str]) -> None:
    """Every tool this turn may use, in a fixed order: the system's, then each enabled
    interface's, hidden until it is loaded. The order never depends on what is loaded."""
    from app.interfaces.registry import INTERFACE_CATALOG

    tools.tool(search_memory, label="Remembering…", timeout=SEARCH_TIMEOUT_SECONDS)
    tools.tool(inspect_memory_evidence, label="Checking where that came from…")
    tools.tool(list_conversations, label="Looking through earlier sessions…")
    tools.tool(read_conversation, label="Rereading the conversation…")
    tools.tool(correct_memory, label="Correcting what I know…")
    tools.tool(forget_memory, label="Forgetting that…")
    register_wipe(tools)
    register_toggle(tools)
    tools.tool(withdraw_proposal, label="Taking that back…")

    keys = interfaces_with_tools(enabled_interfaces)
    for descriptor in INTERFACE_CATALOG:
        if descriptor.key in keys:
            tools.tool(opener(descriptor), label=f"Opening your {descriptor.name.lower()}…", opens=descriptor.key)
    for key in keys:
        module = interface_module(key)
        assert module is not None
        tools.interface = key
        module.register(tools)
        tools.interface = None


class ToolConflict(Exception):
    """A write that could not apply; the result says why, for the agent to handle."""

    def __init__(self, result: dict[str, Any]):
        super().__init__(result.get("reason", "conflict"))
        self.result = result


def committed_write(
    deps: TurnDeps, tool_call_id: str | None, tool_name: str,
    write: Callable[[Session, ConversationRun], dict[str, Any]],
) -> dict[str, Any]:
    from app.chat.runs import fence
    from app.core.database import SessionLocal

    call_id = tool_call_id or ""
    with deps.write_lock, SessionLocal() as db:
        run = fence(db, deps.run_id, deps.worker_id)
        if call_id and (receipt := db.get(ConversationToolReceipt, (deps.run_id, call_id))):
            result = dict(receipt.result)
            db.rollback()
            return result
        try:
            result = write(db, run)
        except ToolConflict as conflict:
            db.rollback()
            return conflict.result
        if call_id:
            db.add(ConversationToolReceipt(
                run_id=deps.run_id, tool_call_id=call_id, tool_name=tool_name, result=result,
            ))
        db.commit()
        return result


def require_enabled(user_id: str, interface_key: str) -> None:
    from app.core.database import SessionLocal
    from app.interfaces.registry import interface_enabled

    # Fresh session: a setting changed after the turn began must still apply.
    with SessionLocal() as db:
        if not interface_enabled(user_id, interface_key, db):
            raise ModelRetry("This interface is no longer enabled. Do not use its tools.")


# === Drafting ======================================================================
#
# The agent's drafting tools: one `propose_<type>` tool per artifact type.
#
# Each saves a draft, an update to one, or its replacement through
# `chat/artifacts.py`, and puts the card in the reply.

if TYPE_CHECKING:
    from app.chat.runs import ConversationRun


# What a drafting tool returns to the agent. A plain dict on the wire; typed here
# so each outcome always has the same shape.
class ArtifactToolResult(TypedDict, total=False):
    status: Literal["committed", "conflict"]
    artifact_id: str
    revision: int
    state: Literal["draft", "ready"]
    missing_fields: list[str]
    uncertain_fields: list[str]
    note: str
    reason: str
    current_revision: int | None


@dataclass
class ArtifactRequest:
    """One drafting call. Without `artifact_id` it creates; with one, `_target`
    decides whether it updates that proposal or replaces it."""

    definition: ArtifactDefinition
    payload: dict[str, Any]
    uncertain_fields: list[ArtifactUncertainty] = field(default_factory=list)
    title: str = ""
    description: str = ""
    artifact_id: str | None = None
    expected_revision: int | None = None


def _conflict(reason: str, artifact: ConversationArtifact) -> ToolConflict:
    return ToolConflict(dict(ArtifactToolResult(
        status="conflict", reason=reason, artifact_id=artifact.id,
        current_revision=artifact.revision,
    )) | {"current_status": artifact.status})


Action = Literal["create", "update", "replace"]


def _target(db: Session, deps: TurnDeps, request: ArtifactRequest) -> tuple[ConversationArtifact | None, Action]:
    """The proposal a call changes, and how: an editable one of the same type is
    updated in place; a failed or expired one, or one of another type, is replaced."""
    if request.artifact_id is None:
        return None, "create"
    artifact = db.query(ConversationArtifact).filter(
        ConversationArtifact.id == request.artifact_id,
        ConversationArtifact.user_id == deps.user_id,
        ConversationArtifact.thread_id == deps.thread_id,
    ).with_for_update().one_or_none()
    if artifact is None:
        raise ModelRetry("That proposal is not in this conversation. Use an ID from the proposal context.")
    if artifact.status not in {"draft", "ready", "failed", "expired"}:
        raise _conflict(f"The proposal is {artifact.status} and can no longer be changed. "
                        "Leave out artifact_id to propose it again.", artifact)
    if request.expected_revision is None:
        raise ModelRetry("Pass expected_revision, the proposal's current revision, with its artifact_id.")
    if artifact.revision != request.expected_revision:
        raise _conflict(f"The proposal is now at revision {artifact.revision}; the user may have edited it.", artifact)
    definition = request.definition
    same_type = (artifact.type, artifact.schema_version) == (definition.type, definition.schema_version)
    editable = artifact.status in {"draft", "ready"} and not idle_expired(artifact)
    return artifact, "update" if same_type and editable else "replace"


def commit_artifact(ctx: RunContext[TurnDeps], request: ArtifactRequest) -> dict[str, Any]:
    """Save a draft, an update to one, or its replacement, as the agent's tool call."""
    from app.chat.threads import ConversationMessage
    from app.chat.runs import append_event

    deps = ctx.deps
    definition = request.definition

    def write(db: Session, run: ConversationRun) -> dict[str, Any]:
        target, action = _target(db, deps, request)
        payload = request.payload
        if action == "create" and definition.prepare_existing:
            payload = definition.prepare_existing(db, deps.user_id, payload, deps.timezone_name)
        try:
            definition.prepare_draft(payload)
        except ArtifactDefinitionError as error:
            raise ModelRetry(str(error)) from error
        title = (target.title if action == "update" and target else request.title).strip()
        description = target.description if action == "update" and target else request.description
        uncertain: list[ArtifactUncertainty | dict[str, str]] = list(request.uncertain_fields)

        def create() -> ConversationArtifact:
            return create_artifact(
                db, user_id=deps.user_id, thread_id=deps.thread_id, artifact_type=definition.type,
                schema_version=definition.schema_version, title=title, description=description,
                draft_payload=payload, uncertain_fields=uncertain,
                source_message_id=deps.assistant_message_id,
            )

        events: list[tuple[ConversationArtifact, Literal["created", "updated", "superseded"]]]
        try:
            if action == "create":
                saved = create()
                events = [(saved, "created")]
            elif action == "update":
                assert target is not None and request.expected_revision is not None
                saved = apply_edit(
                    db, user_id=deps.user_id, artifact_id=target.id,
                    expected_revision=request.expected_revision, draft_payload=payload,
                    uncertain_fields=uncertain, changed_by="agent",
                    source_message_id=deps.assistant_message_id,
                )
                events = [(saved, "updated")]
            else:
                assert target is not None
                saved = create()
                superseded = supersede_artifact(
                    db, user_id=deps.user_id, artifact_id=target.id, replacement_artifact_id=saved.id,
                )
                events = [(superseded, "superseded"), (saved, "created")]
        except ArtifactValidationError as error:
            missing = f" Missing fields: {', '.join(error.missing_fields)}" if error.missing_fields else ""
            raise ModelRetry(f"{error}{missing}") from error
        except ArtifactInterfaceDisabledError as error:
            raise ModelRetry(f"{error} Do not use this tool.") from error
        except ArtifactConflictError as error:
            if target is None:
                raise ModelRetry(str(error)) from error
            raise _conflict(str(error), target) from error
        blocked = check_blocker(db, saved, deps.timezone_name)
        db.flush()
        # Everything the reply has said so far is written before a tool runs, so the
        # card goes after it.
        reply = db.get(ConversationMessage, deps.assistant_message_id)
        text_offset = len(reply.content) if reply else None
        for artifact, committed in events:
            snapshot = artifact_to_response(artifact).model_dump(mode="json")
            db.add(ConversationMessageArtifactEvent(
                id=str(uuid.uuid7()), message_id=deps.assistant_message_id,
                artifact_id=artifact.id, event=committed, snapshot=snapshot, text_offset=text_offset,
            ))
            append_event(db, run, ArtifactCommitted(
                message_id=deps.assistant_message_id, action=committed, artifact=snapshot,
                text_offset=text_offset,
            ))
        note = ("Saved and shown to the user as a card with every detail on it; it changes nothing until they "
                "approve it there. Reply in one short sentence at most.")
        if action == "create" and definition.draft_note and (extra := definition.draft_note(
            db, deps.user_id, ValidatedArtifactCommand(
                kind="create", title=title, description=description, draft_payload=saved.draft_payload,
                uncertain_fields=saved.uncertain_fields, artifact_type=definition.type,
            ),
        )):
            note += " " + extra
        if blocked:
            note = (f"Saved, but it cannot be approved as things stand: {blocked} The card says so. "
                    "Say what blocks it and offer the ways to clear it (such as moving or removing what "
                    "it overlaps, or changing this one), then propose the one the user picks. Once that "
                    "is approved, this card comes back ready by itself; never propose it again.")
        return dict(ArtifactToolResult(
            status="committed", artifact_id=saved.id, revision=saved.revision,
            state="ready" if saved.status == "ready" else "draft",
            missing_fields=list(saved.missing_fields),
            uncertain_fields=[item["path"] for item in saved.uncertain_fields],
            note=note,
        ))

    return committed_write(deps, ctx.tool_call_id, ctx.tool_name or definition.type, write)


def withdraw_proposal(ctx: RunContext[TurnDeps], artifact_id: str) -> dict:
    """Take back a proposal in this conversation that is still waiting on its card,
    when the user no longer wants it. The card closes and nothing changes. One that
    was already approved is real, and is changed or removed with a new proposal."""
    from app.chat.threads import ConversationMessage
    from app.chat.runs import append_event

    deps = ctx.deps

    def write(db: Session, run: ConversationRun) -> dict[str, Any]:
        artifact = db.query(ConversationArtifact).filter(
            ConversationArtifact.id == artifact_id, ConversationArtifact.user_id == deps.user_id,
            ConversationArtifact.thread_id == deps.thread_id,
        ).with_for_update().one_or_none()
        if artifact is None:
            raise ModelRetry("That proposal is not in this conversation. Use an ID from the proposal context.")
        if artifact.status not in {"draft", "ready"}:
            raise ToolConflict({"status": "conflict", "artifact_id": artifact.id, "current_status": artifact.status,
                                "reason": f"The proposal is {artifact.status}, not waiting, so there is nothing "
                                          "to take back. Say how it stands."})
        artifact.status, artifact.updated_by, artifact.resolved_at = "denied", "agent", utc_now()
        artifact.result_payload = {"denial_reason": "Taken back in chat at the user's request."}
        db.flush()
        # The card shows where it now stands, after what the reply has said so far.
        reply = db.get(ConversationMessage, deps.assistant_message_id)
        text_offset = len(reply.content) if reply else None
        snapshot = artifact_to_response(artifact).model_dump(mode="json")
        db.add(ConversationMessageArtifactEvent(
            id=str(uuid.uuid7()), message_id=deps.assistant_message_id,
            artifact_id=artifact.id, event="updated", snapshot=snapshot, text_offset=text_offset,
        ))
        append_event(db, run, ArtifactCommitted(
            message_id=deps.assistant_message_id, action="updated", artifact=snapshot, text_offset=text_offset,
        ))
        return {"status": "withdrawn", "artifact_id": artifact.id,
                "note": "Its card is closed and nothing changed. Say so in one sentence."}

    return committed_write(deps, ctx.tool_call_id, "withdraw_proposal", write)


def register_drafting(tools: ChatToolbox, definition: ArtifactDefinition, guidance: str = "",
                      label: str | None = None,
                      waits_for_decision: Callable[[dict[str, Any]], bool] | None = None) -> None:
    """A type's one `propose_<type>` tool, typed by its draft payload; `guidance` is
    what its description adds where the payload's own field descriptions aren't enough.

    A ready card whose payload `waits_for_decision` holds pauses the reply: the
    tool's result is deferred to the user's decision on it (`chat/worker.py`)."""
    noun = definition.type.replace("_", " ")  # "sleep routine"
    modifier = definition.type.replace("_", "-")  # "a sleep-routine proposal"

    def propose(ctx: RunContext[TurnDeps], title: str, description: str, draft_payload: BaseModel,
                uncertain_fields: list[ArtifactUncertainty], artifact_id: str | None = None,
                expected_revision: int | None = None) -> dict:
        payload = draft_payload.model_dump(mode="json")
        result = commit_artifact(ctx, ArtifactRequest(
            definition=definition, payload=payload,
            uncertain_fields=uncertain_fields, title=title, description=description,
            artifact_id=artifact_id, expected_revision=expected_revision,
        ))
        if waits_for_decision and result.get("state") == "ready" and waits_for_decision(payload):
            raise CallDeferred(metadata={"artifact_id": result["artifact_id"]})
        return result

    # The concrete annotation is assigned before registration so the tool exposes
    # its own Pydantic schema, not BaseModel (or an unresolved closure annotation).
    propose.__name__ = f"propose_{definition.type}"
    propose.__doc__ = (f"Save a complete or incomplete {modifier} proposal for the user to review, or "
                       "change one in this conversation by passing its artifact_id and revision with the "
                       "whole new payload." + guidance)
    propose.__annotations__["draft_payload"] = definition.draft_schema
    tools.tool(propose, label=label or f"Drafting a {noun}…")


# === Memory ========================================================================
#
# Long-term memory: what the user has told Attune about themselves, across conversations.
#
# Personal facts are learned in the background from what the user said, with no
# tool involved. These tools search what is known and show the words a fact rests
# on, and correct or forget a fact when the user asks for that. Those two act at
# once, with no card to approve: memory is what Attune and the user keep together,
# and the user asking is the approval. Forgetting everything is the one change that
# is proposed on a card instead (`chat/artifacts.py`). Memory itself
# is reached only through `app.memory.public`.

# How many recalled facts a session carries forward. Enough to hold the topic the
# conversation is on, small enough that moving on drops what it has left behind.
RECALLED_FACT_LIMIT = 12
SEARCH_TIMEOUT_SECONDS = 15


def _carry_recalled_facts(thread_id: str, fact_ids: list[str]) -> None:
    """Keep what this search found, so the turns after it still have the facts.

    Written on the thread rather than held in the agent, because each turn builds a
    new agent: without this, the same search runs again next turn or the detail is
    simply lost.
    """
    if not fact_ids:
        return
    with SessionLocal() as db:
        thread = db.query(ConversationThread).filter(
            ConversationThread.id == thread_id,
        ).with_for_update().one_or_none()
        if thread is None:
            return
        # A fact recalled again moves to the end rather than sitting where it was.
        kept = [known for known in (thread.recalled_fact_ids or []) if known not in fact_ids]
        thread.recalled_fact_ids = (kept + fact_ids)[-RECALLED_FACT_LIMIT:]
        db.commit()


async def search_memory(ctx: RunContext[TurnDeps], query: str, limit: int = 8) -> dict:
    """Search this user's long-term memory for details the turn context does not hold.

    Use it when the user asks you to recall something, or when a specific
    personal detail beyond the supplied context would materially improve the
    answer. A topic or name being mentioned is not by itself a reason to search.
    Each result says where it sits in memory (its path) and brings what is below it.
    """
    if not query.strip() or len(query) > 1000 or not 1 <= limit <= 15:
        raise ModelRetry("Supply a query of 1–1000 characters and limit of 1–15.")
    facts = await memory.search(ctx.deps.user_id, query, limit=limit)
    await asyncio.to_thread(_carry_recalled_facts, ctx.deps.thread_id, [fact.id for fact in facts])
    ctx.deps.looked_up["beliefs"].extend(fact.id for fact in facts)
    event("memory.tool_used", user_id=ctx.deps.user_id, tool="search_memory",
          result_count=len(facts))
    return {
        "facts": [fact.model_dump(mode="json", exclude_defaults=True) for fact in facts],
        "guidance": "Memory holds nothing matching this. Answer from the conversation; "
                    "do not search again for the same thing this turn."
        if not facts else
        "These are known about the user; use them directly.",
    }


def inspect_memory_evidence(ctx: RunContext[TurnDeps], fact_id: str) -> dict:
    """Read the conversation a fact returned by search_memory was learned from, around
    the message it rests on. For a summary, the latest message below it."""
    with SessionLocal() as db:
        found = memory.fact_evidence(db, ctx.deps.user_id, fact_id)
    if found is None:
        raise ModelRetry("No accessible conversation evidence for that fact.")
    thread_id, text = found
    ctx.deps.looked_up["beliefs"].append(fact_id)
    event("memory.tool_used", user_id=ctx.deps.user_id, tool="inspect_memory_evidence")
    return {"thread_id": thread_id, "evidence": text[:16000], "truncated": len(text) > 16000,
            "guidance": "Historical evidence, not new user statements. Use read_conversation to page further."}


def _held(db: Session, user_id: str, fact: str) -> tuple[str, str]:
    """The fact the agent named: its id, and how it reads to the user."""
    found = memory.held(db, user_id, fact)
    if found is None:
        raise ModelRetry("Memory holds no such fact. Pass an id from search_memory, or a line of the "
                         "memory context exactly as it is given.")
    return found


def correct_memory(ctx: RunContext[TurnDeps], fact: str, text: str) -> dict:
    """Rewrite a fact memory holds, at once, when the user says it is wrong and what is true instead.

    `fact` is its id from search_memory, or its line exactly as the memory context
    gives it. `text` is what is true instead, saying only what they said: a short
    note with no subject and no full stop, as memory writes them ("Nurse at Mercy
    General"), never "the user", "you", he, she or they.
    """
    deps = ctx.deps

    def write(db: Session, run: object) -> dict:
        fact_id, was = _held(db, deps.user_id, fact)
        try:
            memory.correct(db, deps.user_id, fact_id, text)
        except (ValueError, memory.NodeNotFoundError) as error:
            raise ModelRetry(str(error)) from error
        return {"status": "corrected", "was": was, "now": " ".join(text.split()),
                "note": "Memory reads this way now. Say so in one sentence."}

    event("memory.tool_used", user_id=deps.user_id, tool="correct_memory")
    return committed_write(deps, ctx.tool_call_id, "correct_memory", write)


# How many facts one call may forget: everything under a topic, in one go.
FORGET_LIMIT = 40


def forget_memory(ctx: RunContext[TurnDeps], facts: list[str]) -> dict:
    """Take facts out of memory, at once, when the user asks for them to be forgotten.

    Each of `facts` is a fact's id from search_memory, or its line exactly as the
    memory context gives it; pass every fact the request clearly covers in one
    call. What was below a forgotten fact stays, so name those too if they go.
    """
    deps = ctx.deps
    if not facts or len(facts) > FORGET_LIMIT:
        raise ModelRetry(f"Pass 1–{FORGET_LIMIT} facts.")

    def write(db: Session, run: object) -> dict:
        named: dict[str, str] = {}
        missing: list[str] = []
        for fact in dict.fromkeys(facts):
            found = memory.held(db, deps.user_id, fact)
            if found is None:
                missing.append(fact)
            else:
                named[found[0]] = found[1]
        forgotten = [named[fact_id] for fact_id in memory.forget(db, deps.user_id, list(named))]
        if not forgotten:
            raise ModelRetry("Memory holds none of those. Pass ids from search_memory, or lines of the "
                             "memory context exactly as they are given.")
        return {"status": "forgotten", "was": "; ".join(forgotten), "forgotten": forgotten,
                **({"not_found": missing} if missing else {}),
                "note": "Memory no longer holds these. For a request about a whole topic, search memory "
                        "again to check nothing under it is left before you reply."}

    event("memory.tool_used", user_id=deps.user_id, tool="forget_memory", result_count=len(facts))
    return committed_write(deps, ctx.tool_call_id, "forget_memory", write)


def register_wipe(tools: ChatToolbox) -> None:
    from app.chat.artifacts import MEMORY_WIPE

    register_drafting(tools, MEMORY_WIPE, label="Checking with you first…", guidance=(
        " For when the user asks for everything memory holds about them to be forgotten. Pass an empty"
        " draft_payload. It waits for the user's decision on the card, which comes back as the result;"
        " nothing is forgotten unless they approve."
    ), waits_for_decision=lambda payload: True)


# === Earlier conversations =========================================================
#
# Earlier conversations: which there are, and each read back in bounded pages.
#
# Forgetting a fact takes it out of memory, not out of the conversation it was said
# in, so what the user said is always here to be read again.

# How much of a session's first message says what it was about.
OPENING_CHARACTERS = 160


def list_conversations(ctx: RunContext[TurnDeps], limit: int = 20) -> dict:
    """List this user's conversations, oldest first, each with its thread_id, when it
    began, and how the user opened it. Use it to find a session the user refers to
    ("our first chat", "last week") before read_conversation."""
    if not 1 <= limit <= 50:
        raise ModelRetry("limit must be 1–50.")
    with SessionLocal() as db:
        threads = db.query(ConversationThread).filter(
            ConversationThread.user_id == ctx.deps.user_id,
        ).order_by(ConversationThread.created_at, ConversationThread.id).all()
        # Only what the list shows is read: each conversation's count and its first
        # message from the user, never the conversation itself.
        mine = [ConversationMessage.thread_id.in_([thread.id for thread in threads]),
                ConversationMessage.author == "user"]
        counts = dict(db.execute(
            select(ConversationMessage.thread_id, func.count()).where(*mine)
            .group_by(ConversationMessage.thread_id)).tuples().all())
        openings = dict(db.execute(
            select(ConversationMessage.thread_id, ConversationMessage.content).where(*mine)
            .distinct(ConversationMessage.thread_id)
            .order_by(ConversationMessage.thread_id, ConversationMessage.timestamp, ConversationMessage.id)
        ).tuples().all())
        sessions = []
        for thread in threads:
            if thread.id not in counts:
                continue
            opening = " ".join(openings[thread.id].split())
            sessions.append({
                "thread_id": thread.id,
                "began": thread.created_at.astimezone(user_timezone(ctx.deps.timezone_name)).strftime("%A, %B %d, %Y at %I:%M %p"),
                "user_messages": counts[thread.id], "opening": opening[:OPENING_CHARACTERS],
                **({"current": True} if thread.id == ctx.deps.thread_id else {}),
            })
    event("memory.tool_used", user_id=ctx.deps.user_id, tool="list_conversations", result_count=len(sessions))
    # The first and the latest are what a user refers to; the middle goes if there are too many.
    shown = sessions if len(sessions) <= limit else sessions[:limit // 2] + sessions[-(limit - limit // 2):]
    return {"sessions": shown, "total": len(sessions)}


def read_conversation(ctx: RunContext[TurnDeps], thread_id: str, offset: int = 0,
                      limit: int = 12000) -> dict:
    """Read a bounded page of an earlier conversation, including parts the context omitted."""
    if offset < 0 or not 1 <= limit <= 16000:
        raise ModelRetry("offset must be nonnegative; limit must be 1–16000 characters.")
    with SessionLocal() as db:
        thread = db.query(ConversationThread).filter(
            ConversationThread.id == thread_id, ConversationThread.user_id == ctx.deps.user_id,
        ).one_or_none()
        if thread is None:
            raise ModelRetry("Conversation not found.")
        # Completed exchanges only; interrupted output was never a finished answer.
        text = "\n\n".join(f"{m.author}: {m.content}" for m in ordered_thread_messages(db, thread_id)
                           if m.status == "completed")
        end = min(len(text), offset + limit)
        ctx.deps.looked_up["passages"].append({"thread_id": thread_id, "offset": offset, "end": end})
        event("memory.tool_used", user_id=ctx.deps.user_id, tool="read_conversation",
              thread_id=thread_id, offset=offset, characters=end - offset)
        return {"thread_id": thread_id, "text": text[offset:end],
                "next_offset": end if end < len(text) else None,
                "guidance": "Historical context only; not fresh evidence."}


# === Interfaces ====================================================================
#
# Opening an interface's tools in a conversation, and proposing to turn one on or off.
#
# An enabled interface's tools are not sent until the agent asks for them. Each
# interface gets an `open_<key>` tool, whose description says what opening it
# offers, and which brings in its tools and instructions in place of itself. They
# stay open for the rest of the conversation, so the start of the prompt changes
# at most once per interface and stays cached after.
#
# Turning an interface on or off is a proposal like any other change, approved on
# its card (`chat/artifacts.py`). Turning one on is usually the way
# to what the user asked for, so the reply waits on that card and then carries on
# with the interface open.

def opener(descriptor: InterfaceDescriptor) -> Callable[[RunContext[TurnDeps]], dict]:
    key = descriptor.key

    def open_interface(ctx: RunContext[TurnDeps]) -> dict:
        from app.chat.context import instructions_on_loading

        require_enabled(ctx.deps.user_id, key)
        if key in ctx.deps.loaded_interfaces:
            return {"opened": key, "note": "Already open; call its tools."}
        instructions = instructions_on_loading(key)
        ctx.deps.loaded_interfaces.add(key)
        return {"opened": key, "instructions": instructions}

    open_interface.__name__ = f"open_{key}"
    open_interface.__doc__ = (
        f"Open the {descriptor.name.lower()} before doing anything with it, to {descriptor.tools_summary}. "
        "Its tools can be called from your next step, and stay open for the rest of the conversation."
    )
    return open_interface


def register_toggle(tools: ChatToolbox) -> None:
    from app.chat.artifacts import INTERFACE_TOGGLE

    register_drafting(tools, INTERFACE_TOGGLE, label="Drafting that change…", guidance=(
        " Turns one of Attune's interfaces on or off; the turn context lists them and whether each is on."
        " Turning one on waits for the user's decision, which comes back as the result."
    ), waits_for_decision=lambda payload: payload.get("enabled", True))
