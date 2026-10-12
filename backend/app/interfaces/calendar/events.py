"""One-off events, the routine changes each one owns, and one-day changes made directly.

An event is never refused for overlapping: what it clashes with is a warning
(Clashes, below). Saving one can also change the routine occurrences it clashes
with; those changes belong to it, so deleting the event restores the routine and
moving it recomputes them (Occurrences, below). A one-day change without any event
("off work Friday") is the user's own, and wins over an event's change to that day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, UTC
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, Session
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.core.database import Base
from app.core.time import aware, normalize_timezone, user_timezone, utc_now
from app.interfaces.calendar.routines import (
    CalendarRoutine, CalendarRoutineException, CalendarRoutineWindow, routine_display_title,
)


# === Events ========================================================================

# --- Tables ----------------------------------------------------------------------

class CalendarEvent(Base):
    __tablename__ = "interfaces_calendar_events"
    __table_args__ = (
        Index("ix_calendar_event_user_time", "user_id", "starts_at", "ends_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    # Who added it: the user in the calendar, or the assistant's approved proposal.
    source: Mapped[str] = mapped_column(String, nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


# --- Contracts -------------------------------------------------------------------

class EventError(ValueError):
    """A save that can't apply, with a code a proposal card can show."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RoutineChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_id: str
    occurrence_date: date = Field(description="The day the occurrence starts (bedtime's day for sleep).")
    operation: Literal["keep", "start_later", "end_earlier", "split", "skip"]
    margin_minutes: int = Field(default=0, ge=0, le=240)


class CalendarEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=500)
    # The user's own notes. Left out, a change keeps what's there (the
    # assistant's proposals don't carry it); empty clears it.
    description: str | None = Field(default=None, max_length=5000)
    date: date
    start: time
    end: time
    # 1 when it ends the next day; worked out from the times when omitted.
    end_day_offset: Literal[0, 1] | None = None
    routine_changes: list[RoutineChange] = Field(default_factory=list)
    # For a change: the event as it was when the edit began. A mismatch refuses it.
    expected_updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_times(self):
        offset = self.end_day_offset
        if offset is None:
            offset = 1 if self.end <= self.start else 0
            self.end_day_offset = offset
        if offset == 0 and self.end <= self.start:
            raise ValueError("An event has to end after it starts.")
        if offset == 1 and self.end > self.start:
            raise ValueError("An event can't run longer than a day.")
        return self


class CalendarEventResponse(BaseModel):
    id: str
    title: str
    location: str | None
    description: str | None = None
    date: date
    start: str
    end: str
    end_day_offset: Literal[0, 1]
    updated_at: datetime
    routine_changes: list[RoutineChange]


class OccurrenceChangeInput(BaseModel):
    """A change to one day of a routine, made directly.

    Times are minutes from local midnight on the occurrence's start day, so an
    overnight end is past 1440, and a bedtime moved to the evening before is
    below zero.
    """
    model_config = ConfigDict(extra="forbid")

    operation: Literal["skip", "custom"]
    start_offset_minutes: int | None = Field(default=None, ge=-1440, le=2879)
    end_offset_minutes: int | None = Field(default=None, ge=-1439, le=4319)
    expected_created_at: datetime | None = None

    @model_validator(mode="after")
    def validate_times(self):
        if self.operation == "custom":
            if self.start_offset_minutes is None or self.end_offset_minutes is None:
                raise ValueError("A changed day needs its start and end.")
            length = self.end_offset_minutes - self.start_offset_minutes
            if not 0 < length <= 1440:
                raise ValueError("A changed day has to end after it starts, within a day.")
        return self


# --- Events ----------------------------------------------------------------------

def event_response(event: CalendarEvent, db: Session) -> CalendarEventResponse:
    from app.interfaces.calendar.routines import CalendarRoutineException

    tz = user_timezone(event.timezone)
    start = aware(event.starts_at).astimezone(tz)
    end = aware(event.ends_at).astimezone(tz)
    changes = db.query(CalendarRoutineException).filter(
        CalendarRoutineException.event_id == event.id,
    ).order_by(CalendarRoutineException.occurrence_date).all()
    return CalendarEventResponse(
        id=event.id, title=event.title, location=event.location, description=event.description,
        date=start.date(),
        start=start.strftime("%H:%M"), end=end.strftime("%H:%M"),
        end_day_offset=1 if end.date() > start.date() else 0,
        updated_at=aware(event.updated_at),
        routine_changes=[
            RoutineChange(
                window_id=change.window_id, occurrence_date=change.occurrence_date,
                operation=change.operation or "skip",  # type: ignore[arg-type]
                margin_minutes=change.margin_minutes,
            )
            for change in changes
        ],
    )


