"""Five promises Attune makes, each checked without calling a model.

1. The event editor's preview of what a routine can do agrees with the server.
2. The migrations build the database the code expects.
3. The seeded demo loads into a new account.
4. Deleting an account leaves nothing of it behind.
5. The daily allowance is per address, however many accounts are used from it.

They run against a throwaway database (conftest.py), through the API as the web
app calls it. No worker runs here, so a message sent is queued and never answered.
"""

from datetime import date, datetime, time, timedelta, UTC
import json
from pathlib import Path
import subprocess
import uuid
from zoneinfo import ZoneInfo

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as Database

from app.config import DAILY_CHAT_DOLLARS_PER_ADDRESS, UNMEASURED_RUN_DOLLARS
from app.core.auth import Session, User
from app.core.database import Base, load_models, SessionLocal
from app.demo.snapshot import owned, SNAPSHOT, table, TABLES
from app.interfaces.calendar.events import Occurrence, routine_options
from app.interfaces.calendar.routines import CalendarRoutine, CalendarRoutineWindow
from app.main import app

WEB = Path(__file__).parents[2] / "web"
api = TestClient(app)


def new_account() -> tuple[str, dict[str, str]]:
    """A new demo account: its id, and the header that signs a request in as it."""
    user_id, token = str(uuid.uuid4()), uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(User(id=user_id, email=f"{user_id}@test.invalid", is_anonymous=True))
        db.flush()
        db.add(Session(id=str(uuid.uuid4()), token=token, user_id=user_id,
                       expires_at=datetime.now(UTC) + timedelta(hours=1)))
        db.commit()
    return user_id, {"Authorization": f"Bearer {token}"}


def send(signed_in: dict[str, str], address: str) -> int:
    """Send a chat message in a new conversation from a network address; the status that came back."""
    return api.post(
        "/api/chat/turns", headers={**signed_in, "X-Attune-Client-Address": address},
        json={"thread_id": "draft", "content": "Hello", "client_request_id": uuid.uuid4().hex},
    ).status_code


# --- 1. Clash options ------------------------------------------------------------
#
# The rule for what a routine can do about an event that overlaps it is written
# twice: `routine_options` here, which a save goes by, and `clashOptions` in
# web/src/lib/calendar/calendar.ts, which the editor shows while the server's
# answer is on its way. A change to one needs the same change to the other.

DAY = date(2026, 3, 2)
# Routine spans, in minutes on DAY's clock: overnight, a working day, one hour.
SPANS = ((1380, 1860), (540, 1020), (1080, 1140))


def at(minutes: int) -> datetime:
    return datetime.combine(DAY, time(), UTC) + timedelta(minutes=minutes)


def test_clash_options_match():
    cases = []
    for start, end in SPANS:
        # Event edges before, on, inside and after the routine's own.
        marks = sorted({start - 120, start - 30, start, start + 30, (start + end) // 2,
                        end - 30, end, end + 30, end + 120})
        cases += [
            (kind, (start, end), (event_start, event_end), margin)
            for kind in ("sleep", "work", "other")
            for event_start in marks for event_end in marks if event_start < event_end
            for margin in (0, 15, 30, 60)
        ]

    asked = [[kind, "Run", span, event, margin, DAY.isoformat()] for kind, span, event, margin in cases]
    answered = subprocess.run(
        ["node", "scripts/clash-options.js"], cwd=WEB, input=json.dumps(asked),
        capture_output=True, encoding="utf-8", check=True,
    )

    for case, browser in zip(cases, json.loads(answered.stdout), strict=True):
        kind, span, event, margin = case
        occurrence = Occurrence(
            routine=CalendarRoutine(kind=kind, name="Run"), window=CalendarRoutineWindow(id="window"),
            start_day=DAY, timezone=ZoneInfo("UTC"), base_start=at(span[0]), base_end=at(span[1]),
        )
        server = [
            # The server's times carry a UTC offset; the browser's are the same times without one.
            {"operation": option.operation, "label": option.label,
             "segments": [[start[:19], end[:19]] for start, end in option.segments]}
            for option in routine_options(occurrence, at(event[0]), at(event[1]), margin)
        ]
        assert server == browser, case


