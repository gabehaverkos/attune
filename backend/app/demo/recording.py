"""Recording the example account: Daniel Carter says his lines, and Attune does the rest.

Nothing here writes a reply, a card or a belief. Each line goes in as a message
from an ordinary account; the API's worker answers it, the memory worker learns
from it, and the cards the assistant proposes are approved the way a click
would. So the stack has to be running, and a recording costs what that many
turns cost.

His conversations happened over a couple of weeks, so once one has settled,
everything it wrote is moved back to when he had it. What goes on the calendar
stays where it was put.

    uv run python -m app.demo.recording play        # record Daniel Carter from nothing
    uv run python -m app.demo.recording snapshot    # write daniel_carter.json from the recording
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time as clock, timedelta
import sys
import time
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import case, DateTime, func, select

from app.chat import runs
from app.chat.artifacts import accept_artifact, ConversationArtifact
from app.chat.threads import ConversationMessage
from app.core.auth import User
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.demo.snapshot import EVENT_TIMES, SNAPSHOT, TABLES, owned, table, take, write
from app.memory import work
from app.memory.sources import UNRESOLVED, MemorySource
from app.core import queue
from app.core.queue import notify

DANIEL = "seed-daniel-carter"
ZONE = "America/New_York"


# --- What he says ------------------------------------------------------------------

@dataclass(frozen=True)
class Conversation:
    days_ago: int
    at: str
    says: tuple[str, ...]


def _day(day: date) -> str:
    return f"{day:%A, %B} {day.day}"


def _coming(today: date, weekday: int, *, after: int = 2) -> date:
    """The first of a weekday (Monday is 0) at least this many days off."""
    start = today + timedelta(days=after)
    return start + timedelta(days=(weekday - start.weekday()) % 7)


def conversations(today: date) -> tuple[Conversation, ...]:
    dentist = _coming(today, 1)
    dinner = _coming(today, 5)
    day_off = _coming(today, 4, after=8)
    return (
        Conversation(16, "19:40", (
            "Hi, I'm Daniel. I'm a product designer at a logistics software company called Freightline, "
            "in Columbus. I live with my wife Maria and our beagle, Biscuit.",
            "Mostly I want help keeping my weeks from getting away from me. I'm training for my first "
            "half marathon and I keep skipping runs when work gets busy.",
            "I do my best work in the mornings, so I try to keep those free for design. Afternoons are "
            "for meetings, and I don't touch email after 6.",
            "One more thing, in case food ever comes up: I'm vegetarian, and Maria is allergic to shellfish.",
        )),
        Conversation(13, "08:15", (
            "Can you set up my calendar? I sleep 11pm to 6:30am on weeknights, and midnight to 8am on "
            "Friday and Saturday nights.",
            "Work is 9 to 5:30, Monday through Friday. I'm in the office Tuesdays and Thursdays and at "
            "home the rest of the week.",
            "Add my runs too: Tuesday and Thursday at 6:45am for 45 minutes, and a long run on Sunday "
            "at 8am for an hour and a half.",
        )),
        Conversation(9, "12:30", (
            "My manager Priya wants the redesigned shipment tracking page ready for a customer pilot in "
            "early December. Dev, the engineer I'm paired with, thinks the map view is the risky part.",
            "I'd rather cut the map view than slip the pilot, and Priya agrees.",
            "The half marathon is the Cap City Half, on April 25. Maria is running the quarter.",
            "My sister Elena lives in Denver. We talk most Sunday evenings.",
        )),
        Conversation(5, "21:05", (
            "I've had shin pain for a week. My physio, Dr. Okafor, told me to swap the Thursday run for "
            "cycling until it settles, so can you change Thursday's run to a 45 minute ride at the same time?",
            "Maria and I are saving for a trip to Lisbon next September, for our tenth anniversary.",
            "Also, I've stopped drinking coffee after noon. It was wrecking my sleep.",
        )),
        Conversation(2, "07:50", (
            f"Morning. I have a dentist appointment on {_day(dentist)} at 2pm, at Short North Dental. "
            "It takes about an hour.",
            f"Dinner with Maria's parents on {_day(dinner)} at 7pm at their place in Worthington, "
            "probably two hours.",
            f"I'm taking {_day(day_off)} off work. Maria and I are driving down to Hocking Hills for the day.",
            f"What does {dentist:%A} look like now?",
        )),
    )


# --- Saying it ---------------------------------------------------------------------

def _wait(what: str, done: Callable[[], bool], seconds: int = 900) -> None:
    deadline = time.monotonic() + seconds
    while not done():
        if time.monotonic() > deadline:
            raise TimeoutError(f"Gave up waiting for {what}.")
        time.sleep(1)


def _run_status(run_id: str) -> str:
    with SessionLocal() as db:
        return db.query(runs.ConversationRun.status).filter(runs.ConversationRun.id == run_id).scalar()


def _approve_ready(thread_id: str) -> int:
    """Approve every card in the conversation that is ready, as Daniel would."""
    with SessionLocal() as db:
        ready = [row.id for row in db.query(ConversationArtifact.id).filter(
            ConversationArtifact.thread_id == thread_id, ConversationArtifact.status == "ready")]
    for artifact_id in ready:
        with SessionLocal() as db:
            card = accept_artifact(db, user_id=DANIEL, artifact_id=artifact_id, timezone_name=ZONE)
            print(f"    [{card.type}] {card.title}: {card.status}"
                  + (f" ({card.error.message})" if card.error else ""))
    return len(ready)


def say(thread_id: str, text: str) -> str:
    """Send one message and see its reply through, approving what it proposes.
    Returns the conversation it is in."""
    print(f"\nJohn: {text}")
    with SessionLocal() as db:
        turn = runs.accept_turn(db, user_id=DANIEL, thread_id=thread_id, content=text,
                                client_request_id=str(uuid.uuid4()), timezone_name=ZONE)
    while True:
        _wait("the reply", lambda: _run_status(turn.run_id) not in ("queued", "running"))
        status = _run_status(turn.run_id)
        if status in ("failed", "canceled"):
            raise RuntimeError(f"The reply ended as {status}.")
        # A card the reply waits on resumes it once approved; one that needs more
        # than Daniel said stays as it is, and his next message moves on.
        if not _approve_ready(turn.thread_id):
            break
    with SessionLocal() as db:
        reply = db.get(ConversationMessage, turn.assistant_message_id)
        print(f"Attune: {reply.content if reply else ''}")
    _wait("memory to learn it", lambda: not _waiting(work.CONVERSATION, work.STATEMENT))
    return turn.thread_id


# --- Memory's work -----------------------------------------------------------------

def _waiting(*kinds: str) -> int:
    """How much of Daniel's memory work is still to do. A piece that failed for good stops the recording."""
    with SessionLocal() as db:
        lane = db.query(queue.WorkItem).filter(queue.WorkItem.lane == work.lane(DANIEL))
        if failed := lane.filter(queue.WorkItem.status == queue.FAILED).first():
            raise RuntimeError(f"Memory's {failed.kind} failed: {failed.last_error}")
        waiting = lane.filter(queue.WorkItem.status.in_((queue.PENDING, queue.RUNNING)))
        if kinds:
            waiting = waiting.filter(queue.WorkItem.kind.in_(kinds))
        return waiting.count()


