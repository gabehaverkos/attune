"""Memory's background work: four kinds of item on the shared queue, and the worker.

- conversation: learn what a conversation has said since memory last learned it.
  It is queued as every reply ends, whatever became of the reply, so each message
  is learned as soon as its turn is over, usually on its own, read in the
  conversation around it. The gate reads each new message; placement writes what
  passes.
- statement: learn something the user typed straight to memory. It skips the
  gate: it was typed to be kept. A correction is already in the belief it
  corrects, as typed, so learning it only tidies that belief.
- summaries: rewrite the summaries and things whose beliefs changed. Queued after
  a change with a short delay, so a burst of messages is summed up once.
- review: look over the whole graph and arrange it better (`review.py`). Queued
  when memory learns something, for once things are quiet, and at most once a day.
  The user can also ask for one from the panel, a few times a day; while they
  have, none is queued for them. Reviews are charged to the address's daily
  allowance, with chat, and one that would overspend it is not run.

Every item for a user shares one lane, so a user's memory is learned one item at
a time, in the order it was asked for. Different users' work runs side by side.
Forgetting a deleted conversation is not queued: it happens in the transaction
that deletes it. How items are claimed, leased, retried and recovered is in
`app/core/queue.py`.

    uv run python -m app.memory.work    # run the memory worker
"""

import asyncio
from datetime import datetime, timedelta
import logging
import os
import socket
import time
import uuid

from sqlalchemy import and_, ColumnElement, delete, func, or_, select, tuple_
from sqlalchemy.orm import aliased, Session

from app.core.database import SessionLocal
from app.core.observability import (
    configure_memory_logging, configure_tracing, event, job_context, operation, record_content,
    record_turn_content, shutdown_tracing, traced,
)
from app.core.time import utc_now
from app.memory.graph import (
    embed, links_of, load, mark_stale, MemoryEvidence, MemoryExclusion, MemoryNode, prune,
)
from app.memory.sources import (
    CHANGED, chat_spent, CONVERSATION as CONVERSATION_KIND, ConversationMessage, CORRECTION, EMPTY,
    FAILED, last_address, LEARNED, LEARNING, lock_thread, MemoryChange, MemorySource, ordered_messages,
    PENDING, Reading, Said, turn_lookups,
)
from app.memory.learning import apply, gate, KEEP, place, refresh
from app.memory.review import review
from app.core import queue
from app.core.queue import notify, wait_for_wakeup, wakeups

CONVERSATION = "memory.conversation"
STATEMENT = "memory.statement"
SUMMARIES = "memory.summaries"
REVIEW = "memory.review"
KINDS = (CONVERSATION, STATEMENT, SUMMARIES, REVIEW)

# At most this many of the user's messages are learned in one pass. A conversation
# is learned as each reply ends, so a pass is usually one message; this only splits
# a backlog: one learned after the worker was down.
PASSAGE_MESSAGES = 10
# Messages before the new ones a pass reads, for what they answer.
CONTEXT_MESSAGES = 16
SUMMARY_DELAY = timedelta(seconds=90)
# A review waits for memory to go quiet, and a user's reviews are a day apart.
REVIEW_QUIET = timedelta(minutes=10)
REVIEW_EVERY = timedelta(hours=24)
# How many reviews a user may ask for within that day.
REVIEWS_REQUESTED = 4
# How many beliefs a user may correct within a day.
CORRECTIONS_A_DAY = 40


def lane(user_id: str) -> str:
    return f"memory:{user_id}"


# --- Conversations -----------------------------------------------------------------

def conversation_source(db: Session, user_id: str, thread_id: str) -> MemorySource:
    """A conversation's source, made the first time the user says something in it."""
    source = db.scalars(select(MemorySource).where(MemorySource.thread_id == thread_id)).first()
    if source is None:
        source = MemorySource(id=str(uuid.uuid7()), user_id=user_id, kind=CONVERSATION_KIND,
                              thread_id=thread_id, status=PENDING)
        db.add(source)
        db.flush()
    return source