def owned_event(user_id: str, event_id: str, db: Session, *, lock: bool = False) -> CalendarEvent:
    query = db.query(CalendarEvent).filter(CalendarEvent.id == event_id, CalendarEvent.user_id == user_id)
    if lock:
        query = query.with_for_update()
    event = query.one_or_none()
    if event is None:
        raise EventError("target_missing", "That event isn't on the calendar anymore.")
    return event


def _same_instant(left: datetime, right: datetime) -> bool:
    return abs((aware(left) - aware(right)).total_seconds()) < 1


def _apply_times(event: CalendarEvent, payload: CalendarEventInput, timezone_name: str | None) -> None:

    name = normalize_timezone(timezone_name)
    event.starts_at, event.ends_at = event_span(
        payload.date, payload.start.strftime("%H:%M"), payload.end.strftime("%H:%M"),
        payload.end_day_offset or 0, name,
    )
    event.timezone = name
    event.title = payload.title.strip()
    event.location = payload.location.strip() if payload.location and payload.location.strip() else None
    if payload.description is not None:
        event.description = payload.description.strip() or None


def _replace_routine_changes(
    user_id: str, event: CalendarEvent, changes: list[RoutineChange], db: Session, timezone_name: str | None,
) -> None:
    """Make the event's routine changes exactly these, checked against the occurrences now."""
    from app.interfaces.calendar.routines import CalendarRoutineException

    db.query(CalendarRoutineException).filter(
        CalendarRoutineException.event_id == event.id,
    ).delete(synchronize_session=False)
    starts_at, ends_at = aware(event.starts_at), aware(event.ends_at)
    seen: set[tuple[str, date]] = set()
    for change in changes:
        if change.operation == "keep" or (change.window_id, change.occurrence_date) in seen:
            continue
        seen.add((change.window_id, change.occurrence_date))
        occurrence = find_occurrence(
            user_id, db, change.window_id, change.occurrence_date, timezone_name, exclude_event_id=event.id,
        )
        if occurrence is None or not (occurrence.base_start < ends_at and occurrence.base_end > starts_at):
            raise EventError(
                "occurrence_missing",
                "A routine this event changes isn't at that time anymore.",
            )
        db.add(CalendarRoutineException(
            id=str(uuid4()), window_id=change.window_id, occurrence_date=change.occurrence_date,
            kind="cancelled" if change.operation == "skip" else "modified",
            source="event", operation=change.operation, margin_minutes=change.margin_minutes,
            event_id=event.id,
        ))


def create_event(
    user_id: str, payload: CalendarEventInput, db: Session, timezone_name: str | None,
    *, source: Literal["user", "assistant"] = "user",
) -> CalendarEvent:
    event = CalendarEvent(id=str(uuid4()), user_id=user_id, source=source)
    _apply_times(event, payload, timezone_name)
    db.add(event)
    db.flush()
    _replace_routine_changes(user_id, event, payload.routine_changes, db, timezone_name)
    db.flush()
    return event


def update_event(
    user_id: str, event_id: str, payload: CalendarEventInput, db: Session, timezone_name: str | None,
) -> CalendarEvent:
    event = owned_event(user_id, event_id, db, lock=True)
    if payload.expected_updated_at and not _same_instant(event.updated_at, payload.expected_updated_at):
        raise EventError("target_changed", "That event changed since this edit began.")
    _apply_times(event, payload, timezone_name)
    event.updated_at = utc_now()
    db.flush()
    _replace_routine_changes(user_id, event, payload.routine_changes, db, timezone_name)
    db.flush()
    return event


def delete_event(user_id: str, event_id: str, db: Session, *, expected_updated_at: datetime | None = None) -> None:
    """Delete an event; the routine changes it owns go with it."""
    event = owned_event(user_id, event_id, db, lock=True)
    if expected_updated_at and not _same_instant(event.updated_at, expected_updated_at):
        raise EventError("target_changed", "That event changed since this was proposed.")
    db.delete(event)
    db.flush()


# --- One-day changes -------------------------------------------------------------

