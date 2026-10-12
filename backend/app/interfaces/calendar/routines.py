"""Routines: the user's recurring sleep, work and other windows, and their tables.

A sleep routine is stored by the day it starts but proposed by wake day, since
that is how people describe it; the helpers here convert between the two.
"""

from __future__ import annotations
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import user_now, utc_now


# --- Contracts -------------------------------------------------------------------

def weekdays_are_valid(window):
    """A window's weekdays, whichever shape the window is in: each once, Monday 0 to Sunday 6."""
    if len(set(window.weekdays)) != len(window.weekdays):
        raise ValueError("Weekdays must be unique.")
    if any(day < 0 or day > 6 for day in window.weekdays):
        raise ValueError("Weekdays must be between 0 and 6.")
    return window


def ends_after_it_starts(routine):
    """A routine's dates, whichever shape the routine is in."""
    if routine.effective_from and routine.effective_until and routine.effective_until < routine.effective_from:
        raise ValueError("Routine end date cannot be before its start date.")
    return routine


class CalendarRoutineWindowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    weekdays: list[int] = Field(min_length=1)
    start_local_time: time
    end_local_time: time
    end_day_offset: Literal[0, 1] = 0

    validate_weekdays = model_validator(mode="after")(weekdays_are_valid)


class CalendarRoutineUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    kind: Literal["sleep", "work", "other"]
    status: Literal["active", "archived"] = "active"
    archive_reason: str | None = None
    name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=500)
    effective_from: date | None = None
    effective_until: date | None = None
    windows: list[CalendarRoutineWindowInput] = Field(min_length=1)

    validate_effective_dates = model_validator(mode="after")(ends_after_it_starts)


# --- Tables ----------------------------------------------------------------------

class CalendarRoutine(Base):
    __tablename__ = "interfaces_calendar_routines"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('sleep', 'work', 'other')", name="ck_calendar_routine_kind"
        ),
        CheckConstraint(
            "status IN ('active', 'archived')", name="ck_calendar_routine_status"
        ),
        Index("ix_calendar_routine_user_status_kind", "user_id", "status", "kind"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="user")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    archive_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    windows: Mapped[list[CalendarRoutineWindow]] = relationship(
        "CalendarRoutineWindow",
        back_populates="routine",
        cascade="all, delete-orphan",
        # UUID ordering is random and caused UI color assignments to swap after
        # reloading a routine. Creation order preserves the user's color slots.
        order_by="(CalendarRoutineWindow.created_at, CalendarRoutineWindow.id)",
    )


