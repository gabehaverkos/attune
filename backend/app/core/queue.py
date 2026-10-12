"""One durable queue for background work, whoever's work it is.

An item is a unit of work of some `kind`, waiting in a `lane`. Lanes serialize:
at most one item per lane runs at a time, so one user's memory work happens in
order while different users' runs side by side. A worker claims an item under a
lease and renews it while working; an item whose lease lapses (its worker died)
goes back to the queue. Failures back off and give up after `max_attempts`, and
a handler that is not ready yet hands its item back without spending an attempt.

The table is the whole truth: enqueueing commits with the caller's own
transaction, so work is scheduled exactly when the change that needs it is.
Chat runs keep their own table, because they also carry an event log that
viewers replay; everything else in the background goes through here.

Waiting is cheap: Postgres LISTEN/NOTIFY wakes an idle worker the moment work
is committed, and wakes chat's readers the same way. A notification is only a
signal, never the work itself, so a missed one costs at most a polling interval.

The wake-ups come first below, then the items.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy import DateTime, exists, ForeignKey, Index, Integer, JSON, select, String, text, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, Mapped, mapped_column, Session
from typing import Any

from app.config import get_settings
from app.core.database import Base, SessionLocal
from app.core.time import utc_now


# === Wake-ups ======================================================================
#
# Wake-ups: Postgres LISTEN/NOTIFY, so a waiter stops waiting the moment something is committed.
#
# A notification is only a signal, never the work itself, so a missed one costs at
# most a polling interval.

logger = logging.getLogger(__name__)


def notify(db: Session, channel: str, payload: str = "") -> None:
    """Delivered when the surrounding transaction commits, and dropped if it rolls back."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": channel, "payload": payload})


class Wakeups:
    """One listening connection per process, fanned out to local waiters.

    A waiter subscribes to a channel, or to one payload on it, such as a run's id.
    """

    def __init__(self) -> None:
        self._waiters: dict[tuple[str, str | None], set[asyncio.Event]] = defaultdict(set)
        self._task: asyncio.Task | None = None

    def start(self, *channels: str, url: str | None = None) -> None:
        """Listen on these channels for the life of the process. Call from its event loop."""
        url = url or get_settings().database_url
        if self._task is None and channels and url.startswith("postgresql"):
            self._task = asyncio.create_task(self._listen(url.replace("+psycopg", "", 1), channels))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _listen(self, url: str, channels: tuple[str, ...]) -> None:
        import psycopg
        from psycopg import sql

        if sys.platform == "win32" and isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):
            # Psycopg's async mode needs a selector loop. Polling still works.
            logger.info("Wake-ups are unavailable on this event loop; waiters poll instead.")
            return
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
                    for channel in channels:
                        await connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
                    async for notification in connection.notifies():
                        for key in ((notification.channel, None), (notification.channel, notification.payload)):
                            for event in self._waiters.get(key, ()):
                                event.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Wake-up listener disconnected; waiters fall back to polling.")
                await asyncio.sleep(2)

    @contextmanager
    def subscription(self, channel: str, payload: str | None = None):
        """Register before reading, so a change made between the read and the wait still wakes."""
        key = (channel, payload)
        event = asyncio.Event()
        self._waiters[key].add(event)
        try:
            yield event
        finally:
            self._waiters[key].discard(event)
            if not self._waiters[key]:
                del self._waiters[key]


async def wait_for_wakeup(event: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout)
    except TimeoutError:
        pass
    event.clear()


wakeups = Wakeups()


# === Items =========================================================================
#
# The queued items: enqueueing, claiming under a lease, finishing, and the loops that work them.


PENDING, RUNNING, COMPLETED, FAILED = "pending", "running", "completed", "failed"
# Notified with the item's kind whenever waiting work is committed.
WORK_QUEUED = "attune_work_queued"
# An idle worker still looks this often: for delayed and retried items, and in
# case a notification was missed.
IDLE_SECONDS = 5.0