def reply_finished(db: Session, user_id: str, thread_id: str) -> None:
    """Learn what the user said, now. The caller commits.

    A reply that ends while the conversation is being learned queues it again
    behind that work, so nothing said in the meantime is missed.
    """
    source = conversation_source(db, user_id, thread_id)
    if source.status != LEARNING:
        source.status = PENDING
    queue_conversation(db, user_id, thread_id)


def forget_conversation(db: Session, user_id: str, thread_id: str) -> None:
    """Forget what a deleted conversation taught. The caller commits.

    Called in the transaction that deletes the conversation, and before it. Its
    evidence goes, and every belief left resting on nothing goes with it, so a
    belief the user also said somewhere else survives.
    """
    source = db.scalars(select(MemorySource).where(
        MemorySource.user_id == user_id, MemorySource.thread_id == thread_id)).first()
    if source is None:
        return
    touched = set(db.scalars(select(MemoryEvidence.node_id).where(MemoryEvidence.source_id == source.id)))
    # Evidence, exclusions and changes go with the source.
    db.execute(delete(MemoryEvidence).where(MemoryEvidence.source_id == source.id))
    db.execute(delete(MemoryExclusion).where(MemoryExclusion.source_id == source.id))
    db.delete(source)
    links = links_of(db, user_id)
    touched |= prune(db, user_id, links)
    if mark_stale(db, links, touched):
        queue_summaries(db, user_id)
    event("memory.conversation_forgotten", user_id=user_id, thread_id=thread_id)


def _usable(message: ConversationMessage) -> bool:
    return message.status != "streaming" and bool(message.content.strip())


def _said(message: ConversationMessage) -> Said:
    return Said(author="user" if message.author == "user" else "assistant", text=message.content,
                said_at=message.created_at, message_id=message.id)


def unlearned(db: Session, source: MemorySource) -> tuple[list[ConversationMessage], int]:
    """A conversation's usable messages, and where the ones not learned yet start."""
    messages = [message for message in ordered_messages(db, source.thread_id or "") if _usable(message)]
    ids = [message.id for message in messages]
    start = ids.index(source.learned_through) + 1 if source.learned_through in ids else 0
    return messages, start


def waiting_counts(db: Session, sources: list[MemorySource]) -> dict[str, int]:
    """How many of the user's messages in each conversation memory has not learned
    yet, by source id; one with none is left out. Counted in the database, by the
    rules `unlearned` reads them with, so no conversation is loaded to be counted."""
    if not sources:
        return {}

    def usable(message) -> ColumnElement[bool]:
        return and_(message.status != "streaming", func.btrim(message.content, " \t\n\r\x0b\x0c") != "")

    said, learned = aliased(ConversationMessage), aliased(ConversationMessage)
    return dict(db.execute(
        select(MemorySource.id, func.count(said.id))
        .join(said, said.thread_id == MemorySource.thread_id)
        .outerjoin(learned, and_(learned.id == MemorySource.learned_through,
                                 learned.thread_id == MemorySource.thread_id, usable(learned)))
        .where(MemorySource.id.in_([source.id for source in sources]), said.author == "user", usable(said),
               or_(learned.id.is_(None),
                   tuple_(said.created_at, said.id) > tuple_(learned.created_at, learned.id)))
        .group_by(MemorySource.id)
    ).tuples().all())


# --- Enqueueing (in the caller's transaction) ------------------------------------

def queue_conversation(db: Session, user_id: str, thread_id: str) -> queue.WorkItem:
    return queue.enqueue(
        db, kind=CONVERSATION, lane=lane(user_id), user_id=user_id, subject=thread_id,
        payload={"thread_id": thread_id}, dedupe_key=f"{CONVERSATION}:{thread_id}",
    )