class CalendarRoutineWindow(Base):
    __tablename__ = "interfaces_calendar_routine_windows"
    __table_args__ = (
        CheckConstraint("end_day_offset IN (0, 1)", name="ck_calendar_routine_window_day_offset"),
        Index("ix_calendar_routine_window_routine", "routine_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    routine_id: Mapped[str] = mapped_column(
        String, ForeignKey("interfaces_calendar_routines.id", ondelete="CASCADE"), nullable=False
    )
    weekdays: Mapped[Any] = mapped_column(JSON, nullable=False)
    start_local_time: Mapped[str] = mapped_column(String, nullable=False)
    end_local_time: Mapped[str] = mapped_column(String, nullable=False)
    end_day_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    routine: Mapped[CalendarRoutine] = relationship("CalendarRoutine", back_populates="windows")
    exceptions: Mapped[list[CalendarRoutineException]] = relationship(
        "CalendarRoutineException", cascade="all, delete-orphan"
    )


class CalendarRoutineException(Base):
    """A change to one occurrence of a routine window, keyed by the day it starts.

    `source` says who made it: an event (which owns it, and whose deletion removes
    it), or the user directly for that day.
    `operation` is what it does. An event's change stores intent, not times: it is
    worked out from the event's times whenever the occurrence is read, so moving
    the event moves the change. A direct change stores its own times, as minutes
    from local midnight on the occurrence's start day. See Occurrences in `events.py`.
    """
    __tablename__ = "interfaces_calendar_routine_exceptions"
    __table_args__ = (
        CheckConstraint(
            "source IN ('event', 'direct')", name="ck_calendar_routine_exception_source"
        ),
        Index(
            "uq_calendar_routine_exception_event", "window_id", "occurrence_date", "event_id",
            unique=True, postgresql_where=text("event_id IS NOT NULL"),
        ),
        Index(
            "uq_calendar_routine_exception_source", "window_id", "occurrence_date", "source",
            unique=True, postgresql_where=text("event_id IS NULL"),
        ),
        Index("ix_calendar_routine_exception_window_date", "window_id", "occurrence_date"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    window_id: Mapped[str] = mapped_column(
        String, ForeignKey("interfaces_calendar_routine_windows.id", ondelete="CASCADE"), nullable=False
    )
    occurrence_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="cancelled")
    source: Mapped[str] = mapped_column(String, nullable=False, default="direct")
    # skip | start_later | end_earlier | split for an event; skip | custom directly.
    operation: Mapped[str | None] = mapped_column(String, nullable=True)
    margin_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("interfaces_calendar_events.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CalendarOccurrenceNote(Base):
    """The user's note on one occurrence of a routine window, keyed like its changes.

    Kept apart from `CalendarRoutineException`: a note leaves the day as it is.
    """
    __tablename__ = "interfaces_calendar_occurrence_notes"
    __table_args__ = (
        Index("uq_calendar_occurrence_note", "window_id", "occurrence_date", unique=True),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    window_id: Mapped[str] = mapped_column(
        String, ForeignKey("interfaces_calendar_routine_windows.id", ondelete="CASCADE"), nullable=False
    )
    occurrence_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


# --- Sleep -----------------------------------------------------------------------

def active_sleep_routine(user_id: str, db) -> CalendarRoutine | None:
    return db.query(CalendarRoutine).filter(
        CalendarRoutine.user_id == user_id,
        CalendarRoutine.kind == "sleep",
        CalendarRoutine.status == "active",
    ).first()


def sleep_start_days(wake_days: list[int], start: time, end: time) -> tuple[list[int], int]:
    # Midnight starts on the wake day, not the prior evening. Derive the offset
    # rather than trusting a stale editor value that could make a 33-hour window.
    overnight = end <= start
    return ([(day - 1) % 7 for day in wake_days] if overnight else wake_days, int(overnight))


def sleep_routine_wake_day_payload(existing: CalendarRoutineUpsert) -> dict[str, Any]:
    """Convert persisted start-day windows to the editor/tool wake-day convention."""
    payload = existing.model_dump(mode="json")
    for window in payload["windows"]:
        if window["end_day_offset"] == 1:
            window["weekdays"] = sorted((day + 1) % 7 for day in window["weekdays"])
    return payload


# --- Routines --------------------------------------------------------------------

def _calendar_routine_payload(routine: CalendarRoutine) -> CalendarRoutineUpsert:
    return CalendarRoutineUpsert.model_validate(
        {
            "id": routine.id,
            "kind": routine.kind,
            "status": routine.status,
            "archive_reason": routine.archive_reason,
            "name": routine.name,
            "location": routine.location,
            "effective_from": routine.effective_from,
            "effective_until": routine.effective_until,
            "windows": [
                {
                    "id": window.id,
                    "weekdays": window.weekdays,
                    "start_local_time": window.start_local_time,
                    "end_local_time": window.end_local_time,
                    "end_day_offset": window.end_day_offset,
                }
                for window in routine.windows
            ],
        }
    )


def get_calendar_routines(user_id: str, db) -> dict[str, list[CalendarRoutineUpsert]]:
    kinds = ("sleep", "work", "other")
    result: dict[str, list[CalendarRoutineUpsert]] = {kind: [] for kind in kinds}
    routines = db.query(CalendarRoutine).filter(
        CalendarRoutine.user_id == user_id,
        CalendarRoutine.kind.in_(kinds),
    ).order_by(
        (CalendarRoutine.status == "active").desc(),
        CalendarRoutine.updated_at.desc(),
    ).all()
    for routine in routines:
        if routine.status == "active":
            result[routine.kind].append(_calendar_routine_payload(routine))
    return result


def get_calendar_routine(
    user_id: str,
    kind: Literal["sleep", "work", "other"],
    db,
) -> CalendarRoutineUpsert | None:
    routine = db.query(CalendarRoutine).filter(
        CalendarRoutine.user_id == user_id,
        CalendarRoutine.kind == kind,
    ).order_by(
        (CalendarRoutine.status == "active").desc(),
        CalendarRoutine.updated_at.desc(),
    ).first()
    return _calendar_routine_payload(routine) if routine is not None else None


def delete_calendar_routine(
    user_id: str, kind: str, db, routine_id: str | None = None
) -> bool:
    """Delete one routine, the newest of its kind unless named. False when there is none."""
    query = db.query(CalendarRoutine).filter(
        CalendarRoutine.user_id == user_id,
        CalendarRoutine.kind == kind,
    )
    if routine_id is not None:
        query = query.filter(CalendarRoutine.id == routine_id)
    routine = query.order_by(
        (CalendarRoutine.status == "active").desc(),
        CalendarRoutine.updated_at.desc(),
    ).first()
    if routine is None:
        return False
    db.delete(routine)
    db.commit()
    return True


class RoutineOverlapError(ValueError):
    pass


def _window_occurrences(
    window: CalendarRoutineWindowInput,
    effective_from: date,
    effective_until: date | None,
    anchor: date,
) -> list[tuple[datetime, datetime]]:
    occurrences: list[tuple[datetime, datetime]] = []
    for offset in range(-1, 15):
        occurrence_date = anchor + timedelta(days=offset)
        if occurrence_date.weekday() not in window.weekdays:
            continue
        if occurrence_date < effective_from:
            continue
        if effective_until and occurrence_date > effective_until:
            continue
        start = datetime.combine(occurrence_date, window.start_local_time)
        end = datetime.combine(
            occurrence_date + timedelta(days=window.end_day_offset),
            window.end_local_time,
        )
        occurrences.append((start, end))
    return occurrences


def _assert_routine_does_not_overlap(
    user_id: str,
    payload: CalendarRoutineUpsert,
    effective_from: date,
    db,
    replacing_id: str | None,
) -> None:
    """`replacing_id` is the routine this one becomes, which it never overlaps."""
    own_occurrences = [
        (index, occurrence)
        for index, window in enumerate(payload.windows)
        for occurrence in _window_occurrences(
            window, effective_from, payload.effective_until, effective_from
        )
    ]
    if any(
        left_start < right_end and right_start < left_end
        for left_index, (left_start, left_end) in own_occurrences
        for right_index, (right_start, right_end) in own_occurrences
        if left_index < right_index
    ):
        raise RoutineOverlapError("Schedule windows in the routine cannot overlap.")

    candidates = db.query(CalendarRoutine).filter(
        CalendarRoutine.user_id == user_id,
        CalendarRoutine.status == "active",
    ).all()
    if replacing_id:
        candidates = [routine for routine in candidates if routine.id != replacing_id]
    date_ceiling = payload.effective_until or date.max
    for other in candidates:
        # Work and sleep keep clear of everything; only other routines may share time.
        if payload.kind == "other" and other.kind == "other":
            continue
        other_start = other.effective_from or date.min
        other_end = other.effective_until or date.max
        overlap_start = max(effective_from, other_start)
        if overlap_start > min(date_ceiling, other_end):
            continue
        own_occurrences = [
            occurrence
            for window in payload.windows
            for occurrence in _window_occurrences(
                window, effective_from, payload.effective_until, overlap_start
            )
        ]
        other_occurrences = [
            occurrence
            for window in other.windows
            for occurrence in _window_occurrences(
                CalendarRoutineWindowInput(
                    id=window.id,
                    weekdays=window.weekdays,
                    start_local_time=time.fromisoformat(window.start_local_time),
                    end_local_time=time.fromisoformat(window.end_local_time),
                    end_day_offset=window.end_day_offset,
                ),
                other_start,
                other.effective_until,
                overlap_start,
            )
        ]
        if any(
            own_start < other_end_at and other_start_at < own_end
            for own_start, own_end in own_occurrences
            for other_start_at, other_end_at in other_occurrences
        ):
            raise RoutineOverlapError(
                f'This routine overlaps "{routine_display_title(other.kind, other.name)}".'
            )


def _routine_being_replaced(user_id: str, payload: CalendarRoutineUpsert, db) -> CalendarRoutine | None:
    """The routine a payload changes: the one it names, the user's sleep for sleep,
    or the one that owns a window it keeps. None means it is a new routine."""
    if payload.id:
        return db.query(CalendarRoutine).filter(
            CalendarRoutine.id == payload.id,
            CalendarRoutine.user_id == user_id,
            CalendarRoutine.kind == payload.kind,
        ).first()
    if payload.kind == "sleep":
        return active_sleep_routine(user_id, db)
    window_ids = [window.id for window in payload.windows if window.id]
    if not window_ids:
        return None
    return db.query(CalendarRoutine).join(
        CalendarRoutineWindow, CalendarRoutineWindow.routine_id == CalendarRoutine.id,
    ).filter(
        CalendarRoutine.user_id == user_id,
        CalendarRoutine.kind == payload.kind,
        CalendarRoutine.status == "active",
        CalendarRoutineWindow.id.in_(window_ids),
    ).first()


def routine_blocker(user_id: str, payload: CalendarRoutineUpsert, db, timezone_name: str = "UTC") -> str | None:
    """Why saving this routine would fail as things stand, or None if it would not."""
    routine = _routine_being_replaced(user_id, payload, db)
    effective_from = payload.effective_from or user_now(timezone_name).date()
    try:
        _assert_routine_does_not_overlap(user_id, payload, effective_from, db,
                                         replacing_id=routine.id if routine else payload.id)
    except RoutineOverlapError as error:
        return str(error)
    return None


def replace_calendar_routine(
    user_id: str,
    payload: CalendarRoutineUpsert,
    db,
    timezone_name: str = "UTC",
) -> None:
    """Create or update one focused routine while preserving stable identities."""
    routine = _routine_being_replaced(user_id, payload, db)
    effective_from = payload.effective_from or user_now(timezone_name).date()
    _assert_routine_does_not_overlap(user_id, payload, effective_from, db,
                                     replacing_id=routine.id if routine else payload.id)

    if routine is None:
        routine = CalendarRoutine(
            id=str(uuid4()),
            user_id=user_id,
            kind=payload.kind,
            source="calendar",
            confidence=1.0,
            status="active",
        )
        db.add(routine)

    routine.name = payload.name.strip()
    routine.location = payload.location.strip() if payload.location and payload.location.strip() else None
    routine.effective_from = effective_from
    routine.effective_until = payload.effective_until
    routine.status = "active"
    routine.archive_reason = None
    existing_windows = {window.id: window for window in routine.windows}
    retained_ids: set[str] = set()
    for value in payload.windows:
        window = existing_windows.get(value.id) if value.id else None
        if window is None:
            window = CalendarRoutineWindow(id=str(uuid4()))
            routine.windows.append(window)
        retained_ids.add(window.id)
        window.weekdays = value.weekdays
        window.start_local_time = value.start_local_time.strftime("%H:%M")
        window.end_local_time = value.end_local_time.strftime("%H:%M")
        window.end_day_offset = value.end_day_offset

    for window in list(routine.windows):
        if window.id not in retained_ids:
            routine.windows.remove(window)

    db.flush()
    sweep_orphaned_exceptions(routine, db)
    db.commit()


def sweep_orphaned_exceptions(routine: CalendarRoutine, db) -> None:
    """Drop changes to occurrences a routine no longer has.

    A window keeps its id through an edit, so its exceptions survive one; but a
    weekday taken out, or dates moved, can leave a change pointing at nothing.
    """
    for window in routine.windows:
        for exception in list(window.exceptions):
            day = exception.occurrence_date
            if (day.weekday() not in window.weekdays
                    or (routine.effective_from and day < routine.effective_from)
                    or (routine.effective_until and day > routine.effective_until)):
                window.exceptions.remove(exception)


def routine_display_title(kind: str, name: str | None) -> str:
    type_label = kind.replace("_", " ").strip().title() or "Routine"
    specific_name = (name or "").strip()
    if not specific_name or specific_name.casefold() == type_label.casefold():
        return type_label
    # "Other" says nothing a name doesn't: a run is "Run", not "Other · Run".
    if kind == "other":
        return specific_name
    prefix = f"{type_label} ·"
    if specific_name.casefold().startswith(prefix.casefold()):
        return specific_name
    return f"{type_label} · {specific_name}"
