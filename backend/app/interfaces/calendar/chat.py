"""What the calendar adds to chat: its artifact types, and the tools that read it and draft them.

Chat finds this module by the interface's key (`chat/tools.py`): `ARTIFACTS` are
the types it offers while the calendar is enabled, and `register` gives a turn
its tools.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from datetime import date, datetime, time
from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError
from pydantic_ai import ModelRetry, RunContext
from sqlalchemy.orm import Session
from typing import Any, Literal

from app.chat.tools import ChatToolbox, register_drafting, require_enabled, TurnDeps
from app.chat.artifacts import (
    ArtifactDefinition, ArtifactDefinitionError, ArtifactExecutionError, PreparedDraft,
    ValidatedArtifactCommand,
)
from app.core.database import SessionLocal
from app.core.time import user_now, user_timezone
from app.interfaces.calendar.events import (
    CalendarEventInput, create_event, delete_event, event_response, event_span,
    EventError, find_clashes, OccurrenceChangeInput, owned_event, RoutineChange, set_occurrence_change,
    update_event,
)
from app.interfaces.calendar.interface import KEY
from app.interfaces.calendar.routines import (
    CalendarRoutine, CalendarRoutineException, CalendarRoutineUpsert, CalendarRoutineWindow,
    delete_calendar_routine, ends_after_it_starts, get_calendar_routine, get_calendar_routines, replace_calendar_routine,
    routine_blocker, routine_display_title, RoutineOverlapError, sleep_routine_wake_day_payload,
    sleep_start_days, weekdays_are_valid,
)
from app.interfaces.calendar.schedule import get_timeline


# === Artifact types ================================================================
#
# The calendar's artifact types: sleep, work and other routines, events, one-day
# changes to a routine, and removals.
#
# `routine_artifact` owns shared draft validation and execution; each routine
# kind below owns its typed payload. The tools that draft them are under
# Tools below. Sleep artifacts use wake-day weekdays; persisted
# windows use start days. A tool only drafts; approving one executes it through
# the artifact lifecycle.

# --- Routine artifacts -----------------------------------------------------------

def routine_artifact(
    kind: Literal["sleep", "work", "other"], label: str, payload_model: type[BaseModel], *,
    prepare_execution: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    prepare_existing: Callable[[Session, str, dict[str, Any], str], dict[str, Any]] | None = None,
    draft_note: Callable[[Session, str, ValidatedArtifactCommand], str | None] | None = None,
) -> ArtifactDefinition:
    def prepare_draft(payload: dict[str, Any]) -> PreparedDraft:
        normalized = dict(payload)
        normalized.pop("timezone", None)
        try:
            draft = payload_model.model_validate(normalized).model_dump(mode="json")
        except ValidationError as error:
            raise ArtifactDefinitionError(str(error)) from error
        missing = []
        if not (draft["name"] or "").strip():
            missing.append("name")
        if not draft["windows"]:
            missing.append("windows")
        for index, window in enumerate(draft["windows"]):
            for field in ("weekdays", "start_local_time", "end_local_time"):
                if not window[field]:
                    missing.append(f"windows.{index}.{field}")
        return PreparedDraft(payload=draft, missing_fields=missing, ready=not missing)

    def generic_execution(payload: dict[str, Any], timezone_name: str = "UTC") -> dict[str, Any]:
        prepared = prepare_draft(payload)
        if not prepared.ready:
            raise ArtifactDefinitionError(
                f"The {kind} routine needs more information before it can be accepted.", prepared.missing_fields,
            )
        draft = prepared.payload
        for window in draft["windows"]:
            if window["end_day_offset"] is None:
                window["end_day_offset"] = int(window["end_local_time"] <= window["start_local_time"])
        draft["name"] = draft["name"].strip()
        draft["location"] = (draft["location"] or "").strip() or None
        draft["effective_from"] = draft["effective_from"] or user_now(timezone_name).date()
        return CalendarRoutineUpsert.model_validate(draft).model_dump(mode="json")

    def execute(db: Session, user_id: str, approved: dict[str, Any], artifact_id: str) -> dict[str, Any]:
        try:
            replace_calendar_routine(user_id, CalendarRoutineUpsert.model_validate(approved), db)
            routine = get_calendar_routine(user_id, kind, db)
        except RoutineOverlapError as error:
            db.rollback()
            raise ArtifactExecutionError(f"{kind}_routine_overlap", str(error), retryable=True) from error
        except Exception as error:
            db.rollback()
            raise ArtifactExecutionError(
                f"{kind}_routine_save_failed", f"The {kind} routine could not be saved.", retryable=True,
            ) from error
        if routine is None:
            raise ArtifactExecutionError(
                f"{kind}_routine_missing", f"The saved {kind} routine could not be found.", retryable=True,
            )
        return {
            "resource_type": "calendar_routine", "resource_id": routine.id, "kind": kind,
            "effective_from": routine.effective_from.isoformat() if routine.effective_from else None,
            "effective_until": routine.effective_until.isoformat() if routine.effective_until else None,
            "href": "/?calendar=open",
            "idempotency_key": artifact_id,
        }

    def blocker(db: Session, user_id: str, payload: dict[str, Any], timezone_name: str) -> str | None:
        try:
            approved = (prepare_execution or generic_execution)(payload, timezone_name)
        except ArtifactDefinitionError:
            return None
        return routine_blocker(user_id, CalendarRoutineUpsert.model_validate(approved), db, timezone_name)

    return ArtifactDefinition(
        type=f"{kind}_routine", schema_version=1, draft_schema=payload_model,
        prepare_draft=prepare_draft, prepare_execution=prepare_execution or generic_execution,
        execute=execute, required_interface=KEY,
        display_title=(lambda payload: routine_display_title(kind, payload.get("name"))) if kind != "sleep" else None,
        draft_note=draft_note,
        prepare_existing=prepare_existing,
        blocker=blocker,
    )


# One window of a routine as drafted: any of it may still be missing. A comment, not a
# docstring, which would go into the schema the model is shown.
class RoutineWindowDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    weekdays: list[int] = Field(
        default_factory=list,
        description="Weekdays using 0=Monday, 1=Tuesday, through 6=Sunday.",
    )
    start_local_time: time | None = Field(
        default=None, description="Local start time in HH:MM format."
    )
    end_local_time: time | None = Field(
        default=None, description="Local end time in HH:MM format."
    )
    end_day_offset: Literal[0, 1] | None = Field(
        default=None,
        description="0 when the window ends the same day; 1 when it ends the next day.",
    )

    validate_weekdays = model_validator(mode="after")(weekdays_are_valid)


# --- Sleep -----------------------------------------------------------------------

class SleepRoutineWindowDraft(RoutineWindowDraft):
    weekdays: list[int] = Field(
        default_factory=list,
        description=(
            "Days this sleep schedule applies to, using 0=Monday through 6=Sunday. "
            "These are wake days; execution maps an overnight bedtime to the prior date."
        ),
    )


class SleepRoutineDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["sleep"] = "sleep"
    name: str | None = Field(
        default="Sleep",
        max_length=200,
        description="Optional sleep schedule name; use Sleep when no specific name is needed.",
    )
    location: str | None = Field(
        default=None,
        max_length=500,
        description="Optional sleep location; null when unspecified.",
    )
    effective_from: date | None = Field(
        default=None,
        description=(
            "Leave null to apply immediately on approval. Do not ask for a start date; "
            "only set this if the user explicitly supplies one."
        ),
    )
    effective_until: date | None = Field(
        default=None,
        description=(
            "Leave null for an ongoing sleep schedule. Do not ask for an end date; "
            "only set this if the user explicitly supplies one."
        ),
    )
    windows: list[SleepRoutineWindowDraft] = Field(default_factory=list)

    validate_effective_dates = model_validator(mode="after")(ends_after_it_starts)


def prepare_execution(payload: dict[str, Any], timezone_name: str = "UTC") -> dict[str, Any]:
    prepared = SLEEP_ROUTINE.prepare_draft(payload)
    if not prepared.ready:
        raise ArtifactDefinitionError(
            "The sleep routine needs more information before it can be accepted.",
            prepared.missing_fields,
        )
    draft = SleepRoutineDraftPayload.model_validate(prepared.payload)
    assert draft.name is not None
    effective_from: date = draft.effective_from or user_now(timezone_name).date()
    windows = []
    for window in draft.windows:
        assert window.start_local_time is not None
        assert window.end_local_time is not None
        start_weekdays, end_day_offset = sleep_start_days(
            window.weekdays, window.start_local_time, window.end_local_time
        )
        windows.append(
            {
                "id": window.id,
                "weekdays": start_weekdays,
                "start_local_time": window.start_local_time,
                "end_local_time": window.end_local_time,
                "end_day_offset": end_day_offset,
            }
        )
    command = CalendarRoutineUpsert(
        kind="sleep",
        name=draft.name.strip(),
        location=draft.location.strip() if draft.location and draft.location.strip() else None,
        effective_from=effective_from,
        effective_until=draft.effective_until,
        windows=windows,
    )
    return command.model_dump(mode="json")


def _draft_payload_with_existing_schedule(
    db: Session, user_id: str, payload: dict[str, Any], timezone_name: str = "UTC"
) -> dict[str, Any]:
    """Use the active schedule as the editable baseline instead of an empty draft."""
    if payload.get("windows"):
        return payload
    existing = get_calendar_routine(user_id, "sleep", db)
    if existing is None:
        return payload
    return {
        "kind": "sleep",
        "name": existing.name,
        "location": existing.location,
        "effective_from": existing.effective_from,
        "effective_until": existing.effective_until,
        "windows": sleep_routine_wake_day_payload(existing)["windows"],
    }


def sleep_draft_note(db: Session, user_id: str, command: ValidatedArtifactCommand) -> str | None:
    """A new sleep draft replaces the schedule already there, which the agent should say."""
    if get_calendar_routine(user_id, "sleep", db) is not None:
        return "Approving it updates the user's existing sleep schedule."
    return None


SLEEP_ROUTINE = routine_artifact(
    "sleep", "Sleep", SleepRoutineDraftPayload, prepare_execution=prepare_execution,
    prepare_existing=_draft_payload_with_existing_schedule, draft_note=sleep_draft_note,
)


# --- Work ------------------------------------------------------------------------

class WorkRoutineDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["work"] = "work"
    name: str | None = Field(
        default="Work",
        max_length=200,
        description="Specific routine name, such as a company name; omit the Work type label.",
    )
    location: str | None = Field(
        default=None,
        max_length=500,
        description="Optional work location or address; null when unspecified or remote.",
    )
    effective_from: date | None = Field(
        default=None,
        description="Local calendar date when the routine begins; null means immediately.",
    )
    effective_until: date | None = Field(
        default=None,
        description="Inclusive local calendar date when the routine ends; null means no end date.",
    )
    windows: list[RoutineWindowDraft] = Field(default_factory=list)

    validate_effective_dates = model_validator(mode="after")(ends_after_it_starts)


WORK_ROUTINE = routine_artifact("work", "Work", WorkRoutineDraftPayload)


# --- Other -----------------------------------------------------------------------

class OtherRoutineDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["other"] = "other"
    name: str | None = Field(
        default=None,
        max_length=200,
        description="Descriptive routine name, such as Gym, Class, or Volunteering.",
    )
    location: str | None = Field(
        default=None,
        max_length=500,
        description="Optional routine location or address; null when unspecified.",
    )
    effective_from: date | None = Field(
        default=None,
        description="Local calendar date when the routine begins; null means immediately.",
    )
    effective_until: date | None = Field(
        default=None,
        description="Inclusive local calendar date when the routine ends; null means no end date.",
    )
    windows: list[RoutineWindowDraft] = Field(default_factory=list)

    validate_effective_dates = model_validator(mode="after")(ends_after_it_starts)


OTHER_ROUTINE = routine_artifact("other", "Other", OtherRoutineDraftPayload)


# --- Events ----------------------------------------------------------------------
#
# An event proposal adds an event or changes one, with the routine changes it
# owns. A one-day change skips or retimes a single occurrence of a routine. A
# removal deletes an event or a whole routine. Each checks, when approved, that
# what it names is still as it was proposed, and fails with a code otherwise.

def _event_error(error: EventError) -> ArtifactExecutionError:
    return ArtifactExecutionError(error.code, str(error), retryable=False)


def _calendar_link(day: dt.date | str, event_id: str | None = None) -> str:
    return f"/?calendar=open&date={day}" + (f"&event_id={event_id}" if event_id else "")


def _clock(value: dt.datetime) -> str:
    return f"{value.hour % 12 or 12}:{value.minute:02d} {'PM' if value.hour >= 12 else 'AM'}"


def _day(value: dt.date) -> str:
    return f"{value.strftime('%a')}, {value.strftime('%b')} {value.day}"


class EventDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(
        default=None,
        description="The Attune event to change, from find_calendar_items. Null to add a new event.",
    )
    target_updated_at: dt.datetime | None = Field(
        default=None, description="Leave null; it is filled in from the event being changed.",
    )
    title: str | None = Field(default=None, max_length=200, description="What the event is, e.g. Dentist.")
    location: str | None = Field(default=None, max_length=500, description="Optional place or address.")
    date: dt.date | None = Field(default=None, description="Local date the event starts.")
    start: dt.time | None = Field(default=None, description="Local start time, HH:MM.")
    end: dt.time | None = Field(default=None, description="Local end time, HH:MM; may be after midnight.")
    end_day_offset: Literal[0, 1] | None = Field(
        default=None, description="1 when it ends the next day. Leave null to work it out from the times.",
    )
    routine_changes: list[RoutineChange] = Field(
        default_factory=list,
        description=(
            "What happens to routines the event overlaps, from check_event_clashes (window_id and "
            "occurrence_date exactly as given there). Include one only when the user said what happens, "
            "e.g. leaving work early (end_earlier) or skipping it (skip); otherwise omit it and the "
            "routine stays as it is. An event that takes a routine's place that day (a ride instead of "
            "the run) skips it. margin_minutes is time to get there and back."
        ),
    )

    @model_validator(mode="after")
    def derive_offset(self):
        if self.end_day_offset is None and self.start and self.end:
            self.end_day_offset = 1 if self.end <= self.start else 0
        return self


def _prepare_event_draft(payload: dict[str, Any]) -> PreparedDraft:
    try:
        draft = EventDraftPayload.model_validate(payload).model_dump(mode="json")
    except ValidationError as error:
        raise ArtifactDefinitionError(str(error)) from error
    missing = [field for field in ("date", "start", "end") if not draft[field]]
    if not (draft["title"] or "").strip():
        missing.insert(0, "title")
    return PreparedDraft(payload=draft, missing_fields=missing, ready=not missing)


def _existing_event(db: Session, user_id: str, payload: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    """A change starts from the event as it is: fill what the agent left out, and its version."""
    event_id = payload.get("event_id")
    if not event_id:
        return payload
    try:
        current = event_response(owned_event(user_id, event_id, db), db).model_dump(mode="json")
    except EventError as error:
        raise ModelRetry(f"{error} Use an event id from find_calendar_items.") from error
    filled = dict(payload)
    for field in ("title", "location", "date", "start", "end"):
        if filled.get(field) in (None, ""):
            filled[field] = current[field]
    if filled.get("end_day_offset") is None and not (payload.get("start") or payload.get("end")):
        filled["end_day_offset"] = current["end_day_offset"]
    if not filled.get("routine_changes"):
        filled["routine_changes"] = current["routine_changes"]
    filled["target_updated_at"] = current["updated_at"]
    return filled


def _event_execution(payload: dict[str, Any], timezone_name: str = "UTC") -> dict[str, Any]:
    prepared = _prepare_event_draft(payload)
    if not prepared.ready:
        raise ArtifactDefinitionError("The event needs more information before it can be added.", prepared.missing_fields)
    draft = prepared.payload
    command = CalendarEventInput.model_validate({
        "title": draft["title"], "location": draft["location"], "date": draft["date"],
        "start": draft["start"], "end": draft["end"], "end_day_offset": draft["end_day_offset"],
        "routine_changes": draft["routine_changes"], "expected_updated_at": draft["target_updated_at"],
    })
    return {"event_id": draft["event_id"], "timezone": timezone_name, "event": command.model_dump(mode="json")}


def _execute_event(db: Session, user_id: str, approved: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    command = CalendarEventInput.model_validate(approved["event"])
    try:
        if approved.get("event_id"):
            event = update_event(user_id, approved["event_id"], command, db, approved["timezone"])
        else:
            event = create_event(user_id, command, db, approved["timezone"], source="assistant")
        db.commit()
    except EventError as error:
        db.rollback()
        raise _event_error(error) from error
    return {
        "resource_type": "calendar_event", "resource_id": event.id,
        "href": _calendar_link(command.date, event.id),
        "idempotency_key": artifact_id,
    }


EVENT = ArtifactDefinition(
    type="event", schema_version=1, draft_schema=EventDraftPayload,
    prepare_draft=_prepare_event_draft, prepare_execution=_event_execution, execute=_execute_event,
    required_interface=KEY, prepare_existing=_existing_event,
    display_title=lambda payload: (payload.get("title") or "").strip() or "Event",
)


# --- One-day changes -------------------------------------------------------------

class OccurrenceChangeDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_id: str | None = Field(default=None, description="The routine window, from find_calendar_items.")
    occurrence_date: dt.date | None = Field(
        default=None,
        description="The day the occurrence starts, exactly as find_calendar_items gives it (bedtime's day for sleep).",
    )
    operation: Literal["skip", "custom"] = Field(
        default="skip", description="skip drops that day; custom gives it its own start and end.",
    )
    start: dt.time | None = Field(default=None, description="For custom: that day's local start time.")
    end: dt.time | None = Field(default=None, description="For custom: that day's local end time.")
    # Filled in when drafted, for the card: what it changes, and any change already there.
    routine_title: str | None = None
    routine_kind: Literal["sleep", "work", "other"] | None = None
    base_start: dt.datetime | None = None
    base_end: dt.datetime | None = None
    target_created_at: dt.datetime | None = None


def _prepare_occurrence_draft(payload: dict[str, Any]) -> PreparedDraft:
    try:
        draft = OccurrenceChangeDraftPayload.model_validate(payload).model_dump(mode="json")
    except ValidationError as error:
        raise ArtifactDefinitionError(str(error)) from error
    missing = [field for field in ("window_id", "occurrence_date") if not draft[field]]
    if draft["operation"] == "custom":
        missing += [field for field in ("start", "end") if not draft[field]]
    return PreparedDraft(payload=draft, missing_fields=missing, ready=not missing)


def _existing_occurrence(db: Session, user_id: str, payload: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    from app.interfaces.calendar.events import find_occurrence

    if not payload.get("window_id") or not payload.get("occurrence_date"):
        return payload
    day = dt.date.fromisoformat(str(payload["occurrence_date"]))
    occurrence = find_occurrence(user_id, db, payload["window_id"], day, timezone_name)
    if occurrence is None:
        raise ModelRetry("That routine has no occurrence starting on that day. Use find_calendar_items.")
    # A day given the times it already has is a card that changes nothing. It was
    # proposed for "make Thursday's run a ride", which is another thing in its place.
    if payload.get("operation") == "custom" and len(occurrence.segments) == 1:
        current = tuple(moment.astimezone(occurrence.timezone).strftime("%H:%M") for moment in occurrence.segments[0])
        if current == (str(payload.get("start"))[:5], str(payload.get("end"))[:5]):
            raise ModelRetry(
                "That day already has those times, so this would change nothing. A one-day change only skips "
                "the day or gives it other times; it cannot rename it or turn it into something else. To do "
                "something else in its place, propose an event for that, with a routine_changes entry that "
                "skips this routine that day."
            )
    direct = db.query(CalendarRoutineException).filter(
        CalendarRoutineException.window_id == payload["window_id"],
        CalendarRoutineException.occurrence_date == day,
        CalendarRoutineException.source == "direct",
    ).one_or_none()
    return {
        **payload,
        "routine_title": routine_display_title(occurrence.routine.kind, occurrence.routine.name),
        "routine_kind": occurrence.routine.kind,
        "base_start": occurrence.base_start.isoformat(), "base_end": occurrence.base_end.isoformat(),
        "target_created_at": direct.created_at.isoformat() if direct else None,
    }


def _occurrence_execution(payload: dict[str, Any], timezone_name: str = "UTC") -> dict[str, Any]:
    prepared = _prepare_occurrence_draft(payload)
    if not prepared.ready:
        raise ArtifactDefinitionError("The change needs a routine, a day and, for new times, both times.", prepared.missing_fields)
    return {**prepared.payload, "timezone": timezone_name}


def _nearest_offset(clock: str, around: int) -> int:
    """A clock time as minutes on the occurrence's start day, on whichever day is nearest `around`."""
    hour, minute = (int(part) for part in clock.split(":")[:2])
    minutes = hour * 60 + minute
    return min((minutes + days * 1440 for days in (-1, 0, 1, 2)), key=lambda value: abs(value - around))