def queue_statement(db: Session, source: MemorySource) -> queue.WorkItem:
    return queue.enqueue(
        db, kind=STATEMENT, lane=lane(source.user_id), user_id=source.user_id, subject=source.id,
        payload={"source_id": source.id}, dedupe_key=f"{STATEMENT}:{source.id}",
    )


def queue_summaries(db: Session, user_id: str) -> queue.WorkItem:
    return queue.enqueue(
        db, kind=SUMMARIES, lane=lane(user_id), user_id=user_id, subject=user_id,
        dedupe_key=f"{SUMMARIES}:{user_id}", delay=SUMMARY_DELAY,
    )


def _requested_reviews(db: Session, user_id: str) -> list[queue.WorkItem]:
    """The reviews this user asked for within the last day, whatever became of them."""
    since = utc_now() - REVIEW_EVERY
    # An item waiting when they ask becomes theirs, so it may be older than the asking.
    rows = db.query(queue.WorkItem).filter(
        queue.WorkItem.lane == lane(user_id), queue.WorkItem.kind == REVIEW,
        queue.WorkItem.created_at >= since - REVIEW_EVERY).all()
    return [item for item in rows if (asked := (item.payload or {}).get("requested_at"))
            and datetime.fromisoformat(asked) >= since]


def review_state(db: Session, user_id: str) -> tuple[bool, int]:
    """Whether a review the user asked for is under way, and how many more they may ask for today."""
    requested = _requested_reviews(db, user_id)
    under_way = any(item.status in (queue.PENDING, queue.RUNNING) for item in requested)
    return under_way, max(0, REVIEWS_REQUESTED - len(requested))


def review_spend(db: Session, *, client_address: str | None, user_id: str,
                 besides: str | None = None) -> float:
    """Dollars of reviews charged in the last day to this address, or to the
    account when the address is unknown; `besides` leaves one item out. A review
    asked for or under way counts before it has reported what it cost."""
    total = 0.0
    for item in _recent_reviews(db):
        mine = (item.payload or {}).get("client_address") == client_address if client_address else item.user_id == user_id
        if mine and item.id != besides:
            total += _review_charge(item)
    return total


def review_spend_by_address(db: Session, user_id: str) -> dict[str, float]:
    """Dollars of this account's reviews charged in the last day, by the address each was asked from."""
    spent: dict[str, float] = {}
    for item in _recent_reviews(db).filter(queue.WorkItem.user_id == user_id):
        if (address := (item.payload or {}).get("client_address")) and (dollars := _review_charge(item)):
            spent[address] = spent.get(address, 0.0) + dollars
    return spent


def _recent_reviews(db: Session):
    return db.query(queue.WorkItem).filter(
        queue.WorkItem.kind == REVIEW, queue.WorkItem.created_at > utc_now() - timedelta(days=1) - REVIEW_EVERY)


def _review_charge(item: queue.WorkItem) -> float:
    """What one review counts for toward the last day's allowance."""
    from app.config import UNMEASURED_REVIEW_DOLLARS

    payload = item.payload or {}
    if payload.get("skipped"):
        return 0.0
    if item.status in (queue.PENDING, queue.RUNNING):
        # One queued by itself may be a day off, and costs nothing until it runs.
        return UNMEASURED_REVIEW_DOLLARS if item.status == queue.RUNNING or payload.get("requested_at") else 0.0
    if item.finished_at is not None and item.finished_at > utc_now() - timedelta(days=1):
        return float(payload.get("dollars", UNMEASURED_REVIEW_DOLLARS))
    return 0.0


def review_affordable(db: Session, *, client_address: str | None, user_id: str, besides: str | None = None) -> bool:
    """Whether the address's allowance for the day still has room for a review."""
    from app.config import DAILY_CHAT_DOLLARS_PER_ADDRESS

    spent = chat_spent(db, client_address, user_id) + review_spend(
        db, client_address=client_address, user_id=user_id, besides=besides)
    return spent < DAILY_CHAT_DOLLARS_PER_ADDRESS


