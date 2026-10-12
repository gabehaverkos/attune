"""A run: the durable unit of assistant work behind one accepted user message.

Its state lives in Postgres, and its ordered event log is what a viewer replays
from any cursor. Nothing here is transport-specific: the NDJSON stream is just
these events serialized one per line.

Each lifecycle function (accept, claim, fence, publish, cancel, finish, recover)
is one short transaction. Model and network waits never happen inside them; the
worker calls in, commits, and goes back to waiting.

A notification (`app/core/queue.py`) only wakes a waiting reader early;
every reader still reads the durable tables, so a missed notification costs at
most one polling interval. HTTP owns only the observer: disconnecting ends the
replay and nothing else.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import time
from typing import Any, cast, Literal
import uuid

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    case, cast as cast_sql, CheckConstraint, CursorResult, DateTime, Float, ForeignKey, func, Index,
    Integer, JSON, select, String, text, UniqueConstraint, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, Session

from app.core.auth import User
from app.chat.threads import (
    add_message_to_db, ConversationMessage, ConversationThread,
    message_contract, ordered_thread_messages,
)
from app.chat.artifacts import artifact_to_response
from app.config import CHAT_PRICE, DAILY_CHAT_DOLLARS_PER_ADDRESS, UNMEASURED_RUN_DOLLARS
from app.core.database import Base, SessionLocal
from app.interfaces.registry import enabled_interface_keys
from app.core.queue import notify, wait_for_wakeup, wakeups
from app.core.time import aware, utc_now


# --- Contract: states, events, and errors ----------------------------------------

# A waiting run is paused on a card the user has to decide first; deciding it
# queues the run again, and the reply carries on from where it stopped.
RunStatus = Literal["queued", "running", "waiting", "completed", "failed", "canceled"]
ACTIVE_RUN_STATUSES: tuple[RunStatus, ...] = ("queued", "running", "waiting")

# Why a run ended the way it did. Stable codes, never provider or exception text.
FAILURE_MESSAGES: dict[str, str] = {
    "response_stopped": "Stopped.",
    "assistant_unconfigured": "The assistant is not configured.",
    "budget_exhausted": "This response hit its work limit before it finished.",
    "model_unavailable": "The assistant service could not be reached.",
    "model_rate_limited": "The assistant service is busy. Try again shortly.",
    "generation_failed": "The assistant could not finish this response.",
    "generation_timed_out": "This response took too long and was ended.",
    "generation_worker_lost": "This response was interrupted.",
}


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = 0


class RunAccepted(_Event):
    type: Literal["run.accepted"] = "run.accepted"
    run_id: str
    thread_id: str
    user_message_id: str
    assistant_message_id: str


class RunActivity(_Event):
    """What the run is doing right now, as a fixed label. Never tool arguments."""

    type: Literal["run.activity"] = "run.activity"
    kind: Literal["queued", "context", "thinking", "tool"]
    label: str
    tool_call_id: str | None = None


class TextDelta(_Event):
    type: Literal["text.delta"] = "text.delta"
    message_id: str
    delta: str


class ArtifactCommitted(_Event):
    type: Literal["artifact.committed"] = "artifact.committed"
    message_id: str
    action: Literal["created", "updated", "superseded"]
    artifact: dict[str, Any]
    # Where in the reply's text the card goes (`ConversationMessageArtifactEvent`).
    text_offset: int | None = None


class RunHeld(_Event):
    """The run paused until the user decides these cards."""

    type: Literal["run.held"] = "run.held"
    artifact_ids: list[str]
    label: str


class RunCancelRequested(_Event):
    type: Literal["run.cancel_requested"] = "run.cancel_requested"


class RunCompleted(_Event):
    type: Literal["run.completed"] = "run.completed"
    message: dict[str, Any]


class RunFailed(_Event):
    type: Literal["run.failed"] = "run.failed"
    code: str
    message: str
    retryable: bool = True
    partial: dict[str, Any]


class RunCanceled(_Event):
    type: Literal["run.canceled"] = "run.canceled"
    partial: dict[str, Any]


TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.canceled"})


class StreamReset(BaseModel):
    """Sent instead of events when the cursor predates what is retained."""

    type: Literal["stream.reset"] = "stream.reset"
    reason: Literal["cursor_expired", "run_not_found"]


class ActivityLabels:
    CONTEXT = "Getting ready…"
    THINKING = "Thinking…"
    WAITING = "Waiting for your decision…"
    RESUMING = "Continuing…"


# --- Errors ----------------------------------------------------------------------

class RunFencedError(RuntimeError):
    """The caller no longer owns the run: it was canceled, finished, or taken over."""


class ActiveRunError(RuntimeError):
    pass


class DailyLimitError(RuntimeError):
    """The account has started as many replies as a day allows."""


class RequestReuseError(ValueError):
    """A client request ID was reused for a different message."""


# --- Tables ----------------------------------------------------------------------

class ConversationRun(Base):
    """Durable generation work; viewing a stream does not own its lifetime.

    Ownership is a lease: a worker holds a run only while `worker_id` is its own and
    the lease is unexpired, and every write it makes is checked against both.

    A run can pause on cards the user has to decide before it can go on (turning
    an interface on, say). It then waits, owned by no worker, with what it had
    done so far in `hold`; deciding the last of them queues it again, and it
    carries on in the same reply. A new message in the thread ends the wait.
    """

    __tablename__ = "conversation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'completed', 'failed', 'canceled')",
            name="ck_conversation_run_status",
        ),
        UniqueConstraint("user_id", "client_request_id", name="uq_conversation_run_request"),
        # The database, not the browser, is what keeps a thread to one response.
        Index(
            "uq_conversation_run_active_thread", "thread_id", unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'waiting')"),
            sqlite_where=text("status IN ('queued', 'running', 'waiting')"),
        ),
        Index("ix_conversation_run_status_created", "status", "created_at"),
        Index("ix_conversation_run_address_created", "client_address", "created_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String, ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    user_message_id: Mapped[str] = mapped_column(String, nullable=False)
    assistant_message_id: Mapped[str] = mapped_column(String, nullable=False)
    client_request_id: Mapped[str] = mapped_column(String, nullable=False)
    # The visitor's network address, as the web app's proxy saw it: the daily budget
    # is kept per address.
    client_address: Mapped[str | None] = mapped_column(String, nullable=True)
    # Identifies what was asked, so a reused request ID can be told from a retry.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_of_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone_name: Mapped[str] = mapped_column(String, default="UTC", nullable=False)
    enabled_interfaces: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String, default="queued", nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The sequence number of the latest event; the next event takes the one after.
    event_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # What the turn used, for traces and debugging. Sizes and counts, never content.
    context_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # While waiting: the cards it waits on, by the tool call that made each, and the
    # agent's messages so far, to resume from. Cleared once the run settles.
    hold: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationRunEvent(Base):
    """One entry in a run's ordered, replayable event log."""

    __tablename__ = "conversation_run_events"
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation_runs.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DeletedAccountSpending(Base):
    """What a deleted account had spent in its last day, against the address it
    spent from: the one thing kept of it, so the address's allowance stays spent.
    It names no account, and goes once it is a day old."""

    __tablename__ = "deleted_account_spending"
    __table_args__ = (Index("ix_deleted_account_spending_address", "client_address", "spent_at"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    client_address: Mapped[str] = mapped_column(String, nullable=False)
    dollars: Mapped[float] = mapped_column(Float, nullable=False)
    # When the account last spent from the address.
    spent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- Lookups ---------------------------------------------------------------------

def active_run(db: Session, thread_id: str) -> ConversationRun | None:
    return db.query(ConversationRun).filter(
        ConversationRun.thread_id == thread_id,
        ConversationRun.status.in_(ACTIVE_RUN_STATUSES),
    ).one_or_none()


# --- Lifecycle -------------------------------------------------------------------

LEASE = timedelta(seconds=30)
# Events outlive their run long enough for any open tab to catch up; after that a
# viewer is sent a fresh snapshot instead.
EVENT_RETENTION = timedelta(hours=24)

Outcome = Literal["completed", "failed", "canceled"]


@dataclass(frozen=True)
class AcceptedTurn:
    run_id: str
    thread_id: str
    user_message_id: str
    assistant_message_id: str
    thread_created: bool
    cursor: int


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def append_event(db: Session, run: ConversationRun, event: BaseModel) -> int:
    """Append to a run's log. The caller holds the run row lock and commits."""
    run.event_sequence += 1
    payload = event.model_dump(mode="json")
    payload["seq"] = run.event_sequence
    db.add(ConversationRunEvent(
        run_id=run.id, sequence=run.event_sequence, type=payload["type"], payload=payload,
    ))
    notify(db, RUN_EVENTS, run.id)
    return run.event_sequence


def _existing_request(db: Session, user_id: str, client_request_id: str, fingerprint: str) -> AcceptedTurn | None:
    run = db.query(ConversationRun).filter(
        ConversationRun.user_id == user_id, ConversationRun.client_request_id == client_request_id,
    ).one_or_none()
    if run is None:
        return None
    if run.request_fingerprint != fingerprint:
        raise RequestReuseError("This request ID was already used for a different message.")
    return AcceptedTurn(run.id, run.thread_id, run.user_message_id, run.assistant_message_id,
                        thread_created=False, cursor=1)


def _usage_tokens(key: str):
    return func.coalesce(cast_sql(ConversationRun.usage[key].as_string(), Integer), 0)


def _charged():
    """What a run is charged, at the chat model's prices. Completed runs count what
    they reported; the rest count at least UNMEASURED_RUN_DOLLARS."""
    inputs, cached, outputs = (_usage_tokens(key) for key in ("input_tokens", "cache_read_tokens", "output_tokens"))
    reported = ((inputs - cached) * CHAT_PRICE.input + cached * CHAT_PRICE.cached_input
                + outputs * CHAT_PRICE.output) / 1_000_000
    return case(
        (ConversationRun.status == "completed", reported),
        else_=func.greatest(reported, UNMEASURED_RUN_DOLLARS),
    )


def spent_today(db: Session, *, client_address: str | None, user_id: str) -> float:
    """Dollars of chat charged in the last day to this address, or to the account
    when the address is unknown. An address is also charged what accounts since
    deleted spent from it."""
    since = utc_now() - timedelta(days=1)
    scope = (ConversationRun.client_address == client_address if client_address
             else ConversationRun.user_id == user_id)
    spent = float(db.query(func.coalesce(func.sum(_charged()), 0)).filter(
        scope, ConversationRun.created_at > since).scalar() or 0)
    if client_address:
        spent += float(db.query(func.coalesce(func.sum(DeletedAccountSpending.dollars), 0)).filter(
            DeletedAccountSpending.client_address == client_address,
            DeletedAccountSpending.spent_at > since).scalar() or 0)
    return spent


def keep_spending(db: Session, user_id: str) -> None:
    """Before an account is deleted: note what it spent in the last day, chat and
    reviews, against each address it spent from, so deleting it gives no allowance
    back. Notes a day old are dropped here too. The caller commits."""
    from app.memory import public as memory  # Memory reads chat's runs, so it is imported here.

    since = utc_now() - timedelta(days=1)
    db.query(DeletedAccountSpending).filter(DeletedAccountSpending.spent_at <= since).delete(synchronize_session=False)
    spent: dict[str, float] = memory.review_spend_by_address(db, user_id)
    latest: dict[str, datetime] = {}
    for address, dollars, last in db.query(
            ConversationRun.client_address, func.sum(_charged()), func.max(ConversationRun.created_at)).filter(
            ConversationRun.user_id == user_id, ConversationRun.client_address.is_not(None),
            ConversationRun.created_at > since).group_by(ConversationRun.client_address):
        if address is not None:
            spent[address] = spent.get(address, 0.0) + float(dollars)
            latest[address] = last
    for address, dollars in spent.items():
        db.add(DeletedAccountSpending(id=str(uuid.uuid7()), client_address=address, dollars=dollars,
                                      spent_at=latest.get(address) or utc_now()))


def allowance_used(db: Session, *, client_address: str | None, user_id: str) -> float:
    """How much of the day's allowance this address has spent, from 0 to 1: its
    chat, and the reviews of memory asked for from it."""
    from app.memory import public as memory  # Memory reads chat's runs, so it is imported here.

    spent = spent_today(db, client_address=client_address, user_id=user_id) + memory.review_spend(
        db, client_address=client_address, user_id=user_id)
    return min(1.0, spent / DAILY_CHAT_DOLLARS_PER_ADDRESS)


def _open_run(
    db: Session, thread: ConversationThread, *, user_id: str, user_message_id: str,
    client_request_id: str, fingerprint: str, timezone_name: str, client_address: str | None,
    retry_of: str | None = None,
) -> ConversationRun:
    if (current := active_run(db, thread.id)) is not None:
        if current.status != "waiting":
            raise ActiveRunError("The assistant is already responding in this conversation.")
        # Writing again is moving on: the paused reply ends as it stands, and its
        # cards stay up to decide whenever.
        _terminate(db, current, "completed", None)
    if client_address:
        # Accounts sharing an address take turns here, so they can't all pass at once.
        db.execute(select(func.pg_advisory_xact_lock(func.hashtext(client_address))))
    if allowance_used(db, client_address=client_address, user_id=user_id) >= 1:
        raise DailyLimitError(
            "Today's demo allowance for your connection is used up. Try again tomorrow."
        )
    assistant = add_message_to_db(db, thread.id, "assistant", "", status="streaming")
    run = ConversationRun(
        id=str(uuid.uuid7()), thread_id=thread.id, user_id=user_id,
        user_message_id=user_message_id, assistant_message_id=assistant.id,
        client_request_id=client_request_id, request_fingerprint=fingerprint,
        client_address=client_address, retry_of_run_id=retry_of, timezone_name=timezone_name,
        # Captured at acceptance; each tool still rechecks when it runs.
        enabled_interfaces=sorted(enabled_interface_keys(user_id, db)),
        status="queued", event_sequence=0,
    )
    db.add(run)
    db.flush()
    append_event(db, run, RunAccepted(
        run_id=run.id, thread_id=thread.id, user_message_id=user_message_id,
        assistant_message_id=assistant.id,
    ))
    notify(db, RUN_QUEUED, run.id)
    return run


def accept_turn(
    db: Session, *, user_id: str, thread_id: str, content: str,
    client_request_id: str, timezone_name: str = "UTC", client_address: str | None = None,
) -> AcceptedTurn:
    """Save the user message, its reply placeholder and the queued run together.

    `thread_id` may be "draft", in which case the thread is created here too, under
    the same request-ID protection: a repeated send returns the first thread.
    """
    fingerprint = _fingerprint("turn", thread_id, content)
    # Serializes one user's sends, so the request-ID check cannot race itself.
    db.query(User).filter(User.id == user_id).with_for_update().one()
    if existing := _existing_request(db, user_id, client_request_id, fingerprint):
        db.rollback()
        return existing
    created = thread_id == "draft"
    if created:
        thread = ConversationThread(id=str(uuid.uuid7()), user_id=user_id)
        db.add(thread)
        db.flush()
    else:
        thread = db.query(ConversationThread).filter(
            ConversationThread.id == thread_id, ConversationThread.user_id == user_id,
        ).with_for_update().one_or_none()
        if thread is None:
            raise LookupError("Chat thread not found.")
    user_message = add_message_to_db(db, thread.id, "user", content)
    run = _open_run(
        db, thread, user_id=user_id, user_message_id=user_message.id,
        client_request_id=client_request_id, fingerprint=fingerprint, timezone_name=timezone_name,
        client_address=client_address,
    )
    accepted = AcceptedTurn(run.id, thread.id, user_message.id, run.assistant_message_id,
                            thread_created=created, cursor=run.event_sequence)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise ActiveRunError("The assistant is already responding in this conversation.") from error
    return accepted


def retry_turn(
    db: Session, *, user_id: str, run_id: str, client_request_id: str, timezone_name: str = "UTC",
    client_address: str | None = None,
) -> AcceptedTurn:
    """Answer the same user message again, as a new run linked to the one that ended."""
    fingerprint = _fingerprint("retry", run_id)
    db.query(User).filter(User.id == user_id).with_for_update().one()
    if existing := _existing_request(db, user_id, client_request_id, fingerprint):
        db.rollback()
        return existing
    previous = db.query(ConversationRun).filter(
        ConversationRun.id == run_id, ConversationRun.user_id == user_id,
    ).one_or_none()
    if previous is None:
        raise LookupError("Response not found.")
    thread = db.query(ConversationThread).filter(
        ConversationThread.id == previous.thread_id,
    ).with_for_update().one()
    if previous.status not in ("failed", "canceled"):
        raise ActiveRunError("Only a stopped or failed response can be retried.")
    messages = ordered_thread_messages(db, thread.id)
    if not messages or messages[-1].id != previous.assistant_message_id:
        raise ActiveRunError("Only the latest response can be retried.")
    run = _open_run(
        db, thread, user_id=user_id, user_message_id=previous.user_message_id,
        client_request_id=client_request_id, fingerprint=fingerprint,
        timezone_name=timezone_name, client_address=client_address, retry_of=previous.id,
    )
    accepted = AcceptedTurn(run.id, thread.id, run.user_message_id, run.assistant_message_id,
                            thread_created=False, cursor=run.event_sequence)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise ActiveRunError("The assistant is already responding in this conversation.") from error
    return accepted


def claim_next_run(worker_id: str) -> str | None:
    with SessionLocal() as db:
        run = db.query(ConversationRun).filter(ConversationRun.status == "queued").order_by(
            ConversationRun.created_at,
        ).with_for_update(skip_locked=True).first()
        if run is None:
            return None
        now = utc_now()
        run.status = "running"
        run.worker_id = worker_id
        run.lease_expires_at = now + LEASE
        run.started_at = now
        append_event(db, run, RunActivity(kind="context", label=ActivityLabels.CONTEXT))
        run_id = run.id
        db.commit()
        return run_id


def renew_lease(run_id: str, worker_id: str) -> bool:
    with SessionLocal() as db:
        renewed = db.execute(update(ConversationRun).where(
            ConversationRun.id == run_id, ConversationRun.worker_id == worker_id,
            ConversationRun.status == "running",
        ).values(lease_expires_at=utc_now() + LEASE))
        db.commit()
        return bool(cast(CursorResult, renewed).rowcount)


def fence(db: Session, run_id: str, worker_id: str, *, allow_cancel_requested: bool = False) -> ConversationRun:
    """Lock the run and prove this worker may still write for it."""
    run = db.query(ConversationRun).filter(ConversationRun.id == run_id).with_for_update().one_or_none()
    if run is None or run.status != "running" or run.worker_id != worker_id:
        raise RunFencedError("This worker no longer owns the run.")
    lease = aware(run.lease_expires_at) if run.lease_expires_at else None
    if lease is None or lease < utc_now():
        raise RunFencedError("The run lease expired.")
    if run.cancel_requested_at is not None and not allow_cancel_requested:
        raise RunFencedError("The run was asked to stop.")
    return run


def publish(run_id: str, worker_id: str, message_id: str, text: str, events: list[BaseModel]) -> None:
    """Write buffered text and any events after it, in order, in one transaction."""
    with SessionLocal() as db:
        run = fence(db, run_id, worker_id)
        if text:
            message = db.get(ConversationMessage, message_id)
            assert message is not None
            message.content += text
            append_event(db, run, TextDelta(message_id=message_id, delta=text))
        for event in events:
            append_event(db, run, event)
        db.commit()


def request_cancel(db: Session, *, user_id: str, run_id: str) -> str:
    """Ask a run to stop. A queued run is canceled here; a running one by its worker."""
    run = db.query(ConversationRun).filter(
        ConversationRun.id == run_id, ConversationRun.user_id == user_id,
    ).with_for_update().one_or_none()
    if run is None:
        raise LookupError("Response not found.")
    if run.status not in ACTIVE_RUN_STATUSES:
        db.rollback()
        return run.status
    if run.cancel_requested_at is None:
        run.cancel_requested_at = utc_now()
        append_event(db, run, RunCancelRequested())
    if run.status == "queued":
        _terminate(db, run, "canceled", "response_stopped")
    elif run.status == "waiting":
        # Nothing is being written; the reply so far is the whole of it.
        _terminate(db, run, "completed", None)
    db.commit()
    return run.status


def _terminate(db: Session, run: ConversationRun, outcome: Outcome, code: str | None) -> None:
    now = utc_now()
    message = db.get(ConversationMessage, run.assistant_message_id)
    run.status = outcome
    run.failure_code = code
    run.finished_at = now
    run.lease_expires_at = None
    run.hold = None
    if message is not None and message.status == "streaming":
        message.status = {"completed": "completed", "failed": "failed", "canceled": "interrupted"}[outcome]
        message.failure_code = code
        message.completed_at = now
    db.flush()
    contract = message_contract(message, run.id).model_dump(mode="json") if message else {}
    if outcome == "completed":
        append_event(db, run, RunCompleted(message=contract))
    elif outcome == "canceled":
        append_event(db, run, RunCanceled(partial=contract))
    else:
        append_event(db, run, RunFailed(
            code=code or "generation_failed",
            message=FAILURE_MESSAGES.get(code or "", FAILURE_MESSAGES["generation_failed"]),
            partial=contract,
        ))
    from app.memory import public as memory

    # Every turn is memory's to learn from; nothing in chat asks for it.
    memory.reply_finished(db, run.user_id, run.thread_id)


def finish_run(
    run_id: str, worker_id: str, *, outcome: Outcome, code: str | None = None,
    text: str = "", usage: dict | None = None,
) -> bool:
    """Flush the last text and settle the run. False when another party already did."""
    with SessionLocal() as db:
        try:
            run = fence(db, run_id, worker_id, allow_cancel_requested=True)
        except RunFencedError:
            db.rollback()
            return False
        if run.cancel_requested_at is not None and outcome == "completed":
            # Finishing raced the stop; the user's request wins, the text stays.
            outcome, code = "canceled", "response_stopped"
        if text and run.cancel_requested_at is None:
            message = db.get(ConversationMessage, run.assistant_message_id)
            assert message is not None
            message.content += text
            append_event(db, run, TextDelta(message_id=run.assistant_message_id, delta=text))
        if usage is not None:
            run.usage = _combined_usage(run.usage, usage)
        _terminate(db, run, outcome, code)
        db.commit()
        return True


def hold_run(run_id: str, worker_id: str, *, text: str, usage: dict, hold: dict[str, Any]) -> bool:
    """Pause the run until the user decides the cards in `hold`. False when the run is
    no longer this worker's to pause, or the user asked it to stop."""
    with SessionLocal() as db:
        try:
            run = fence(db, run_id, worker_id)
        except RunFencedError:
            db.rollback()
            return False
        if text:
            message = db.get(ConversationMessage, run.assistant_message_id)
            assert message is not None
            message.content += text
            append_event(db, run, TextDelta(message_id=run.assistant_message_id, delta=text))
        run.status = "waiting"
        run.worker_id = None
        run.lease_expires_at = None
        run.usage = _combined_usage(run.usage, usage)
        run.hold = hold
        append_event(db, run, RunHeld(artifact_ids=list(hold["calls"].values()), label=ActivityLabels.WAITING))
        db.commit()
        return True


def resume_held(db: Session, artifact_id: str, thread_id: str) -> None:
    """Queue the run waiting on this card again, once it and every other card it
    waits on are decided. A card that failed to apply keeps it waiting."""
    from app.chat.artifacts import ConversationArtifact

    run = db.query(ConversationRun).filter(
        ConversationRun.thread_id == thread_id, ConversationRun.status == "waiting",
    ).with_for_update().one_or_none()
    waited_on = list(((run.hold if run else None) or {}).get("calls", {}).values())
    if run is None or artifact_id not in waited_on:
        return
    decided = db.query(ConversationArtifact.id).filter(
        ConversationArtifact.id.in_(waited_on),
        ConversationArtifact.status.in_(("completed", "denied")),
    ).count()
    if decided < len(waited_on):
        return
    run.status = "queued"
    # A card may have turned an interface on; the rest of the reply can use it.
    run.enabled_interfaces = sorted(enabled_interface_keys(run.user_id, db))
    append_event(db, run, RunActivity(kind="queued", label=ActivityLabels.RESUMING))
    notify(db, RUN_QUEUED, run.id)
    db.commit()


def end_expired_wait(db: Session, user_id: str, thread_id: str) -> None:
    """End the reply waiting on a card that expired: nobody is deciding it, so the
    reply ends as it stands, like one the user wrote past. The caller commits."""
    from app.chat.artifacts import ConversationArtifact

    run = db.query(ConversationRun).filter(
        ConversationRun.thread_id == thread_id, ConversationRun.user_id == user_id,
        ConversationRun.status == "waiting",
    ).with_for_update().one_or_none()
    waited_on = list(((run.hold if run else None) or {}).get("calls", {}).values())
    if run is None or not waited_on:
        return
    expired = db.query(ConversationArtifact.id).filter(
        ConversationArtifact.id.in_(waited_on), ConversationArtifact.status == "expired",
    ).count()
    if expired:
        _terminate(db, run, "completed", None)


# Counts a resumed run adds up across the stretches before and after its pause.
_SUMMED_USAGE = ("requests", "tool_calls", "input_tokens", "output_tokens", "cache_read_tokens",
                 "duration_ms", "text_characters")


def _combined_usage(earlier: dict | None, latest: dict) -> dict:
    if not earlier:
        return latest
    combined = {**earlier, **latest}
    for key in _SUMMED_USAGE:
        if key in earlier or key in latest:
            combined[key] = (earlier.get(key) or 0) + (latest.get(key) or 0)
    combined["tools"] = [*earlier.get("tools", []), *latest.get("tools", [])]
    combined["loaded"] = sorted({*earlier.get("loaded", []), *latest.get("loaded", [])})
    if earlier.get("first_text_ms") is not None:
        combined["first_text_ms"] = earlier["first_text_ms"]
    looked_up = [usage["looked_up"] for usage in (earlier, latest) if usage.get("looked_up")]
    if looked_up:
        combined["looked_up"] = {key: [item for found in looked_up for item in found.get(key, [])]
                                 for key in ("beliefs", "passages")}
    return combined


def record_context_stats(run_id: str, worker_id: str, stats: dict) -> None:
    with SessionLocal() as db:
        run = fence(db, run_id, worker_id)
        run.context_stats = stats
        db.commit()


def recover_expired_runs() -> int:
    """Settle runs whose worker stopped renewing. Their work is never replayed."""
    recovered = 0
    with SessionLocal() as db:
        runs = db.query(ConversationRun).filter(
            ConversationRun.status == "running", ConversationRun.lease_expires_at < utc_now(),
        ).with_for_update(skip_locked=True).all()
        for run in runs:
            if run.cancel_requested_at is not None:
                _terminate(db, run, "canceled", "response_stopped")
            else:
                _terminate(db, run, "failed", "generation_worker_lost")
            recovered += 1
        db.commit()
    return recovered


def prune_events() -> None:
    with SessionLocal() as db:
        finished = db.query(ConversationRun.id).filter(
            ConversationRun.status.not_in(ACTIVE_RUN_STATUSES),
            ConversationRun.finished_at < utc_now() - EVENT_RETENTION,
        )
        db.query(ConversationRunEvent).filter(
            ConversationRunEvent.run_id.in_(finished.scalar_subquery()),
        ).delete(synchronize_session=False)
        db.commit()


@dataclass(frozen=True)
class EventPage:
    events: list[dict]
    terminal: bool
    reset: Literal["cursor_expired", "run_not_found"] | None = None


def events_after(db: Session, *, user_id: str, run_id: str, after: int) -> EventPage:
    run = db.query(ConversationRun).filter(
        ConversationRun.id == run_id, ConversationRun.user_id == user_id,
    ).one_or_none()
    if run is None:
        return EventPage([], terminal=True, reset="run_not_found")
    rows = db.query(ConversationRunEvent).filter(
        ConversationRunEvent.run_id == run_id, ConversationRunEvent.sequence > after,
    ).order_by(ConversationRunEvent.sequence).limit(500).all()
    if after < run.event_sequence and (not rows or rows[0].sequence != after + 1):
        return EventPage([], terminal=True, reset="cursor_expired")
    events = [cast(dict, row.payload) for row in rows]
    terminal = any(event["type"] in TERMINAL_EVENT_TYPES for event in events)
    if not events and run.status not in ACTIVE_RUN_STATUSES:
        terminal = True
    return EventPage(events, terminal)


def current_activity(db: Session, run: ConversationRun) -> str | None:
    rows = db.query(ConversationRunEvent.type, ConversationRunEvent.payload).filter(
        ConversationRunEvent.run_id == run.id,
        ConversationRunEvent.type.in_(("run.activity", "run.held", "text.delta")),
    ).order_by(ConversationRunEvent.sequence.desc()).first()
    if rows is None or rows[0] == "text.delta":
        return None
    return cast(dict, rows[1]).get("label")


def committed_work(db: Session, run_ids: list[str]) -> list[str]:
    """What earlier attempts at a reply already left behind, described for the agent."""
    from app.chat.artifacts import ConversationArtifact
    from app.chat.tools import ConversationToolReceipt

    notes: list[str] = []
    for receipt in db.query(ConversationToolReceipt).filter(
        ConversationToolReceipt.run_id.in_(run_ids),
    ).order_by(ConversationToolReceipt.created_at).all():
        result = receipt.result
        if result.get("status") == "committed" and (artifact := db.get(ConversationArtifact, result.get("artifact_id"))):
            snapshot = artifact_to_response(artifact)
            notes.append(f"Proposal {snapshot.id} ({snapshot.type}, revision {snapshot.revision}, {snapshot.status}) was already prepared.")
        elif result.get("status") == "queued":
            notes.append("A memory request was already queued.")
        elif result.get("status") in ("corrected", "forgotten"):
            notes.append(f"Memory was already {result['status']}: {result.get('was')}")
    return notes


# --- Wake-ups --------------------------------------------------------------------

RUN_QUEUED = "attune_chat_run_queued"
RUN_EVENTS = "attune_chat_run_events"


# --- Replay to a viewer ----------------------------------------------------------

# A reader that misses a notification still sees new events this soon.
FALLBACK_POLL_SECONDS = 1.0
# Keeps idle connections open through proxies while a model is slow to start.
HEARTBEAT_SECONDS = 15.0


def encode(event: dict[str, Any]) -> bytes:
    return (json.dumps(event, separators=(",", ":"), default=str) + "\n").encode("utf-8")


def _page(user_id: str, run_id: str, after: int) -> EventPage:
    with SessionLocal() as db:
        return events_after(db, user_id=user_id, run_id=run_id, after=after)


async def observe_run(user_id: str, run_id: str, after: int) -> AsyncIterator[bytes]:
    cursor = after
    last_sent = time.monotonic()
    with wakeups.subscription(RUN_EVENTS, run_id) as wake:
        while True:
            page = await asyncio.to_thread(_page, user_id, run_id, cursor)
            if page.reset:
                yield encode(StreamReset(reason=page.reset).model_dump())
                return
            for event in page.events:
                cursor = event["seq"]
                yield encode(event)
                last_sent = time.monotonic()
            if page.terminal:
                return
            if page.events:
                continue
            if time.monotonic() - last_sent >= HEARTBEAT_SECONDS:
                yield encode({"type": "stream.heartbeat", "seq": cursor})
                last_sent = time.monotonic()
            await wait_for_wakeup(wake, FALLBACK_POLL_SECONDS)