def settle() -> None:
    """Have memory finish with the conversation now: its summaries, and the look over
    the graph it would otherwise leave for a quiet moment."""
    with SessionLocal() as db:
        work.queue_review(db, DANIEL, now=True)
        db.commit()

    def done() -> bool:
        if not _waiting():
            return True
        with SessionLocal() as db:
            # Summaries and retries wait out a delay; nothing here is worth waiting for.
            hurried = db.query(queue.WorkItem).filter(
                queue.WorkItem.lane == work.lane(DANIEL), queue.WorkItem.status == queue.PENDING,
                queue.WorkItem.run_after > utc_now(),
            ).update({queue.WorkItem.run_after: utc_now()}, synchronize_session=False)
            if hurried:
                notify(db, queue.WORK_QUEUED)
            db.commit()
        return False

    _wait("memory to settle", done)


def move_back(since: datetime, by: timedelta) -> None:
    """Move everything written since then back by that much: the conversation, and
    all that came of it."""
    with SessionLocal() as db:
        # Children first: writing a routine's window touches the routine.
        for name, _, _ in reversed(TABLES):
            rows = table(name)
            written = [column for column in rows.columns if isinstance(column.type, DateTime)
                       and not (name == "interfaces_calendar_events" and column.name in EVENT_TIMES)]
            if not written:
                continue
            db.execute(rows.update().where(owned(name, DANIEL)).values({
                column.name: case((column >= since, column - by), else_=column) for column in written}))
        # A message also keeps when it was sent as text, which is what orders a conversation.
        messages = table("conversation_messages")
        for message_id, sent in db.execute(select(messages.c.id, messages.c.timestamp).where(
                owned("conversation_messages", DANIEL))).all():
            if (sent_at := datetime.fromisoformat(sent)) >= since:
                db.execute(messages.update().where(messages.c.id == message_id).values(
                    timestamp=(sent_at - by).isoformat(), updated_at=messages.c.updated_at))
        db.commit()