def request_review(db: Session, user_id: str, client_address: str | None = None) -> str | None:
    """The user asks for a review now. Returns why not, when they have had the
    day's share or the day's allowance is spent. The caller commits."""
    under_way, left = review_state(db, user_id)
    if under_way:
        return None  # Already on its way.
    if not left:
        return "Attune has tidied as often as it can today. Try again tomorrow."
    if not review_affordable(db, client_address=client_address, user_id=user_id):
        return "Today's demo allowance for your connection is used up. Try again tomorrow."
    queue.enqueue(
        db, kind=REVIEW, lane=lane(user_id), user_id=user_id, subject=user_id,
        payload={"requested_at": utc_now().isoformat(), "client_address": client_address},
        dedupe_key=f"{REVIEW}:{user_id}",
    )
    return None


def correction_refused(db: Session, user_id: str, client_address: str | None = None) -> str | None:
    """Why the user may not correct a belief now, if they may not: the day's
    allowance is spent, or they have corrected as often as a day allows. Tidying a
    correction is model work nobody is charged for, so it is counted instead."""
    if not review_affordable(db, client_address=client_address, user_id=user_id):
        return "Today's demo allowance for your connection is used up. Try again tomorrow."
    made = db.query(func.count(MemorySource.id)).filter(
        MemorySource.user_id == user_id, MemorySource.kind == CORRECTION,
        MemorySource.created_at > utc_now() - timedelta(days=1)).scalar()
    if made >= CORRECTIONS_A_DAY:
        return "That is as many corrections as Attune takes in a day. Try again tomorrow."
    return None


def queue_review(db: Session, user_id: str, *, now: bool = False) -> queue.WorkItem | None:
    """A review once memory has been quiet a while, and no sooner than a day after
    the last. None while the user has asked for one themselves within the day."""
    if not now and _requested_reviews(db, user_id):
        return None
    last = db.query(func.max(queue.WorkItem.finished_at)).filter(
        queue.WorkItem.lane == lane(user_id), queue.WorkItem.kind == REVIEW,
        queue.WorkItem.status == queue.COMPLETED).scalar()
    due = utc_now() + REVIEW_QUIET
    if last is not None:
        due = max(due, last + REVIEW_EVERY)
    return queue.enqueue(
        db, kind=REVIEW, lane=lane(user_id), user_id=user_id, subject=user_id,
        payload={"client_address": last_address(db, user_id)},
        dedupe_key=f"{REVIEW}:{user_id}", delay=timedelta(0) if now else due - utc_now(),
    )


def queue_source(db: Session, source: MemorySource) -> queue.WorkItem:
    if source.kind == CONVERSATION_KIND:
        return queue_conversation(db, source.user_id, source.thread_id or "")
    return queue_statement(db, source)


def retry_failed(db: Session, user_id: str) -> int:
    """Put this user's failed memory work back in the queue. The caller commits.

    Failed items start over with fresh attempts; a failed source with no item of
    its own gets a new one.
    """
    retried = db.query(queue.WorkItem).filter(
        queue.WorkItem.lane == lane(user_id), queue.WorkItem.status == queue.FAILED,
    ).update({queue.WorkItem.status: queue.PENDING, queue.WorkItem.attempts: 0,
              queue.WorkItem.run_after: utc_now(), queue.WorkItem.finished_at: None},
             synchronize_session=False)
    if retried:
        notify(db, queue.WORK_QUEUED)
    waiting = {row[0] for row in db.query(queue.WorkItem.subject).filter(
        queue.WorkItem.lane == lane(user_id),
        queue.WorkItem.status.in_((queue.PENDING, queue.RUNNING)),
    )}
    for source in db.scalars(select(MemorySource).where(
            MemorySource.user_id == user_id, MemorySource.status == FAILED)):
        source.status = PENDING
        if (source.thread_id or source.id) not in waiting:
            queue_source(db, source)
            retried += 1
    return int(retried)


