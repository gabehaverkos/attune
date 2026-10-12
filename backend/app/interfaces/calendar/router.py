
"""The calendar API: the days it shows, its routines and events, and what clashes."""

from __future__ import annotations
from datetime import date, datetime, time
from typing import List, Literal, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, User
from app.core.database import get_db
from app.interfaces.calendar.routines import (
    CalendarRoutineUpsert, delete_calendar_routine, get_calendar_routines, replace_calendar_routine,
    RoutineOverlapError,
)
from app.interfaces.calendar.events import (
    CalendarEventInput, CalendarEventResponse, Clash, clear_occurrence_change, create_event, delete_event,
    event_response, event_span, EventError, find_clashes, OccurrenceChangeInput,
    OccurrenceNoteInput, owned_event, set_occurrence_change, set_occurrence_note, update_event,
)
from app.interfaces.calendar.schedule import (
    CalendarDay, CalendarRangePayload, get_calendar_days, get_calendar_initial_month_range,
    get_calendar_month_range,
)
from app.interfaces.calendar.interface import KEY
from app.interfaces.registry import require_interface
from app.core.time import request_timezone

if TYPE_CHECKING:
    from app.interfaces.calendar.routines import CalendarRoutineUpsert
    from app.interfaces.calendar.schedule import CalendarDay


# --- Contracts -------------------------------------------------------------------

class CalendarPayload(BaseModel):
    days: List[CalendarDay]
    routines: dict[str, list[CalendarRoutineUpsert]]
    months: dict[str, List[CalendarDay]] = Field(default_factory=dict)


# Calendar is an opt-in interface; every route below is unavailable until the
# user enables it.
router = APIRouter(dependencies=[Depends(require_interface(KEY))])