def set_occurrence_change(
    user_id: str, window_id: str, start_day: date, payload: OccurrenceChangeInput, db: Session,
    timezone_name: str | None,
) -> None:
    """Change one day of a routine directly, replacing any direct change already there."""
    from app.interfaces.calendar.routines import CalendarRoutineException

    if find_occurrence(user_id, db, window_id, start_day, timezone_name) is None:
        raise EventError("occurrence_missing", "That routine doesn't happen on that day.")
    existing = db.query(CalendarRoutineException).filter(
        CalendarRoutineException.window_id == window_id,
        CalendarRoutineException.occurrence_date == start_day,
        CalendarRoutineException.source == "direct",
    ).with_for_update().one_or_none()
    if payload.expected_created_at and (existing is None or not _same_instant(
        existing.created_at, payload.expected_created_at,
    )):
        raise EventError("target_changed", "That day changed since this was proposed.")
    if existing is None:
        existing = CalendarRoutineException(
            id=str(uuid4()), window_id=window_id, occurrence_date=start_day, source="direct",
        )
        db.add(existing)
    existing.kind = "cancelled" if payload.operation == "skip" else "modified"
    existing.operation = payload.operation
    existing.start_offset_minutes = payload.start_offset_minutes
    existing.end_offset_minutes = payload.end_offset_minutes
    existing.created_at = utc_now()
    db.flush()


class OccurrenceNoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(max_length=5000)


def set_occurrence_note(
    user_id: str, window_id: str, start_day: date, description: str, db: Session, timezone_name: str | None,
) -> None:
    """Write the user's note on one day of a routine; an empty one removes it.

    A note isn't a change: the day keeps its times, and events still change it.
    """
    from app.interfaces.calendar.routines import CalendarOccurrenceNote

    if find_occurrence(user_id, db, window_id, start_day, timezone_name) is None:
        raise EventError("occurrence_missing", "That routine doesn't happen on that day.")
    note = db.query(CalendarOccurrenceNote).filter(
        CalendarOccurrenceNote.window_id == window_id,
        CalendarOccurrenceNote.occurrence_date == start_day,
    ).with_for_update().one_or_none()
    text_value = description.strip()
    if not text_value:
        if note is not None:
            db.delete(note)
    elif note is None:
        db.add(CalendarOccurrenceNote(
            id=str(uuid4()), window_id=window_id, occurrence_date=start_day, description=text_value,
        ))
    else:
        note.description = text_value
    db.flush()


def clear_occurrence_change(user_id: str, window_id: str, start_day: date, db: Session) -> bool:
    """Put one day of a routine back as it normally is. Events' changes to it apply again."""
    from app.interfaces.calendar.routines import (
        CalendarRoutine, CalendarRoutineException, CalendarRoutineWindow,
    )

    exception = db.query(CalendarRoutineException).join(
        CalendarRoutineWindow, CalendarRoutineException.window_id == CalendarRoutineWindow.id,
    ).join(CalendarRoutine, CalendarRoutineWindow.routine_id == CalendarRoutine.id).filter(
        CalendarRoutine.user_id == user_id,
        CalendarRoutineException.window_id == window_id,
        CalendarRoutineException.occurrence_date == start_day,
        CalendarRoutineException.source == "direct",
    ).one_or_none()
    if exception is None:
        return False
    db.delete(exception)
    db.flush()
    return True


# === Occurrences ===================================================================
#
# Routine occurrences: when each window actually happens, once changes are applied.
#
# An occurrence is one window on one day, keyed by the day it **starts**. Sleep is
# talked about by the day you wake, so an overnight window's occurrence for "the
# sleep ending Thursday" is keyed by Wednesday.
#
# A change to an occurrence (`CalendarRoutineException`) comes from one of two
# places, and they resolve in this order:
#
# - **direct**: the user changed that one day. It wins outright: its own times, or
#   skipped.
# - **event**: an event cut into it. Each event-owned change takes away the part of
#   the occurrence its event needs (all of it, the start, the end, or a gap in the
#   middle), widened by its margin, so several events compose.

Span = tuple[datetime, datetime]


@dataclass
class Occurrence:
    routine: CalendarRoutine
    window: CalendarRoutineWindow
    start_day: date
    timezone: ZoneInfo
    base_start: datetime
    base_end: datetime
    # What remains of it: one span unchanged, none when skipped, two when split.
    segments: list[Span] = field(default_factory=list)
    changed_by: str | None = None
    changed_by_event_ids: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return occurrence_key(self.window.id, self.start_day)


def occurrence_key(window_id: str, start_day: date) -> str:
    return f"{window_id}:{start_day.isoformat()}"