# --- Learning ----------------------------------------------------------------------

def _not_counting(db: Session, source: MemorySource, reading: Reading) -> list[dict[str, str]]:
    """What the user removed, by the message in this reading it no longer counts from."""
    shorts = {said.message_id or "": short for short, said in reading.short_ids().items()}
    return [{"message": shorts[row.message_id], "belief": row.text}
            for row in db.scalars(select(MemoryExclusion).where(MemoryExclusion.source_id == source.id))
            if row.message_id in shorts]


async def _learn(db: Session, source: MemorySource, reading: Reading, learning: dict[str, Said],
                 replies: list[str]) -> int:
    """Place what is being learned and write it. Returns how many changes it made."""
    graph = load(db, source.user_id)
    looked_up, passages = turn_lookups(db, source.user_id, replies) if replies else ([], [])
    placement = await place(db, source.user_id, graph, reading, learning,
                            looked_up=looked_up, passages=passages)
    applied = await apply(db, source.user_id, source, placement)
    if applied.stale:
        queue_summaries(db, source.user_id)
    return applied.changes


def _settle(db: Session, source: MemorySource, learned_something: bool) -> None:
    taught = learned_something or db.query(MemoryChange.id).filter(MemoryChange.source_id == source.id).first()
    source.status = LEARNED if taught else EMPTY
    source.error, source.learned_at = None, utc_now()


def _fail(db: Session, source_id: str, error: Exception) -> None:
    db.rollback()
    source = db.get(MemorySource, source_id)
    if source is not None:
        source.status, source.error = FAILED, str(error)[:2000]
        db.commit()


@traced("memory.pass")
async def _learn_pass(db: Session, item: queue.Claimed, source: MemorySource, reading: Reading,
                      replies: list[str], through: str) -> None:
    """Gate the new messages of one pass, place what passes, and move the cursor. Commits."""
    source_id = source.id
    record_content(input=[said.text for said in reading.new_from_user().values()])
    try:
        scores = await gate(reading)
        learning = {short: said for short, said in reading.new_from_user().items()
                    if scores.get(short, 0.0) >= KEEP}
        changes = await _learn(db, source, reading, learning, replies) if learning else 0
        if changes:
            queue_review(db, source.user_id)
        source.learned_through = through
        _settle(db, source, changes > 0)
        db.commit()
        record_content(output={"kept": len(learning), "changes": changes})
        event("memory.ingestion_completed", user_id=item.user_id, source_id=source_id,
              messages=len(reading.new_from_user()), kept=len(learning), changes=changes)
    except Exception as error:
        _fail(db, source_id, error)
        event("memory.ingestion_failed", level=logging.ERROR, user_id=item.user_id,
              source_id=source_id, error_type=type(error).__name__)
        raise


async def learn_conversation(item: queue.Claimed) -> None:
    """Learn what the conversation has said since memory last learned it, a pass at a time."""
    thread_id = item.payload["thread_id"]
    while True:
        with SessionLocal() as db:
            if lock_thread(db, item.user_id or "", thread_id) is None:
                return  # Deleted while this waited.
            source = conversation_source(db, item.user_id or "", thread_id)
            messages, start = unlearned(db, source)
            said = [index for index in range(start, len(messages)) if messages[index].author == "user"]
            if not said:
                if source.status in (PENDING, LEARNING):
                    _settle(db, source, False)
                db.commit()
                return
            # Through the reply to the last message learned in this pass.
            last = said[:PASSAGE_MESSAGES][-1]
            end = next((index for index in range(last + 1, len(messages)) if messages[index].author == "user"),
                       len(messages)) - 1
            first = max(0, start - CONTEXT_MESSAGES)
            window = messages[first:end + 1]
            reading = Reading(conversation=[_said(message) for message in window], first_new=start - first)
            reading.not_counting = _not_counting(db, source, reading)
            replies = [message.id for message in window[start - first:] if message.author != "user"]
            source.status = LEARNING
            db.commit()
            await _learn_pass(db, item, source, reading, replies, window[-1].id)