@router.get("/api/calendar", response_model=CalendarPayload)
def calendar(
    month: str | None = Query(default=None, pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$"),
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarPayload:
    months = (
        get_calendar_initial_month_range(current_user, db, timezone_name)
        if month is None
        else {}
    )
    return CalendarPayload(
        days=get_calendar_days(current_user, db, timezone_name, month),
        routines=get_calendar_routines(current_user.id, db),
        months=months,
    )


@router.get("/api/calendar/range", response_model=CalendarRangePayload)
def calendar_range(
    start: str = Query(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$"),
    months: int = Query(default=13, ge=1, le=24),
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarRangePayload:
    return CalendarRangePayload(
        months=get_calendar_month_range(current_user, db, start, months, timezone_name),
    )


class ClashQuery(BaseModel):
    date: date
    start: time
    end: time
    end_day_offset: Literal[0, 1] | None = None
    # The event being edited: its own changes and itself are left out.
    event_id: str | None = None
    # Per clash key, the buffer around the event for the changes offered.
    margins: dict[str, int] = Field(default_factory=dict)


class EventDelete(BaseModel):
    expected_updated_at: datetime | None = None


def _event_error(error: EventError) -> HTTPException:
    status_code = 404 if error.code == "target_missing" else 409
    return HTTPException(status_code=status_code, detail={"code": error.code, "message": str(error)})


@router.post("/api/calendar/clashes", response_model=list[Clash])
def post_calendar_clashes(
    payload: ClashQuery,
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Clash]:
    offset = payload.end_day_offset if payload.end_day_offset is not None else int(payload.end <= payload.start)
    starts_at, ends_at = event_span(
        payload.date, payload.start.strftime("%H:%M"), payload.end.strftime("%H:%M"), offset, timezone_name,
    )
    return find_clashes(
        current_user.id, db, starts_at, ends_at, timezone_name,
        exclude_event_id=payload.event_id, margins=payload.margins,
    )


@router.get("/api/calendar/events/{event_id}", response_model=CalendarEventResponse)
def get_calendar_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarEventResponse:
    try:
        return event_response(owned_event(current_user.id, event_id, db), db)
    except EventError as error:
        raise _event_error(error) from error


@router.post("/api/calendar/events", response_model=CalendarEventResponse, status_code=status.HTTP_201_CREATED)
def post_calendar_event(
    payload: CalendarEventInput,
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarEventResponse:
    try:
        event = create_event(current_user.id, payload, db, timezone_name)
    except EventError as error:
        db.rollback()
        raise _event_error(error) from error
    db.commit()
    return event_response(event, db)


@router.put("/api/calendar/events/{event_id}", response_model=CalendarEventResponse)
def put_calendar_event(
    event_id: str,
    payload: CalendarEventInput,
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarEventResponse:
    try:
        event = update_event(current_user.id, event_id, payload, db, timezone_name)
    except EventError as error:
        db.rollback()
        raise _event_error(error) from error
    db.commit()
    return event_response(event, db)


@router.delete("/api/calendar/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_calendar_event(
    event_id: str,
    payload: EventDelete | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_event(
            current_user.id, event_id, db,
            expected_updated_at=payload.expected_updated_at if payload else None,
        )
    except EventError as error:
        db.rollback()
        raise _event_error(error) from error
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/api/calendar/occurrences/{window_id}/{occurrence_date}", status_code=status.HTTP_204_NO_CONTENT,
)
def put_occurrence_change(
    window_id: str,
    occurrence_date: date,
    payload: OccurrenceChangeInput,
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Change one day of a routine directly; `occurrence_date` is the day it starts."""
    try:
        set_occurrence_change(current_user.id, window_id, occurrence_date, payload, db, timezone_name)
    except EventError as error:
        db.rollback()
        raise _event_error(error) from error
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/api/calendar/occurrences/{window_id}/{occurrence_date}/note", status_code=status.HTTP_204_NO_CONTENT,
)
def put_occurrence_note(
    window_id: str,
    occurrence_date: date,
    payload: OccurrenceNoteInput,
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Write (or, empty, remove) the user's note on one day of a routine."""
    try:
        set_occurrence_note(current_user.id, window_id, occurrence_date, payload.description, db, timezone_name)
    except EventError as error:
        db.rollback()
        raise _event_error(error) from error
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/api/calendar/occurrences/{window_id}/{occurrence_date}", status_code=status.HTTP_204_NO_CONTENT,
)
def delete_occurrence_change(
    window_id: str,
    occurrence_date: date,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not clear_occurrence_change(current_user.id, window_id, occurrence_date, db):
        raise HTTPException(status_code=404, detail="That day has no change of its own.")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/calendar/routines/{kind}", response_model=CalendarPayload)
def delete_routine(
    kind: Literal["sleep", "work", "other"],
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarPayload:
    if not delete_calendar_routine(current_user.id, kind, db):
        raise HTTPException(status_code=404, detail="Routine not found.")
    db.commit()
    return CalendarPayload(
        days=get_calendar_days(current_user, db, timezone_name),
        routines=get_calendar_routines(current_user.id, db),
    )


@router.delete("/api/calendar/routines/{kind}/{routine_id}", response_model=CalendarPayload)
def delete_routine_by_id(
    routine_id: str,
    kind: Literal["sleep", "work", "other"],
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarPayload:
    if not delete_calendar_routine(current_user.id, kind, db, routine_id):
        raise HTTPException(status_code=404, detail="Routine not found.")
    db.commit()
    return CalendarPayload(
        days=get_calendar_days(current_user, db, timezone_name),
        routines=get_calendar_routines(current_user.id, db),
    )


@router.put("/api/calendar/routines/{kind}", response_model=CalendarPayload)
def put_calendar_routine(
    kind: Literal["sleep", "work", "other"],
    payload: CalendarRoutineUpsert,
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarPayload:
    if kind != payload.kind:
        raise HTTPException(status_code=400, detail="Routine type does not match the URL.")
    try:
        replace_calendar_routine(current_user.id, payload, db, timezone_name)
    except RoutineOverlapError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    return CalendarPayload(
        days=get_calendar_days(current_user, db, timezone_name),
        routines=get_calendar_routines(current_user.id, db),
    )