def _clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":")[:2])
    return time(hour, minute)


def occurs_on(routine: CalendarRoutine, window: CalendarRoutineWindow, start_day: date) -> bool:
    """Whether the window has an occurrence starting on this day."""
    if start_day.weekday() not in window.weekdays:
        return False
    if routine.effective_from and start_day < routine.effective_from:
        return False
    if routine.effective_until and start_day > routine.effective_until:
        return False
    return True


def base_span(window: CalendarRoutineWindow, start_day: date, tz: ZoneInfo) -> Span:
    start = datetime.combine(start_day, _clock(window.start_local_time), tz)
    end = datetime.combine(start_day + timedelta(days=window.end_day_offset), _clock(window.end_local_time), tz)
    return start, end


def at_offset(start_day: date, minutes: int, tz: ZoneInfo) -> datetime:
    """Local wall time `minutes` after midnight on `start_day`.

    Aware datetimes add in wall time, so a change across a daylight-saving night
    keeps the clock times the user set.
    """
    return datetime.combine(start_day, time.min, tz) + timedelta(minutes=minutes)


def offset_of(start_day: date, value: datetime, tz: ZoneInfo) -> int:
    local = value.astimezone(tz)
    days = (local.date() - start_day).days
    return days * 1440 + local.hour * 60 + local.minute


def _subtract(segments: list[Span], cut_start: datetime, cut_end: datetime) -> list[Span]:
    result: list[Span] = []
    for start, end in segments:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
            continue
        if cut_start > start:
            result.append((start, cut_start))
        if cut_end < end:
            result.append((cut_end, end))
    return result


FAR_PAST = datetime(1, 1, 2, tzinfo=UTC)
FAR_FUTURE = datetime(9999, 12, 30, tzinfo=UTC)


def event_cut(operation: str, event_start: datetime, event_end: datetime, margin: int) -> Span | None:
    """The part of an occurrence an event's change takes away, or None to keep it."""
    before = event_start - timedelta(minutes=margin)
    after = event_end + timedelta(minutes=margin)
    if operation == "skip":
        return FAR_PAST, FAR_FUTURE
    if operation == "start_later":
        return FAR_PAST, after
    if operation == "end_earlier":
        return before, FAR_FUTURE
    if operation == "split":
        return before, after
    return None


def resolve(occurrence: Occurrence, exceptions: list[CalendarRoutineException],
            events: dict[str, CalendarEvent], *, exclude_event_id: str | None = None) -> Occurrence:
    """Apply an occurrence's changes to it, in precedence order (see the module)."""
    base = [(occurrence.base_start, occurrence.base_end)]
    direct = next((item for item in exceptions if item.source == "direct"), None)
    if direct is not None:
        occurrence.changed_by = "direct"
        if direct.operation == "skip" or direct.kind == "cancelled":
            occurrence.segments = []
        else:
            occurrence.segments = [(
                at_offset(occurrence.start_day, direct.start_offset_minutes or 0, occurrence.timezone),
                at_offset(occurrence.start_day, direct.end_offset_minutes or 0, occurrence.timezone),
            )]
        return occurrence
    segments = base
    for item in exceptions:
        if item.source != "event" or item.event_id is None or item.event_id == exclude_event_id:
            continue
        event = events.get(item.event_id)
        if event is None or not item.operation:
            continue
        cut = event_cut(item.operation, aware(event.starts_at), aware(event.ends_at), item.margin_minutes)
        if cut is None:
            continue
        segments = _subtract(segments, *cut)
        occurrence.changed_by = "event"
        occurrence.changed_by_event_ids.append(event.id)
    occurrence.segments = segments
    return occurrence


