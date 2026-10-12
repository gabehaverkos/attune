"""The snapshot of the example account, and copying it into a visitor's.

A snapshot is every row the example person owns, as JSON: their conversations
and the cards in them, their calendar, and the memory learned from what they
said, embeddings included, so a copy costs no model call. Runs are left out: a
copied reply was never the visitor's to retry, and never spent their allowance.

A copy is the visitor's own. Every id is made anew, and everything moves forward
by the whole weeks since the snapshot was taken, so the example's week stays
recent however old the file is: when things were written, what is on the
calendar around then, and those same dates wherever a conversation or memory
mentions them in words. Weekdays stay as they were. A date further off (a race
next spring) stays where it is, on the calendar and in words alike. Events keep
their clock time in the visitor's timezone, like routines, which have none of
their own.
"""

from __future__ import annotations

from array import array
import base64
from datetime import date, datetime, timedelta, UTC
from functools import cache
import json
from pathlib import Path
import re
from typing import Any
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Date, DateTime, Table, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.auth import get_current_user, User
from app.core.database import Base, get_db, load_models
from app.core.time import normalize_timezone, utc_now

SNAPSHOT = Path(__file__).with_name("daniel_carter.json")

# Every table a user's rows are in, parents before children: the column that says
# whose a row is, and the table that column points into (None for the user).
TABLES: tuple[tuple[str, str, str | None], ...] = (
    ("conversation_threads", "user_id", None),
    ("conversation_messages", "thread_id", "conversation_threads"),
    ("conversation_artifacts", "user_id", None),
    ("conversation_artifact_revisions", "artifact_id", "conversation_artifacts"),
    ("conversation_artifact_execution_attempts", "artifact_id", "conversation_artifacts"),
    ("conversation_message_artifact_events", "artifact_id", "conversation_artifacts"),
    ("interfaces_preferences", "user_id", None),
    ("interfaces_calendar_events", "user_id", None),
    ("interfaces_calendar_routines", "user_id", None),
    ("interfaces_calendar_routine_windows", "routine_id", "interfaces_calendar_routines"),
    ("interfaces_calendar_routine_exceptions", "window_id", "interfaces_calendar_routine_windows"),
    ("interfaces_calendar_occurrence_notes", "window_id", "interfaces_calendar_routine_windows"),
    ("memory_sources", "user_id", None),
    ("memory_nodes", "user_id", None),
    ("memory_edges", "user_id", None),
    ("memory_node_evidence", "node_id", "memory_nodes"),
    ("memory_changes", "source_id", "memory_sources"),
    ("memory_exclusions", "user_id", None),
)
# When something is on the calendar rather than when a row was written: a clock
# time in the event's own timezone.
EVENT_TIMES = ("starts_at", "ends_at")


@cache
def table(name: str) -> Table:
    load_models()
    return Base.metadata.tables[name]


def owned(name: str, user_id: str) -> ColumnElement[bool]:
    """The rows of a table that are this user's."""
    owner, parent = next((owner, parent) for table_name, owner, parent in TABLES if table_name == name)
    column = table(name).c[owner]
    if parent is None:
        return column == user_id
    return column.in_(select(table(parent).c.id).where(owned(parent, user_id)))


@cache
def _stored(name: str) -> list[Any]:
    """A table's columns that hold what was written, not what Postgres works out."""
    return [column for column in table(name).columns if column.computed is None]


# --- Taking a snapshot -------------------------------------------------------------

def _written(column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, Vector):
        return base64.b64encode(array("f", [float(number) for number in value]).tobytes()).decode()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def take(db: Session, user_id: str, *, timezone_name: str) -> dict[str, Any]:
    """Everything the user owns, as it is written to the file."""
    user = db.get(User, user_id)
    assert user is not None
    tables = {}
    for name, _, _ in TABLES:
        columns = _stored(name)
        rows = db.execute(select(*columns).where(owned(name, user_id))).all()
        tables[name] = [{column.name: _written(column, value) for column, value in zip(columns, row)}
                        for row in rows]
    return {"name": user.name, "timezone": timezone_name, "taken_at": utc_now().isoformat(), "tables": tables}


def write(snapshot: dict[str, Any]) -> None:
    SNAPSHOT.write_text(json.dumps(snapshot, indent=1) + "\n", encoding="utf-8")


# --- Copying it ---------------------------------------------------------------------

ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# A date as data writes it, which an id's digits never spell.
WRITTEN_DATE = re.compile(r"(?<![0-9a-f-])(\d{4})-(\d{2})-(\d{2})(?![\d-])")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
# A date as people write it: "October 13", "Oct. 13th".
SPOKEN_DATE = re.compile(
    r"\b(" + "|".join(MONTHS) + "|" + "|".join(month[:3] for month in MONTHS) + r"|Sept)(\.?) (\d{1,2})(st|nd|rd|th)?\b(?!:\d)")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# And without its month: "Saturday the 17th".
SPOKEN_DAY = re.compile(r"\b(" + "|".join(WEEKDAYS) + r") the (\d{1,2})(?:st|nd|rd|th)\b")
# Dates further than this from the snapshot are not part of its weeks: a birthday,
# a race next spring. They stay as they were.
REACH = timedelta(days=45)


def _ordinal(day: int) -> str:
    return "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