class WorkItem(Base):
    __tablename__ = "work_queue"
    __table_args__ = (
        Index("ix_work_queue_due", "status", "run_after"),
        Index("ix_work_queue_subject", "subject"),
        Index("ix_work_queue_user", "user_id", "created_at"),
        # The lane rule, enforced where two workers could otherwise both win a claim.
        Index("ux_work_queue_running_lane", "lane", unique=True,
              postgresql_where=text("status = 'running'"), sqlite_where=text("status = 'running'")),
        # At most one waiting item per dedupe key; enqueueing again updates it.
        Index("ux_work_queue_pending_dedupe", "dedupe_key", unique=True,
              postgresql_where=text("status = 'pending'"), sqlite_where=text("status = 'pending'")),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    lane: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=True)
    # What the work is about (a thread, a source), for finding it again.
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=PENDING)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@dataclass(frozen=True)
class Claimed:
    """A claimed item, detached from any session, for a handler to work from."""

    id: str
    kind: str
    lane: str
    user_id: str | None
    subject: str | None
    payload: dict[str, Any]
    attempts: int


class NotReady(Exception):
    """Raised by a handler whose work cannot start yet; the item waits, unpenalized."""

    def __init__(self, retry_in: timedelta):
        super().__init__("not ready")
        self.retry_in = retry_in


def enqueue(
    db: Session, *, kind: str, lane: str, user_id: str | None = None, subject: str | None = None,
    payload: dict[str, Any] | None = None, delay: timedelta = timedelta(0),
    dedupe_key: str | None = None, max_attempts: int = 3,
) -> WorkItem:
    """Add work in the caller's transaction. The caller commits.

    With a `dedupe_key`, an item already waiting under that key is updated
    instead: the later request replaces its payload and timing.
    """
    item = None
    if dedupe_key is not None:
        item = db.query(WorkItem).filter(
            WorkItem.dedupe_key == dedupe_key, WorkItem.status == PENDING,
        ).one_or_none()
    if item is None:
        item = WorkItem(id=str(uuid.uuid7()), kind=kind, lane=lane, user_id=user_id, subject=subject,
                        dedupe_key=dedupe_key, max_attempts=max_attempts)
        db.add(item)
    item.payload = payload or {}
    item.run_after = utc_now() + delay
    db.flush()
    notify(db, WORK_QUEUED, kind)
    return item


def cancel(db: Session, *, subject: str, kinds: tuple[str, ...]) -> int:
    """Drop waiting work about a subject that no longer exists. The caller commits."""
    return db.query(WorkItem).filter(
        WorkItem.subject == subject, WorkItem.kind.in_(kinds), WorkItem.status == PENDING,
    ).delete(synchronize_session=False)