def occurrences_between(
    user_id: str, db: Session, range_start: datetime, range_end: datetime, timezone_name: str | None,
    *, exclude_event_id: str | None = None, include_skipped: bool = False,
) -> list[Occurrence]:
    """Every routine occurrence that overlaps the range, by its base span or by the
    times a day of it was moved to, with its changes applied.

    `exclude_event_id` leaves out one event's own changes, which is how an event
    being edited sees the routines it clashes with as they would be without it.
    """
    routines = db.query(CalendarRoutine).filter(
        CalendarRoutine.user_id == user_id,
        CalendarRoutine.status == "active",
    ).all()
    window_ids = [window.id for routine in routines for window in routine.windows]
    first_day = (range_start - timedelta(days=2)).date()
    last_day = (range_end + timedelta(days=1)).date()
    exceptions: dict[tuple[str, date], list[CalendarRoutineException]] = {}
    if window_ids:
        for item in db.query(CalendarRoutineException).filter(
            CalendarRoutineException.window_id.in_(window_ids),
            CalendarRoutineException.occurrence_date >= first_day,
            CalendarRoutineException.occurrence_date <= last_day,
        ):
            exceptions.setdefault((item.window_id, item.occurrence_date), []).append(item)
    event_ids = {item.event_id for items in exceptions.values() for item in items if item.event_id}
    events = {
        event.id: event
        for event in db.query(CalendarEvent).filter(CalendarEvent.id.in_(event_ids))
    } if event_ids else {}

    result: list[Occurrence] = []
    tz = user_timezone(timezone_name)
    for routine in routines:
        created_at = aware(routine.created_at)
        for window in routine.windows:
            day = first_day
            while day <= last_day:
                if occurs_on(routine, window, day):
                    start, end = base_span(window, day, tz)
                    changes = exceptions.get((window.id, day), [])
                    in_range = start < range_end and end > range_start
                    # A day given its own times can sit outside its base span.
                    moved = any(item.source == "direct" and item.operation == "custom" for item in changes)
                    # Sleep is not backfilled from before the schedule existed;
                    # creation time, not now, so later history stays.
                    if (in_range or moved) and not (routine.kind == "sleep" and end <= created_at):
                        occurrence = resolve(
                            Occurrence(routine=routine, window=window, start_day=day, timezone=tz,
                                       base_start=start, base_end=end),
                            changes, events, exclude_event_id=exclude_event_id,
                        )
                        reaches = in_range or any(
                            part_start < range_end and part_end > range_start
                            for part_start, part_end in occurrence.segments
                        )
                        if reaches and (occurrence.segments or include_skipped):
                            result.append(occurrence)
                day += timedelta(days=1)
    return result


def find_occurrence(
    user_id: str, db: Session, window_id: str, start_day: date, timezone_name: str | None,
    *, exclude_event_id: str | None = None,
) -> Occurrence | None:
    """One occurrence by its window and start day, if the user's routine has it."""
    window = db.query(CalendarRoutineWindow).join(
        CalendarRoutine, CalendarRoutineWindow.routine_id == CalendarRoutine.id,
    ).filter(
        CalendarRoutineWindow.id == window_id,
        CalendarRoutine.user_id == user_id,
        CalendarRoutine.status == "active",
    ).one_or_none()
    if window is None or not occurs_on(window.routine, window, start_day):
        return None
    tz = user_timezone(timezone_name)
    start, end = base_span(window, start_day, tz)
    return next((
        occurrence for occurrence in occurrences_between(
            user_id, db, start, end, timezone_name,
            exclude_event_id=exclude_event_id, include_skipped=True,
        )
        if occurrence.window.id == window_id and occurrence.start_day == start_day
    ), None)


# === Clashes =======================================================================
#
# What an event overlaps, and what each routine it overlaps could do about it.
#
# Clashes never stop an event: they are shown as warnings. A clash with a routine
# occurrence also offers changes to that occurrence (start later, end earlier,
# split, skip), worked out here with their resulting times and labels, so the
# editor, the chat artifact and the assistant all describe them the same way.
# Other events only warn.

Operation = Literal["keep", "start_later", "end_earlier", "split", "skip"]


class ClashOption(BaseModel):
    operation: Operation
    label: str
    # What would remain of the occurrence, as ISO local times.
    segments: list[tuple[str, str]]


class Clash(BaseModel):
    key: str
    type: Literal["routine", "event"]
    title: str
    start: str
    end: str
    routine_id: str | None = None
    routine_kind: Literal["sleep", "work", "other"] | None = None
    window_id: str | None = None
    occurrence_date: str | None = None
    event_id: str | None = None
    # A direct change to that day wins over an event's, so there is nothing to offer.
    locked: bool = False
    options: list[ClashOption] = Field(default_factory=list)


def _clock_label(value: datetime) -> str:
    return f"{value.hour % 12 or 12}:{value.minute:02d} {'PM' if value.hour >= 12 else 'AM'}"