# What placement is told about a correction, which the belief already reads as.
TIDY_HINT = (
    "The user corrected a memory belief that read \"{was}\". It now reads exactly as they typed it, "
    "\"{now}\". Revise that belief into one short note in memory's usual form that says the same "
    "and no more. If what they typed holds more than one fact, the revised belief keeps one and each "
    "other is an add under the same parents."
)


async def learn_statement(item: queue.Claimed) -> None:
    """Learn one thing the user typed straight to memory."""
    with SessionLocal() as db:
        source = db.get(MemorySource, item.payload["source_id"])
        if source is None or source.status in (LEARNED, EMPTY):
            return
        said = Said(author="user", text=source.text or "", said_at=source.created_at)
        # The belief a correction was written into, while it is still there.
        corrected = db.get(MemoryNode, source.corrects_node_id) if source.corrects_node_id else None
        if corrected is not None and corrected.user_id != source.user_id:
            corrected = None
        if corrected is not None and corrected.text != source.text:
            # Corrected again since: the later correction tidies it.
            _settle(db, source, True)
            db.commit()
            return
        hint = None
        if corrected is not None:
            hint = TIDY_HINT.format(was=source.corrects_text or "", now=corrected.text)
        elif source.corrects_text:
            hint = f"This corrects what memory held: \"{source.corrects_text}\". Revise that belief."
        reading = Reading(conversation=[said], hint=hint)
        record_content(input=said.text)
        source.status = LEARNING
        db.commit()
        try:
            if corrected is not None:
                # Searchable as it reads now, whatever placement makes of it.
                [corrected.embedding] = await embed([corrected.text], "document")
            also = [source.corrects_node_id] if source.corrects_node_id else []
            graph = load(db, source.user_id)
            placement = await place(db, source.user_id, graph, reading, {"m1": said}, looked_up=also)
            applied = await apply(db, source.user_id, source, placement)
            if applied.stale:
                queue_summaries(db, source.user_id)
            # The correction itself is a change, even when there was nothing to tidy.
            if corrected is not None and not db.query(MemoryChange.id).filter(
                    MemoryChange.source_id == source.id).first():
                db.add(MemoryChange(id=str(uuid.uuid7()), source_id=source.id, message_id="", op=CHANGED,
                                    text=source.text or "", node_id=corrected.id, created_at=utc_now()))
                applied.changes += 1
            if applied.changes:
                queue_review(db, source.user_id)
            _settle(db, source, applied.changes > 0)
            db.commit()
            record_content(output={"changes": applied.changes})
            event("memory.ingestion_completed", user_id=item.user_id, source_id=source.id, changes=applied.changes)
        except Exception as error:
            _fail(db, item.payload["source_id"], error)
            event("memory.ingestion_failed", level=logging.ERROR, user_id=item.user_id,
                  source_id=item.payload["source_id"], error_type=type(error).__name__)
            raise


async def refresh_summaries(item: queue.Claimed) -> None:
    with SessionLocal() as db:
        await refresh(db, item.user_id or "")
        db.commit()


async def review_memory(item: queue.Claimed) -> None:
    """Review the user's memory if the day's allowance has room, and record what it cost."""
    user_id = item.user_id or ""
    with SessionLocal() as db:
        if review_affordable(db, client_address=item.payload.get("client_address"), user_id=user_id,
                             besides=item.id):
            _, dollars = await review(db, user_id)
            outcome: dict = {"dollars": dollars}
        else:
            outcome = {"skipped": True}
            event("memory.review_skipped", user_id=user_id, reason="allowance")
        if (row := db.get(queue.WorkItem, item.id)) is not None:
            row.payload = {**item.payload, **outcome}
        db.commit()