class _Copy:
    """One copy: the new ids, and how far everything moves."""

    def __init__(self, snapshot: dict[str, Any], user_id: str, timezone_name: str | None):
        self.user_id = user_id
        self.taken = datetime.fromisoformat(snapshot["taken_at"])
        self.moved = timedelta(weeks=max(0, (utc_now() - self.taken) // timedelta(weeks=1)))
        self.timezone = timezone_name
        # New ids keep the order of the old: conversations are listed by theirs.
        old = sorted({row["id"] for rows in snapshot["tables"].values() for row in rows if "id" in row})
        self.ids = {old_id: str(uuid.uuid7()) for old_id in old}

    def day(self, day: date) -> date:
        """A date moved with the snapshot's weeks, when it is one of them."""
        return day + self.moved if abs(day - self.taken.date()) <= REACH else day

    def _written_date(self, match: re.Match[str]) -> str:
        try:
            return self.day(date(*(int(part) for part in match.groups()))).isoformat()
        except ValueError:
            return match.group()

    def _spoken_date(self, match: re.Match[str]) -> str:
        month, stop, number, ordinal = match.groups()
        index = next(at for at, name in enumerate(MONTHS, start=1) if name.startswith(month[:3]))
        days = []
        for year in (self.taken.year - 1, self.taken.year, self.taken.year + 1):
            try:
                days.append(date(year, index, int(number)))
            except ValueError:
                pass
        if not days:
            return match.group()
        day = self.day(min(days, key=lambda day: abs(day - self.taken.date())))
        name = MONTHS[day.month - 1]
        if month not in MONTHS:
            name = "Sept" if month == "Sept" and day.month == 9 else name[:3]
        return f"{name}{stop} {day.day}{_ordinal(day.day) if ordinal else ''}"

    def _spoken_day(self, match: re.Match[str]) -> str:
        weekday, number = match.group(1), int(match.group(2))
        near = (self.taken.date() + timedelta(days=offset)
                for offset in sorted(range(-REACH.days, REACH.days + 1), key=abs))
        day = next((day for day in near if day.day == number and WEEKDAYS[day.weekday()] == weekday), None)
        if day is None:
            return match.group()
        day += self.moved
        return f"{weekday} the {day.day}{_ordinal(day.day)}"

    def text(self, value: str) -> str:
        value = ID.sub(lambda match: self.ids.get(match.group(), match.group()), value)
        if not self.moved:
            return value
        value = WRITTEN_DATE.sub(self._written_date, value)
        return SPOKEN_DAY.sub(self._spoken_day, SPOKEN_DATE.sub(self._spoken_date, value))

    def _event_time(self, value: datetime, zone: str) -> datetime:
        local = value.astimezone(ZoneInfo(zone)).replace(tzinfo=None)
        local = datetime.combine(self.day(local.date()), local.time())
        return local.replace(tzinfo=ZoneInfo(self.timezone or zone)).astimezone(UTC)

    def row(self, name: str, written: dict[str, Any]) -> dict[str, Any]:
        row = {}
        for column in _stored(name):
            value = written.get(column.name)
            if column.name == "user_id":
                value = self.user_id
            elif value is None:
                pass
            elif isinstance(column.type, Vector):
                value = list(array("f", base64.b64decode(value)))
            elif isinstance(column.type, DateTime):
                value = datetime.fromisoformat(value)
                if name == "interfaces_calendar_events" and column.name in EVENT_TIMES:
                    value = self._event_time(value, written["timezone"])
                else:
                    value += self.moved
            elif isinstance(column.type, Date):
                value = self.day(date.fromisoformat(value))
            elif isinstance(column.type, JSON):
                value = json.loads(self.text(json.dumps(value)))
            elif isinstance(value, str):
                value = self.text(value)
            row[column.name] = value
        if name == "interfaces_calendar_events" and self.timezone:
            row["timezone"] = self.timezone
        return row


def has_anything(db: Session, user_id: str) -> bool:
    """Whether the account has a conversation or anything in memory yet."""
    return any(db.execute(select(table(name).c.id).where(owned(name, user_id)).limit(1)).first()
               for name in ("conversation_threads", "memory_sources", "memory_nodes"))


def copy(db: Session, user: User, snapshot: dict[str, Any], *, timezone_name: str | None = None) -> None:
    """Give the user a copy of the snapshot. The caller commits."""
    copying = _Copy(snapshot, user.id, timezone_name)
    for name, _, _ in TABLES:
        rows = [copying.row(name, written) for written in snapshot["tables"].get(name, [])]
        if not rows:
            continue
        if name == "conversation_artifacts":
            # A card can name the one that replaced it, which may not be in yet.
            replaced = {row["id"]: row["superseded_by_artifact_id"] for row in rows
                        if row["superseded_by_artifact_id"]}
            db.execute(table(name).insert(), [{**row, "superseded_by_artifact_id": None} for row in rows])
            for artifact_id, by in replaced.items():
                # Named so that writing it does not count as the card changing.
                db.execute(table(name).update().where(table(name).c.id == artifact_id).values(
                    superseded_by_artifact_id=by, updated_at=table(name).c.updated_at))
        else:
            db.execute(table(name).insert(), rows)
    if snapshot.get("name"):
        user.name = snapshot["name"]


# --- Routes -------------------------------------------------------------------------

router = APIRouter()


@router.post("/api/demo/seed", status_code=204)
def seed_demo(
    # The device's timezone when the web app knows it; unlike the usual header, it
    # is left out rather than guessed.
    x_user_timezone: str | None = Header(default=None, alias="X-User-Timezone"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Start a new demo account as the example person's. Asking again changes nothing."""
    if not current_user.is_anonymous:
        raise HTTPException(status_code=403, detail="Only a demo account can start seeded.")
    if not SNAPSHOT.exists():
        raise HTTPException(status_code=404, detail="There is no seeded demo to start from.")
    # Serializes a repeated request, so the account is never seeded twice.
    user = db.query(User).filter(User.id == current_user.id).with_for_update().one()
    if has_anything(db, user.id):
        return
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    copy(db, user, snapshot, timezone_name=normalize_timezone(x_user_timezone) if x_user_timezone else None)
    db.commit()