def _label(operation: str, kind: str, name: str, remaining: list[tuple[datetime, datetime]],
           base: tuple[datetime, datetime]) -> str:
    if operation == "keep":
        return "Keep"
    if operation == "skip":
        return {"sleep": "Skip this sleep", "work": "Skip work"}.get(kind, f"Skip {name}")
    if operation == "start_later":
        return f"{'Bedtime' if kind == 'sleep' else 'Start at'} {_clock_label(remaining[0][0])}"
    if operation == "end_earlier":
        return {"sleep": "Wake at", "work": "Leave at"}.get(kind, "End at") + f" {_clock_label(remaining[-1][1])}"
    gap = f"{_clock_label(remaining[0][1])}–{_clock_label(remaining[-1][0])}"
    return f"Up {gap}" if kind == "sleep" else f"Out {gap}"


def routine_options(occurrence: Occurrence, event_start: datetime, event_end: datetime,
                    margin: int = 0) -> list[ClashOption]:
    """The changes that make sense for where the event falls in this occurrence."""
    base = (occurrence.base_start, occurrence.base_end)
    before = event_start - timedelta(minutes=margin)
    after = event_end + timedelta(minutes=margin)
    operations: list[str] = ["keep"]
    if base[0] < after < base[1]:
        operations.append("start_later")
    if base[0] < before < base[1]:
        operations.append("end_earlier")
    if base[0] < before and after < base[1]:
        operations.append("split")
    operations.append("skip")
    routine = occurrence.routine
    name = routine.name or routine.kind
    options = []
    for operation in operations:
        cut = event_cut(operation, event_start, event_end, margin)
        remaining = [
            (start.astimezone(occurrence.timezone), end.astimezone(occurrence.timezone))
            for start, end in (_subtract([base], *cut) if cut else [base])
        ]
        options.append(ClashOption(
            operation=operation,  # type: ignore[arg-type]
            label=_label(operation, routine.kind, name, remaining, base),
            segments=[(start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))
                      for start, end in remaining],
        ))
    return options


def find_clashes(
    user_id: str, db: Session, starts_at: datetime, ends_at: datetime, timezone_name: str | None,
    *, exclude_event_id: str | None = None, margins: dict[str, int] | None = None,
) -> list[Clash]:
    """Everything the span overlaps, routines first, as the user's calendar has it now.

    `exclude_event_id` is the event being edited: its own changes are left out, so
    the routines appear as they would without it, and it isn't a clash with itself.
    """
    margins = margins or {}
    tz = user_timezone(timezone_name)
    clashes: list[Clash] = []

    for occurrence in occurrences_between(
        user_id, db, starts_at, ends_at, timezone_name, exclude_event_id=exclude_event_id,
    ):
        if not any(start < ends_at and end > starts_at for start, end in occurrence.segments):
            continue
        routine = occurrence.routine
        locked = occurrence.changed_by == "direct"
        clashes.append(Clash(
            key=occurrence.key, type="routine",
            title=routine_display_title(routine.kind, routine.name),
            start=occurrence.base_start.isoformat(timespec="seconds"),
            end=occurrence.base_end.isoformat(timespec="seconds"),
            routine_id=routine.id, routine_kind=routine.kind,  # type: ignore[arg-type]
            window_id=occurrence.window.id, occurrence_date=occurrence.start_day.isoformat(),
            locked=locked,
            options=[] if locked else routine_options(
                occurrence, starts_at, ends_at, margins.get(occurrence.key, 0),
            ),
        ))

    events = db.query(CalendarEvent).filter(
        CalendarEvent.user_id == user_id,
        CalendarEvent.starts_at < ends_at,
        CalendarEvent.ends_at > starts_at,
    )
    if exclude_event_id:
        events = events.filter(CalendarEvent.id != exclude_event_id)
    for event in events.order_by(CalendarEvent.starts_at):
        clashes.append(Clash(
            key=f"event:{event.id}", type="event", title=event.title,
            start=aware(event.starts_at).astimezone(tz).isoformat(timespec="seconds"),
            end=aware(event.ends_at).astimezone(tz).isoformat(timespec="seconds"),
            event_id=event.id,
        ))

    return clashes


def event_span(day: date, start: str, end: str, end_day_offset: int, timezone_name: str | None) -> tuple[datetime, datetime]:
    """An event's start and end from its local date, clock times and day offset."""
    tz = user_timezone(timezone_name)
    start_hour, start_minute = (int(part) for part in start.split(":")[:2])
    end_hour, end_minute = (int(part) for part in end.split(":")[:2])
    starts_at = datetime.combine(day, time(start_hour, start_minute), tz)
    ends_at = datetime.combine(day + timedelta(days=end_day_offset), time(end_hour, end_minute), tz)
    return starts_at.astimezone(UTC), ends_at.astimezone(UTC)