HANDLERS = {CONVERSATION: learn_conversation, STATEMENT: learn_statement, SUMMARIES: refresh_summaries,
            REVIEW: review_memory}


# --- The worker ------------------------------------------------------------------

WORKER_ID = f"memory:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
LEASE = timedelta(seconds=60)
# A pass takes a couple of model calls, and a long conversation several passes;
# this bounds a stuck one.
ITEM_TIMEOUT_SECONDS = 20 * 60
# Pause after the loop itself fails, e.g. while Postgres is briefly unreachable.
LOOP_ERROR_DELAY_SECONDS = 5
# A finished item is read back for a day or two at most (what reviews cost, how
# many were asked for), so older ones only make the table longer to search.
KEEP_COMPLETED = timedelta(days=7)
PRUNE_EVERY_SECONDS = 60 * 60
# How many items this worker runs at once. The queue keeps a user's own items one
# at a time, so these are always different users'.
CONCURRENT_ITEMS = 4


async def _run(claimed: queue.Claimed) -> str:
    """One item, as one trace named for its kind. A conversation's is part of that
    conversation's session, beside its chat turns."""
    with operation(claimed.kind):
        record_turn_content(session_id=claimed.payload.get("thread_id"))
        return await queue.work(claimed, WORKER_ID, HANDLERS[claimed.kind],
                                lease=LEASE, timeout=ITEM_TIMEOUT_SECONDS)


async def _job(claimed: queue.Claimed) -> None:
    """One claimed item through to its end, as a task of its own beside the others."""
    job_context.set(claimed.id)
    try:
        outcome = await _run(claimed)
        event("memory.job_finished", level=logging.ERROR if outcome == "failed" else logging.INFO,
              user_id=claimed.user_id, kind=claimed.kind, outcome=outcome, attempt=claimed.attempts)
    except Exception as error:
        # Settling the item failed too; its lease lapses and it is recovered.
        event("memory.job_record_error", level=logging.ERROR, error_type=type(error).__name__)
        await asyncio.sleep(LOOP_ERROR_DELAY_SECONDS)


async def main() -> None:
    configure_memory_logging()
    configure_tracing("attune-memory-worker")
    wakeups.start(queue.WORK_QUEUED)
    jobs: set[asyncio.Task[None]] = set()
    pruned_at: float | None = None
    try:
        with wakeups.subscription(queue.WORK_QUEUED) as queued:
            while True:
                jobs = {job for job in jobs if not job.done()}
                if len(jobs) >= CONCURRENT_ITEMS:
                    await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                    continue
                try:
                    await asyncio.to_thread(queue.recover_expired)
                    if pruned_at is None or time.monotonic() - pruned_at >= PRUNE_EVERY_SECONDS:
                        pruned_at = time.monotonic()
                        if pruned := await asyncio.to_thread(queue.prune_completed, KINDS, KEEP_COMPLETED):
                            event("memory.queue_pruned", count=pruned)
                    claimed = await asyncio.to_thread(queue.claim, WORKER_ID, KINDS, LEASE)
                except Exception as error:
                    # Keep polling through transient failures (a Postgres restart, its
                    # hostname not resolving yet) instead of exiting the worker.
                    event("memory.claim_error", level=logging.ERROR, error_type=type(error).__name__)
                    await asyncio.sleep(LOOP_ERROR_DELAY_SECONDS)
                    continue
                if claimed is None:
                    # New work, or an item ending: that frees its user's lane for their next.
                    woken = asyncio.create_task(wait_for_wakeup(queued, queue.IDLE_SECONDS))
                    await asyncio.wait({woken, *jobs}, return_when=asyncio.FIRST_COMPLETED)
                    woken.cancel()
                    continue
                jobs.add(asyncio.create_task(_job(claimed)))
    finally:
        # Stopping: the items in flight stop too, and their leases lapse so they are picked up again.
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await wakeups.stop()
        await shutdown_tracing()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # A requested stop (Ctrl+C, dev reload) is not a crash.
