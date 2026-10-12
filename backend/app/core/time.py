from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Header


def normalize_timezone(value: str | None, fallback: str = "UTC") -> str:
    candidate = value or fallback
    try:
        ZoneInfo(candidate)
    except ValueError, ZoneInfoNotFoundError:
        # A key that is not a plain zone name ("../UTC") is a ValueError, not a missing zone.
        return fallback
    return candidate


def user_timezone(value: str | None) -> ZoneInfo:
    return ZoneInfo(normalize_timezone(value))


def user_now(value: str | None) -> datetime:
    return datetime.now(user_timezone(value))


def utc_now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    """A stored time read back without its zone is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def request_timezone(
    x_user_timezone: str | None = Header(default=None, alias="X-User-Timezone"),
) -> str:
    """Resolve the device timezone for this request, falling back to UTC."""
    return normalize_timezone(x_user_timezone, fallback="UTC")
