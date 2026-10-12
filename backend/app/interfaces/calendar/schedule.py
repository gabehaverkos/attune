"""The user's schedule laid out day by day: events, and routine occurrences with
their changes applied, in the shapes the API returns.
"""

from __future__ import annotations
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import List, Literal

from pydantic import BaseModel, Field

from app.core.auth import User
from app.interfaces.calendar.events import CalendarEvent, occurrences_between
from app.interfaces.calendar.routines import CalendarOccurrenceNote, routine_display_title
from app.core.time import aware, user_now, user_timezone


# --- Contracts -------------------------------------------------------------------

class ScheduleBlock(BaseModel):
    id: str
    title: str
    kind: Literal["event", "sleep", "work", "other"]
    start: str
    end: str
    source: Literal["user", "assistant"]
    location: str | None = None
    description: str | None = None
    event_id: str | None = None
    # A routine occurrence: which window, and the day it starts.
    routine_id: str | None = None
    window_id: str | None = None
    occurrence_date: str | None = None
    # Who changed this occurrence, if anyone: the user that day, or events.
    changed_by: Literal["direct", "event"] | None = None
    changed_by_event_ids: list[str] = Field(default_factory=list)


class CalendarDay(BaseModel):
    date: str
    blocks: List[ScheduleBlock]


class CalendarRangePayload(BaseModel):
    months: dict[str, List[CalendarDay]]


# --- Timeline --------------------------------------------------------------------

def get_timeline(
    user_id: str,
    db,
    *,
    start_day: date | None = None,
    day_count: int | None = None,
    timezone_name: str | None = None,
) -> List[CalendarDay]:
    today = user_now(timezone_name).date()
    if start_day is None:
        start_day = today - timedelta(days=(today.weekday() + 1) % 7)
    if day_count is None:
        day_count = 14

    projection_timezone = user_timezone(timezone_name)
    range_start = datetime.combine(start_day, time.min, projection_timezone)
    range_end = datetime.combine(start_day + timedelta(days=day_count), time.min, projection_timezone)

    # Every block with its real start and end; each day then shows the ones that
    # touch it, so an overnight block appears on both days with its true times.
    spans: list[tuple[datetime, datetime, dict]] = []
    for event in (
        db.query(CalendarEvent)
        .filter(
            CalendarEvent.user_id == user_id,
            CalendarEvent.starts_at < range_end,
            CalendarEvent.ends_at > range_start,
        )
        .order_by(CalendarEvent.starts_at.asc())
    ):
        spans.append((aware(event.starts_at), aware(event.ends_at), {
            "id": event.id, "title": event.title, "kind": "event",
            "source": "assistant" if event.source == "assistant" else "user",
            "location": event.location, "description": event.description, "event_id": event.id,
        }))

    occurrences = occurrences_between(user_id, db, range_start, range_end, timezone_name)
    notes: dict[tuple[str, date], str] = {}
    if occurrences:
        for note in db.query(CalendarOccurrenceNote).filter(
            CalendarOccurrenceNote.window_id.in_({occurrence.window.id for occurrence in occurrences}),
            CalendarOccurrenceNote.occurrence_date >= min(occurrence.start_day for occurrence in occurrences),
            CalendarOccurrenceNote.occurrence_date <= max(occurrence.start_day for occurrence in occurrences),
        ):
            notes[(note.window_id, note.occurrence_date)] = note.description

    for occurrence in occurrences:
        routine = occurrence.routine
        for part, (start, end) in enumerate(occurrence.segments):
            suffix = f":part:{part}" if len(occurrence.segments) > 1 else ""
            spans.append((start, end, {
                "id": f"routine:{routine.id}:window:{occurrence.window.id}:start:{occurrence.start_day.isoformat()}{suffix}",
                "title": routine_display_title(routine.kind, routine.name),
                "kind": routine.kind,
                "source": "user" if routine.source in ("user", "calendar") else "assistant",
                "location": routine.location,
                "description": notes.get((occurrence.window.id, occurrence.start_day)),
                "routine_id": routine.id, "window_id": occurrence.window.id,
                "occurrence_date": occurrence.start_day.isoformat(),
                "changed_by": occurrence.changed_by,
                "changed_by_event_ids": occurrence.changed_by_event_ids,
            }))

    days = []
    for offset in range(day_count):
        day_date = start_day + timedelta(days=offset)
        day_date_str = day_date.isoformat()
        local_day_start = datetime.combine(day_date, time.min, projection_timezone)
        local_day_end = local_day_start + timedelta(days=1)
        blocks = []
        for starts_at, ends_at, fields in spans:
            if starts_at >= local_day_end or ends_at <= local_day_start:
                continue
            block_id = fields["id"]
            if block_id.startswith("routine:"):
                block_id = f"{block_id}:day:{day_date_str}"
            blocks.append(ScheduleBlock(**{
                **fields, "id": block_id,
                "start": starts_at.astimezone(projection_timezone).isoformat(timespec="seconds"),
                "end": ends_at.astimezone(projection_timezone).isoformat(timespec="seconds"),
            }))
        blocks.sort(key=lambda block: block.start)
        days.append(CalendarDay(date=day_date_str, blocks=blocks))

    return days


def get_calendar_month_range(
    current_user: User,
    db,
    start_month: str,
    month_count: int,
    timezone_name: str | None = None,
) -> dict[str, List[CalendarDay]]:
    year, month_number = (int(value) for value in start_month.split("-"))
    start_day = date(year, month_number, 1)
    end_month_index = year * 12 + month_number - 1 + month_count
    end_day = date(end_month_index // 12, end_month_index % 12 + 1, 1)
    projected_days = get_timeline(
        current_user.id,
        db,
        start_day=start_day,
        day_count=(end_day - start_day).days,
        timezone_name=timezone_name,
    )
    months: dict[str, List[CalendarDay]] = {}
    for day in projected_days:
        months.setdefault(day.date[:7], []).append(day)
    return months


DEFAULT_RANGE_MONTHS_BEFORE = 6


DEFAULT_RANGE_MONTH_COUNT = 13


def get_calendar_initial_month_range(
    current_user: User,
    db,
    timezone_name: str | None = None,
) -> dict[str, List[CalendarDay]]:
    today = user_now(timezone_name).date()
    anchor_month_index = today.year * 12 + today.month - 1 - DEFAULT_RANGE_MONTHS_BEFORE
    start_month = f"{anchor_month_index // 12:04d}-{anchor_month_index % 12 + 1:02d}"
    return get_calendar_month_range(
        current_user, db, start_month, DEFAULT_RANGE_MONTH_COUNT, timezone_name
    )


def get_calendar_days(
    current_user: User,
    db,
    timezone_name: str | None = None,
    month: str | None = None,
) -> List[CalendarDay]:
    if month:
        year, month_number = (int(value) for value in month.split("-"))
        start_day = date(year, month_number, 1)
    else:
        today = user_now(timezone_name).date()
        start_day = today.replace(day=1)
    day_count = monthrange(start_day.year, start_day.month)[1]
    return get_timeline(
        current_user.id,
        db,
        start_day=start_day,
        day_count=day_count,
        timezone_name=timezone_name,
    )
