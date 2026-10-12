"""What no one domain owns: onboarding, and deleting an account.

Onboarding is the shell revealing itself as it has something to show: a new
account starts with chat and the account button, and the memory rail appears,
pointed out once, the first time memory learns something.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import DateTime, delete, ForeignKey, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.auth import User, get_current_user
from app.core.database import Base, get_db
from app.core.time import utc_now


# --- Tables ----------------------------------------------------------------------

class OnboardingProgress(Base):
    """Which parts of the shell have been pointed out."""

    __tablename__ = "onboarding_progress"

    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), primary_key=True)
    # When the memory rail was first pointed out, once it had something in it.
    memory_introduced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Onboarding ------------------------------------------------------------------

router = APIRouter()


class OnboardingState(BaseModel):
    memory_introduced: bool


def progress(db: Session, user_id: str) -> OnboardingProgress:
    # Locks even the first request, before the progress row exists, so two tabs
    # cannot both create it.
    db.query(User).filter(User.id == user_id).with_for_update().one()
    row = db.get(OnboardingProgress, user_id)
    if row is None:
        row = OnboardingProgress(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def state(row: OnboardingProgress) -> OnboardingState:
    return OnboardingState(memory_introduced=row.memory_introduced_at is not None)


@router.get("/api/onboarding", response_model=OnboardingState)
def read_onboarding(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = state(progress(db, current_user.id))
    db.commit()
    return result


@router.post("/api/onboarding/memory-introduced", response_model=OnboardingState)
def memory_introduced(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = progress(db, current_user.id)
    if row.memory_introduced_at is None:
        row.memory_introduced_at = utc_now()
    db.commit()
    return state(row)


# --- The account -----------------------------------------------------------------

@router.delete("/api/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    """Delete the account with everything it made. Every user table cascades from auth_users.

    What it spent of the day's allowance stays against its address, or logging out
    of a demo and starting another would start the allowance again.
    """
    from app.chat.runs import keep_spending  # Chat imports half the application.

    keep_spending(db, current_user.id)
    db.execute(delete(User).where(User.id == current_user.id))
    db.commit()