# --- 2. Migrations ---------------------------------------------------------------

def test_migrations_match_the_tables(migrations):
    """The database was just built from nothing by the migrations (conftest.py).
    This fails when a table in the code has changed and no migration says so."""
    command.check(migrations)


# --- 3. The seeded demo ----------------------------------------------------------

def test_seeded_demo_loads():
    user_id, signed_in = new_account()
    assert api.post("/api/demo/seed", headers=signed_in).status_code == 204

    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        arrived = {name: db.scalar(select(func.count()).select_from(table(name)).where(owned(name, user_id)))
                   for name, _, _ in TABLES}
        account = db.get(User, user_id)
        assert account is not None and account.name == snapshot["name"]
    assert arrived == {name: len(rows) for name, rows in snapshot["tables"].items()}

    # And what a visitor opens first reads it back.
    for page in ("/api/chat/thread", "/api/memory/panel", "/api/calendar"):
        assert api.get(page, headers=signed_in).status_code == 200, page


# --- 4. Deleting an account ------------------------------------------------------

# Sign-in links and codes are kept by email address, not by account, and expire.
# What a deleted account spent is kept for a day by network address (promise 5).
NOT_AN_ACCOUNTS = {"auth_verifications", "deleted_account_spending"}
# Tables the account made below never writes to; the first half still covers them.
NOT_FILLED = {"auth_accounts", "conversation_tool_receipts", "interfaces_calendar_occurrence_notes",
              "memory_exclusions"}


def goes_with_its_account(a_table) -> bool:
    """Whether deleting a user deletes this table's rows: it points at the user, or
    at a table that does, and says to go when what it points at goes."""
    return any(
        key.ondelete == "CASCADE" and (key.column.table.name == "auth_users" or goes_with_its_account(key.column.table))
        for key in a_table.foreign_keys if key.column.table is not a_table
    )


def rows(db: Database) -> dict[str, int]:
    return {name: db.scalar(select(func.count()).select_from(a_table)) or 0
            for name, a_table in Base.metadata.tables.items()}


def test_deleting_an_account_leaves_nothing():
    load_models()
    kept = {name for name, a_table in Base.metadata.tables.items() if not goes_with_its_account(a_table)}
    assert kept == {"auth_users"} | NOT_AN_ACCOUNTS

    with SessionLocal() as db:
        before = rows(db)
    _, signed_in = new_account()
    address = {"X-Attune-Client-Address": "203.0.113.4"}
    assert api.post("/api/demo/seed", headers=signed_in).status_code == 204
    assert send(signed_in, address["X-Attune-Client-Address"]) == 202
    assert api.get("/api/onboarding", headers=signed_in).status_code == 200
    assert api.post("/api/memory/review", headers={**signed_in, **address}).status_code == 200
    with SessionLocal() as db:
        filled = rows(db)
    assert {name for name in before if filled[name] == before[name]} == NOT_AN_ACCOUNTS | NOT_FILLED

    assert api.delete("/api/account", headers=signed_in).status_code == 204
    with SessionLocal() as db:
        after = rows(db)
    # All that is left is the note of what it spent, which names an address and no account.
    assert after.pop("deleted_account_spending") == before.pop("deleted_account_spending") + 1
    assert after == before


# --- 5. The daily allowance ------------------------------------------------------

def test_daily_allowance_is_per_address():
    _, first = new_account()
    _, second = new_account()
    home, elsewhere = "203.0.113.5", "203.0.113.6"

    # Nothing answers here, so each message is charged as a reply that never finished.
    sent = 0
    while send(first, home) == 202:
        sent += 1
        assert (sent - 1) * UNMEASURED_RUN_DOLLARS < DAILY_CHAT_DOLLARS_PER_ADDRESS, "The allowance never ran out."
    assert sent > 0

    assert send(second, home) == 429
    assert send(second, elsewhere) == 202

    # Logging out of a demo deletes it, and a new one from there starts no fresh allowance.
    assert api.delete("/api/account", headers=first).status_code == 204
    _, third = new_account()
    assert send(third, home) == 429