def _execute_occurrence(db: Session, user_id: str, approved: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    from app.interfaces.calendar.events import find_occurrence, offset_of

    day = dt.date.fromisoformat(approved["occurrence_date"])
    occurrence = find_occurrence(user_id, db, approved["window_id"], day, approved["timezone"])
    if occurrence is None:
        raise ArtifactExecutionError("occurrence_missing", "That routine doesn't happen on that day anymore.", retryable=False)
    change = {"operation": approved["operation"], "expected_created_at": approved.get("target_created_at")}
    if approved["operation"] == "custom":
        start = _nearest_offset(approved["start"], offset_of(day, occurrence.base_start, occurrence.timezone))
        end = _nearest_offset(approved["end"], offset_of(day, occurrence.base_end, occurrence.timezone))
        if end <= start:
            end += 1440
        change |= {"start_offset_minutes": start, "end_offset_minutes": end}
    try:
        set_occurrence_change(user_id, approved["window_id"], day, OccurrenceChangeInput.model_validate(change), db, approved["timezone"])
        db.commit()
    except EventError as error:
        db.rollback()
        raise _event_error(error) from error
    except ValidationError as error:
        db.rollback()
        raise ArtifactExecutionError("invalid", "Those times don't make a day of this routine.", retryable=False) from error
    return {
        "resource_type": "calendar_occurrence", "resource_id": f"{approved['window_id']}:{day.isoformat()}",
        "href": _calendar_link(day), "idempotency_key": artifact_id,
    }


OCCURRENCE_CHANGE = ArtifactDefinition(
    type="occurrence_change", schema_version=1, draft_schema=OccurrenceChangeDraftPayload,
    prepare_draft=_prepare_occurrence_draft, prepare_execution=_occurrence_execution,
    execute=_execute_occurrence, required_interface=KEY, prepare_existing=_existing_occurrence,
    display_title=lambda payload: f"{payload.get('routine_title') or 'Routine'}, one day",
)


# --- Removals --------------------------------------------------------------------

class CalendarRemovalDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["event", "sleep", "work", "other"] | None = Field(
        default=None, description="An Attune event, or a whole routine of that kind.",
    )
    target_id: str | None = Field(default=None, description="The event or routine id, from find_calendar_items or read_routines.")
    # Filled in when drafted, for the card: what goes, what comes back, and its version.
    title: str | None = None
    summary: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    target_updated_at: dt.datetime | None = None


def _prepare_removal_draft(payload: dict[str, Any]) -> PreparedDraft:
    try:
        draft = CalendarRemovalDraftPayload.model_validate(payload).model_dump(mode="json")
    except ValidationError as error:
        raise ArtifactDefinitionError(str(error)) from error
    missing = [field for field in ("target_type", "target_id") if not draft[field]]
    return PreparedDraft(payload=draft, missing_fields=missing, ready=not missing)


def _window_summary(window: CalendarRoutineWindow) -> str:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days = ", ".join(names[day] for day in sorted(window.weekdays))
    start = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(window.start_local_time))
    end = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(window.end_local_time))
    return f"{days} · {_clock(start)}–{_clock(end)}"


