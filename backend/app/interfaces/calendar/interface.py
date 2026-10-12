"""The calendar as an interface: what it declares to the registry.
"""

from fastapi import APIRouter

from app.interfaces.descriptor import InterfaceDescriptor


# === What it tells the assistant ===================================================
#
# What the calendar adds to the assistant: its instructions. Its tools are in
# `chat.py`.
#
# It adds nothing to memory's outline. Routines and events live on the calendar,
# which chat reads directly; a copy in memory would only fall out of step with it.

# --- Instructions ----------------------------------------------------------------

AGENT_INSTRUCTIONS = """Calendar:
- For the user's schedule, read the calendar, not memory. Find an item with find_calendar_items or read_routines before changing it; use the next upcoming match.
- Routines need days and times. Work can't overlap other routines; other routines can't overlap work or sleep. If a start date isn't one of its days, mark it uncertain.
- Sleep needs days (the days they wake), bedtime and wake time. Bare hours mean night: "11-7" is 11 PM to 7 AM.
- Events need a title, date and time. Check clashes first; change a routine only if the user said to.
- One day of a routine can be skipped or given other times, nothing else. To do something different in its place that day ("cycle instead of the Thursday run"), propose an event for the new thing at that time, with a routine change that skips the routine that day.
- Deleting uses a removal proposal."""


# === The declaration ===============================================================
#
# The calendar as an interface: what it declares to the registry.

KEY = "calendar"


def _router() -> APIRouter:
    from app.interfaces.calendar.router import router

    return router


INTERFACE = InterfaceDescriptor(
    key=KEY,
    name="Calendar",
    description="Plan around your routines and scheduled time in Attune's own calendar.",
    agent_instructions=AGENT_INSTRUCTIONS,
    tools_summary=("read the user's schedule and routines, find events and routine days, check "
                   "clashes, and propose events, routines, one-day changes and removals"),
    router=_router,
)
