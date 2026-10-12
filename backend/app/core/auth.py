"""Accounts: the tables Better Auth writes, who is asking, and expiring guests.

The web app signs people in; this side reads the tables it shares, resolves a
bearer session token to its user, and deletes demo accounts left unused.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, UTC
import logging
from typing import cast

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Boolean, CursorResult, DateTime, delete, exists, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, Session as DatabaseSession

from app.core.database import Base, get_db, SessionLocal
from app.core.time import utc_now


# --- Tables ----------------------------------------------------------------------

class User(Base):
    __tablename__ = "auth_users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    image: Mapped[str | None] = mapped_column(String, nullable=True)
    # A demo account: no way to sign in again until the user keeps it, by linking
    # Google or verifying an email and password.
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The terms the account was opened under, and when they were agreed to. The web
    # app stamps both as it creates the account, after the user agrees.
    terms_version: Mapped[str | None] = mapped_column(String, nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

class Account(Base):
    __tablename__ = "auth_accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[str] = mapped_column(String, nullable=False)
    access_token: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    id_token: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    password: Mapped[str | None] = mapped_column(String, nullable=True)
    password_expired: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

class Session(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

class Verification(Base):
    __tablename__ = "auth_verifications"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=True
    )


# --- The current user ------------------------------------------------------------

def get_current_user(
    request: Request,
    db: DatabaseSession = Depends(get_db),
) -> User:
    auth_header = request.headers.get("authorization")
    session_id = None
    if auth_header and auth_header.lower().startswith("bearer "):
        session_id = auth_header[7:].strip()
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    db_session = db.query(Session).filter(Session.token == session_id).first()
    if not db_session:
        raise HTTPException(status_code=401, detail="Session not found or invalid")
    expires_at = db_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < utc_now():
        raise HTTPException(status_code=401, detail="Session expired")

    user = db.query(User).filter(User.id == db_session.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# --- Expiring guests -------------------------------------------------------------

# A demo account nobody has used for this long is deleted with everything it made.
# Better Auth refreshes a session's updated_at at most daily while it is in use.
GUEST_IDLE = timedelta(days=7)
GUEST_SWEEP_SECONDS = 60 * 60

logger = logging.getLogger(__name__)


def expire_guests() -> int:
    """Delete guests idle past GUEST_IDLE. Every user table cascades from auth_users."""
    cutoff = utc_now() - GUEST_IDLE
    recent_session = exists().where(Session.user_id == User.id, Session.updated_at >= cutoff)
    with SessionLocal() as db:
        result = cast(CursorResult, db.execute(delete(User).where(
            User.is_anonymous.is_(True), User.updated_at < cutoff, ~recent_session,
        )))
        db.commit()
    return result.rowcount


async def guest_expiry_loop() -> None:
    while True:
        try:
            if removed := await asyncio.to_thread(expire_guests):
                logger.info("Deleted %d idle guest accounts.", removed)
        except Exception:
            logger.exception("Expiring guest accounts failed; trying again later.")
        await asyncio.sleep(GUEST_SWEEP_SECONDS)