def claim(owner: str, kinds: tuple[str, ...], lease: timedelta) -> Claimed | None:
    """Take the oldest due item of these kinds whose lane is free, or None."""
    with SessionLocal() as db:
        running = aliased(WorkItem)
        now = utc_now()
        item = db.execute(
            select(WorkItem).where(
                WorkItem.status == PENDING, WorkItem.run_after <= now, WorkItem.kind.in_(kinds),
                ~exists().where(running.lane == WorkItem.lane, running.status == RUNNING),
            ).order_by(WorkItem.run_after, WorkItem.created_at).limit(1).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if item is None:
            return None
        item.status, item.owner = RUNNING, owner
        # The key only merges waiting requests; one arriving now queues anew.
        item.dedupe_key = None
        item.lease_expires_at = now + lease
        item.started_at = now
        item.attempts += 1
        claimed = Claimed(item.id, item.kind, item.lane, item.user_id, item.subject,
                          dict(item.payload or {}), item.attempts)
        try:
            db.commit()
        except IntegrityError:
            # Another worker started this lane in the meantime; try again later.
            db.rollback()
            return None
        return claimed


def _owned(db: Session, item_id: str, owner: str) -> WorkItem | None:
    return db.query(WorkItem).filter(
        WorkItem.id == item_id, WorkItem.owner == owner, WorkItem.status == RUNNING,
    ).with_for_update().one_or_none()


def renew(item_id: str, owner: str, lease: timedelta) -> bool:
    with SessionLocal() as db:
        item = _owned(db, item_id, owner)
        if item is None:
            return False
        item.lease_expires_at = utc_now() + lease
        db.commit()
        return True


def complete(item_id: str, owner: str) -> None:
    with SessionLocal() as db:
        if item := _owned(db, item_id, owner):
            item.status, item.owner, item.lease_expires_at = COMPLETED, None, None
            item.finished_at, item.last_error = utc_now(), None
            db.commit()


def fail(item_id: str, owner: str, error: str) -> bool:
    """Record a failure; back off and retry unless attempts are spent. Returns True if it gave up."""
    with SessionLocal() as db:
        item = _owned(db, item_id, owner)
        if item is None:
            return False
        item.owner, item.lease_expires_at, item.last_error = None, None, error[:2000]
        if item.attempts >= item.max_attempts:
            item.status, item.finished_at = FAILED, utc_now()
        else:
            item.status = PENDING
            item.run_after = utc_now() + timedelta(minutes=2 ** item.attempts)
        db.commit()
        return item.status == FAILED


def release(item_id: str, owner: str, retry_in: timedelta) -> None:
    """Hand back work that could not start yet, without spending an attempt."""
    with SessionLocal() as db:
        if item := _owned(db, item_id, owner):
            item.status, item.owner, item.lease_expires_at = PENDING, None, None
            item.attempts = max(0, item.attempts - 1)
            item.run_after = utc_now() + retry_in
            db.commit()


async def work(
    claimed: Claimed, owner: str, handler: Callable[[Claimed], Awaitable[None]], *,
    lease: timedelta, timeout: float,
) -> str:
    """Run a handler for a claimed item, renewing its lease, and settle the item.

    Returns how it ended: "completed", "waiting" (not ready), "retrying",
    "failed" (attempts spent), or "lost" (the lease lapsed and the handler was
    stopped; whoever holds the item now settles it).
    """
    job = asyncio.create_task(asyncio.wait_for(handler(claimed), timeout=timeout))
    renew_every = lease.total_seconds() / 3
    lost = False

    async def keep_lease() -> None:
        nonlocal lost
        while not job.done():
            await asyncio.sleep(renew_every)
            if not job.done() and not await asyncio.to_thread(renew, claimed.id, owner, lease):
                lost = True
                job.cancel()
                return

    keeper = asyncio.create_task(keep_lease())
    try:
        await job
    except NotReady as waiting:
        await asyncio.to_thread(release, claimed.id, owner, waiting.retry_in)
        return "waiting"
    except asyncio.CancelledError:
        if not lost:
            # The caller itself is being cancelled (shutdown): stop the work too,
            # and let the lease lapse so the item is picked up again. Cancelling the
            # caller cancels the job with it, so the job's own state cannot tell the two apart.
            job.cancel()
            raise
        return "lost"
    except Exception as error:
        gave_up = await asyncio.to_thread(fail, claimed.id, owner, f"{type(error).__name__}: {error}")
        return "failed" if gave_up else "retrying"
    finally:
        keeper.cancel()
    await asyncio.to_thread(complete, claimed.id, owner)
    return "completed"


def prune_completed(kinds: tuple[str, ...], keep: timedelta) -> int:
    """Delete items of these kinds that completed longer ago than `keep`. Failed
    ones stay: they are the record of what went wrong."""
    with SessionLocal() as db:
        pruned = db.query(WorkItem).filter(
            WorkItem.status == COMPLETED, WorkItem.kind.in_(kinds), WorkItem.finished_at < utc_now() - keep,
        ).delete(synchronize_session=False)
        db.commit()
        return int(pruned)


def recover_expired() -> int:
    """Return running items whose worker stopped renewing to the queue."""
    with SessionLocal() as db:
        recovered = db.query(WorkItem).filter(
            WorkItem.status == RUNNING, WorkItem.lease_expires_at < utc_now(),
        ).update({WorkItem.status: PENDING, WorkItem.owner: None, WorkItem.lease_expires_at: None,
                  WorkItem.run_after: utc_now()}, synchronize_session=False)
        db.commit()
        return int(recovered)