def _existing_removal(db: Session, user_id: str, payload: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    from app.interfaces.calendar.events import base_span

    target_type, target_id = payload.get("target_type"), payload.get("target_id")
    if not target_type or not target_id:
        return payload
    if target_type == "event":
        try:
            event = owned_event(user_id, target_id, db)
        except EventError as error:
            raise ModelRetry(f"{error} Use an event id from find_calendar_items.") from error
        tz = user_timezone(event.timezone)
        start, end = event.starts_at.astimezone(tz), event.ends_at.astimezone(tz)
        consequences = []
        for change in db.query(CalendarRoutineException).filter(CalendarRoutineException.event_id == event.id):
            window = db.get(CalendarRoutineWindow, change.window_id)
            if window is None:
                continue
            base_start, base_end = base_span(window, change.occurrence_date, user_timezone(timezone_name))
            title = routine_display_title(window.routine.kind, window.routine.name)
            consequences.append(
                f"{title} returns to {_clock(base_start)}–{_clock(base_end)} on {_day(change.occurrence_date)}"
            )
        return {
            **payload, "title": event.title,
            "summary": [f"{_day(start.date())} · {_clock(start)}–{_clock(end)}"],
            "consequences": consequences, "target_updated_at": event.updated_at.isoformat(),
        }
    routine = db.query(CalendarRoutine).filter(
        CalendarRoutine.id == target_id, CalendarRoutine.user_id == user_id, CalendarRoutine.kind == target_type,
    ).one_or_none()
    if routine is None:
        raise ModelRetry("That routine isn't in the calendar. Use an id from read_routines.")
    consequences = []
    event_count = db.query(CalendarRoutineException.event_id).filter(
        CalendarRoutineException.window_id.in_([window.id for window in routine.windows]),
        CalendarRoutineException.event_id.isnot(None),
    ).distinct().count() if routine.windows else 0
    if event_count:
        consequences.append(
            f"{event_count} event{'s' if event_count != 1 else ''} change{'' if event_count != 1 else 's'} "
            "this routine. The events stay, and their changes to it are removed."
        )
    return {
        **payload, "title": routine_display_title(routine.kind, routine.name),
        "summary": [_window_summary(window) for window in routine.windows],
        "consequences": consequences, "target_updated_at": routine.updated_at.isoformat(),
    }


def _removal_execution(payload: dict[str, Any], timezone_name: str = "UTC") -> dict[str, Any]:
    prepared = _prepare_removal_draft(payload)
    if not prepared.ready:
        raise ArtifactDefinitionError("The removal needs to name what goes.", prepared.missing_fields)
    return prepared.payload


def _execute_removal(db: Session, user_id: str, approved: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    expected = dt.datetime.fromisoformat(approved["target_updated_at"]) if approved.get("target_updated_at") else None
    if approved["target_type"] == "event":
        try:
            delete_event(user_id, approved["target_id"], db, expected_updated_at=expected)
            db.commit()
        except EventError as error:
            db.rollback()
            raise _event_error(error) from error
        return {
            "resource_type": "calendar_event", "resource_id": approved["target_id"], "href": "/?calendar=open",
            "idempotency_key": artifact_id,
        }
    routine = db.query(CalendarRoutine).filter(
        CalendarRoutine.id == approved["target_id"], CalendarRoutine.user_id == user_id,
    ).with_for_update().one_or_none()
    if routine is None:
        raise ArtifactExecutionError("target_missing", "That routine isn't in the calendar anymore.", retryable=False)
    updated = routine.updated_at if routine.updated_at.tzinfo else routine.updated_at.replace(tzinfo=dt.UTC)
    if expected and abs((updated - expected).total_seconds()) >= 1:
        raise ArtifactExecutionError("target_changed", "That routine changed since this was proposed.", retryable=False)
    delete_calendar_routine(user_id, approved["target_type"], db, approved["target_id"])
    db.commit()
    return {
        "resource_type": "calendar_routine", "resource_id": approved["target_id"], "href": "/?calendar=open",
        "idempotency_key": artifact_id,
    }


CALENDAR_REMOVAL = ArtifactDefinition(
    type="calendar_removal", schema_version=1, draft_schema=CalendarRemovalDraftPayload,
    prepare_draft=_prepare_removal_draft, prepare_execution=_removal_execution, execute=_execute_removal,
    required_interface=KEY, prepare_existing=_existing_removal,
    display_title=lambda payload: f"Remove {payload.get('title') or 'from the calendar'}",
)


# Offered to the agent in this order while the calendar is enabled.
ARTIFACTS = (OTHER_ROUTINE, SLEEP_ROUTINE, WORK_ROUTINE, EVENT, OCCURRENCE_CHANGE, CALENDAR_REMOVAL)


# === Tools =========================================================================
#
# The calendar's tools: reads of scheduled time, routines, events and clashes, and
# drafting tools for its artifacts (sleep, work and other routines; events; one-day
# changes to a routine; removals).
#
# Each artifact type gets one `propose_<type>` tool, typed by its draft payload,
# that creates a proposal or changes one already in the conversation. It only
# drafts: the user sees a proposal card, and nothing is scheduled until they
# approve it.

READ_TIMEOUT_SECONDS = 20


# --- Reads -----------------------------------------------------------------------

def read_calendar(ctx: RunContext[TurnDeps], start_day: date, day_count: int = 7) -> dict:
    """Read current scheduled time for a date range (at most 31 days)."""
    require_enabled(ctx.deps.user_id, KEY)
    if not 1 <= day_count <= 31:
        raise ModelRetry("day_count must be between 1 and 31.")
    with SessionLocal() as db:
        days = get_timeline(ctx.deps.user_id, db, start_day=start_day, day_count=day_count,
                            timezone_name=ctx.deps.timezone_name)
        return {"days": [day.model_dump(mode="json") for day in days]}


def read_routines(ctx: RunContext[TurnDeps]) -> dict:
    """Read the user's current active routines before proposing changes."""
    require_enabled(ctx.deps.user_id, KEY)
    with SessionLocal() as db:
        return {kind: [routine.model_dump(mode="json") for routine in routines]
                for kind, routines in get_calendar_routines(ctx.deps.user_id, db).items()}


def find_calendar_items(ctx: RunContext[TurnDeps], query: str = "", start_day: date | None = None,
                        day_count: int = 14) -> dict:
    """Find events and routine occurrences by what they're called, before changing or removing one.

    Matches titles containing `query` (empty matches everything) from `start_day`
    (today by default) for `day_count` days, at most 31, next upcoming first. For a
    routine occurrence, `occurrence_date` is the day it starts, which for sleep is
    the night before the day you wake.
    """
    require_enabled(ctx.deps.user_id, KEY)
    if not 1 <= day_count <= 31:
        raise ModelRetry("day_count must be between 1 and 31.")
    now = user_now(ctx.deps.timezone_name)
    first = start_day or now.date()
    needle = query.casefold().strip()
    with SessionLocal() as db:
        days = get_timeline(ctx.deps.user_id, db, start_day=first, day_count=day_count,
                            timezone_name=ctx.deps.timezone_name)
    items: dict[str, dict] = {}
    for day in days:
        for block in day.blocks:
            if needle and needle not in block.title.casefold() and needle != block.kind:
                continue
            key = block.id.split(":day:")[0]
            if key in items:
                continue
            item = {"title": block.title, "start": block.start, "end": block.end, "location": block.location}
            if block.event_id:
                item |= {"type": "event", "event_id": block.event_id}
            else:
                item |= {"type": "routine", "routine_id": block.routine_id, "routine_kind": block.kind,
                         "window_id": block.window_id, "occurrence_date": block.occurrence_date,
                         "changed": block.changed_by}
            items[key] = item
    upcoming = sorted(items.values(), key=lambda item: (datetime.fromisoformat(item["end"]) < now, item["start"]))
    return {"items": upcoming[:25], "more": max(0, len(upcoming) - 25)}


def check_event_clashes(ctx: RunContext[TurnDeps], date: date, start: time, end: time,
                        event_id: str | None = None) -> dict:
    """What an event at this time would overlap, before proposing it.

    Routines come with the changes they can take (operation and a label saying
    what it means); pass the one the user asked for as a routine change on the
    proposal, or none to leave the routine as it is. `event_id` is the event being
    changed, left out of its own clashes. End before start means it ends the next day.
    """
    require_enabled(ctx.deps.user_id, KEY)
    starts_at, ends_at = event_span(date, start.strftime("%H:%M"), end.strftime("%H:%M"), int(end <= start),
                                    ctx.deps.timezone_name)
    with SessionLocal() as db:
        clashes = find_clashes(ctx.deps.user_id, db, starts_at, ends_at, ctx.deps.timezone_name,
                               exclude_event_id=event_id)
    return {"clashes": [
        {"type": clash.type, "title": clash.title, "start": clash.start, "end": clash.end,
         **({"window_id": clash.window_id, "occurrence_date": clash.occurrence_date, "locked": clash.locked,
             "options": [{"operation": option.operation, "label": option.label} for option in clash.options]}
            if clash.type == "routine" else {})}
        for clash in clashes
    ]}


# --- Drafting ---------------------------------------------------------------------

# What each type's propose tool adds to its description, where the payload's own
# field descriptions aren't enough.
GUIDANCE = {
    "event": (
        " One call per event; several in one reply make a set the user pages through and can approve "
        "together. To change an existing event, pass its event_id from find_calendar_items and only "
        "what changes. Call check_event_clashes first, and include routine_changes only for what the "
        "user said happens to the routine; never ask about clashes."
    ),
    "occurrence_change": (
        " Skips or retimes one day of a routine with no event involved, e.g. off work Friday or "
        "sleeping in Saturday. Find the occurrence with find_calendar_items. It cannot rename the day or "
        "make it something else: for that, propose an event that skips the routine that day."
    ),
    "calendar_removal": (
        " Removes an event or a whole routine. Deleting always goes through this proposal."
    ),
}


def register(tools: ChatToolbox) -> None:
    tools.tool(read_calendar, label="Checking your calendar…", timeout=READ_TIMEOUT_SECONDS)
    tools.tool(read_routines, label="Checking your routines…", timeout=READ_TIMEOUT_SECONDS)
    tools.tool(find_calendar_items, label="Looking through your calendar…", timeout=READ_TIMEOUT_SECONDS)
    tools.tool(check_event_clashes, label="Checking what that overlaps…", timeout=READ_TIMEOUT_SECONDS)
    for definition in (OTHER_ROUTINE, SLEEP_ROUTINE, WORK_ROUTINE, EVENT, OCCURRENCE_CHANGE, CALENDAR_REMOVAL):
        register_drafting(tools, definition, GUIDANCE.get(definition.type, ""))