# --- The recording -----------------------------------------------------------------

def start_over() -> None:
    """Daniel as a new account, with nothing left of the last recording."""
    with SessionLocal() as db:
        if db.query(queue.WorkItem).filter(
                queue.WorkItem.lane == work.lane(DANIEL), queue.WorkItem.status == queue.RUNNING).count():
            raise RuntimeError("The memory worker is busy with the last recording; retry shortly.")
        db.query(queue.WorkItem).filter(queue.WorkItem.lane == work.lane(DANIEL)).delete(synchronize_session=False)
        db.query(User).filter(User.id == DANIEL).delete(synchronize_session=False)
        db.add(User(id=DANIEL, name="Daniel Carter", email="daniel.carter@seed.attune.invalid"))
        db.commit()


def play() -> None:
    start_over()
    today = datetime.now(ZoneInfo(ZONE)).date()
    for conversation in conversations(today):
        began = utc_now()
        happened = datetime.combine(today - timedelta(days=conversation.days_ago),
                                    clock.fromisoformat(conversation.at), ZoneInfo(ZONE))
        print(f"\n=== {happened:%A, %B %d at %H:%M} ===")
        thread_id = "draft"
        for text in conversation.says:
            thread_id = say(thread_id, text)
        settle()
        move_back(began, began - happened)
    with SessionLocal() as db:
        beliefs = db.query(func.count()).select_from(table("memory_nodes")).filter(owned("memory_nodes", DANIEL)).scalar()
    print(f"\nRecorded. Memory holds {beliefs} nodes. Take the snapshot with: "
          "python -m app.demo.recording snapshot")


def snapshot() -> None:
    with SessionLocal() as db:
        if db.get(User, DANIEL) is None:
            raise RuntimeError("There is no recording yet; play one first.")
        unlearned = db.query(MemorySource).filter(
            MemorySource.user_id == DANIEL, MemorySource.status.in_(UNRESOLVED)).count()
        replying = db.query(runs.ConversationRun).filter(
            runs.ConversationRun.user_id == DANIEL,
            runs.ConversationRun.status.in_(runs.ACTIVE_RUN_STATUSES)).count()
        if unlearned or replying or _waiting(work.CONVERSATION, work.STATEMENT, work.SUMMARIES):
            raise RuntimeError("The recording has not settled: a reply or memory's work is unfinished.")
        taken = take(db, DANIEL, timezone_name=ZONE)
    write(taken)
    print(f"Wrote {SNAPSHOT} ({SNAPSHOT.stat().st_size // 1024} KB): "
          + ", ".join(f"{len(rows)} {name}" for name, rows in taken["tables"].items() if rows))


def main() -> int:
    parser = argparse.ArgumentParser(description="Record the example account for the seeded demo.")
    parser.add_argument("command", choices=("play", "snapshot"))
    args = parser.parse_args()
    # The transcript has typographic punctuation, which a Windows console cannot print by default.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    {"play": play, "snapshot": snapshot}[args.command]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
