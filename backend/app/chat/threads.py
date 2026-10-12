"""Conversation threads and their messages, as the API shows them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast, List, Literal, Optional, TYPE_CHECKING
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, Session

from app.chat.artifacts import ArtifactMessageEvent, ArtifactResponse
from app.core.database import Base
from app.core.time import utc_now

if TYPE_CHECKING:
    from app.chat.artifacts import (
        ConversationArtifact, ConversationMessageArtifactEvent,
    )


# --- Tables ----------------------------------------------------------------------

class ConversationThread(Base):
    __tablename__ = "conversation_threads"
    __table_args__ = (
        UniqueConstraint("user_id", "handoff_request_id", name="uq_thread_handoff_request"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_conversation_thread_status"),
        Index("ix_conversation_thread_user_status", "user_id", "status"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    # Archiving only hides a conversation from the session list. Nothing it taught
    # memory is touched, which is what separates it from deletion.
    status: Mapped[str] = mapped_column(String, nullable=False, default="active", server_default="active")
    archive_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    handoff_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    previous_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # When the session began. The web app names it from this, in the user's own time.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    # What this session has already pulled in from memory, most recent last. Later
    # turns keep seeing it, so a topic the conversation is on stays in view, and the
    # list is bounded so moving on eventually drops what is no longer being discussed.
    recalled_fact_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    messages: Mapped[list[ConversationMessage]] = relationship(
        "ConversationMessage", back_populates="thread", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[ConversationArtifact]] = relationship(
        "ConversationArtifact",
        cascade="all, delete-orphan",
        foreign_keys="ConversationArtifact.thread_id",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('streaming', 'completed', 'interrupted', 'failed')",
            name="ck_conversation_message_status",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False)
    author: Mapped[str] = mapped_column(String, nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)

    thread: Mapped[ConversationThread] = relationship("ConversationThread", back_populates="messages")
    artifact_events: Mapped[list[ConversationMessageArtifactEvent]] = relationship(
        "ConversationMessageArtifactEvent", cascade="all, delete-orphan", order_by="ConversationMessageArtifactEvent.created_at"
    )


# --- Contracts -------------------------------------------------------------------

class ChatThread(BaseModel):
    id: str
    created_at: datetime
    # Archived sessions still ship with the list; the drawer's toggle decides
    # whether they are shown, so flipping it costs no round trip.
    archived: bool = False


class NewSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previous_thread_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)
    client_request_id: str = Field(min_length=1, max_length=200)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=1, max_length=200)


class TurnAccepted(BaseModel):
    run_id: str
    thread_id: str
    user_message_id: str
    assistant_message_id: str
    thread_created: bool
    # Replay events after this sequence; everything up to it is in this response.
    cursor: int


class ChatThreadMessage(BaseModel):
    id: str
    thread_id: str
    author: Literal["user", "assistant"]
    content: str
    timestamp: str
    status: Literal["streaming", "completed", "interrupted", "failed"] = "completed"
    failure_code: Optional[str] = None
    # The run that wrote an assistant message, which is what a retry names.
    run_id: Optional[str] = None
    # Remember requests this reply queued; they are learned in the background.
    artifact_events: Optional[List[ArtifactMessageEvent]] = None


class ActiveRun(BaseModel):
    id: str
    status: Literal["queued", "running", "waiting"]
    assistant_message_id: str
    # The snapshot above already reflects every event up to this sequence.
    cursor: int
    activity: Optional[str] = None
    cancel_requested: bool = False
    # While waiting: the cards it waits on the user to decide.
    held_artifact_ids: List[str] = Field(default_factory=list)


class ChatThreadPage(BaseModel):
    threads: List[ChatThread]
    active_thread_id: str
    thread: List[ChatThreadMessage]
    artifacts: List[ArtifactResponse] = Field(default_factory=list)
    active_run: Optional[ActiveRun] = None


# --- Threads and messages --------------------------------------------------------

MessageAuthor = Literal["user", "assistant"]


def ordered_thread_messages(db: Session, thread_id: str) -> list[ConversationMessage]:
    return db.query(ConversationMessage).filter(
        ConversationMessage.thread_id == thread_id,
    ).order_by(ConversationMessage.timestamp.asc(), ConversationMessage.id.asc()).all()


def message_contract(message: ConversationMessage, run_id: str | None = None) -> ChatThreadMessage:
    return ChatThreadMessage(
        id=message.id,
        thread_id=message.thread_id,
        author=cast(MessageAuthor, message.author),
        content=message.content,
        timestamp=message.timestamp,
        status=cast(Any, message.status),
        failure_code=message.failure_code,
        run_id=run_id,
        artifact_events=[
            ArtifactMessageEvent(artifact_id=event.artifact_id, event=cast(Any, event.event),
                                 snapshot=cast(Any, event.snapshot), text_offset=event.text_offset)
            for event in message.artifact_events
        ] or None,
    )


def get_thread_messages(thread_id: str, user_id: str, db: Session) -> List[ChatThreadMessage]:
    from app.chat.runs import ConversationRun

    owned = db.query(ConversationThread.id).filter(
        ConversationThread.id == thread_id, ConversationThread.user_id == user_id,
    ).first()
    if owned is None:
        return []
    run_ids = {message_id: run_id for message_id, run_id in db.query(
        ConversationRun.assistant_message_id, ConversationRun.id,
    ).filter(ConversationRun.thread_id == thread_id).all()}
    return [
        message_contract(message, run_ids.get(message.id))
        for message in ordered_thread_messages(db, thread_id)
    ]


def add_message_to_db(
    db: Session,
    thread_id: str,
    author: MessageAuthor,
    content: str,
    status: str = "completed",
) -> ConversationMessage:
    now = utc_now()
    message = ConversationMessage(
        id=str(uuid.uuid7()),
        thread_id=thread_id,
        author=author,
        content=content,
        timestamp=now.isoformat(),
        created_at=now,
        status=status,
        completed_at=now if status == "completed" else None,
    )
    db.add(message)
    db.flush()
    return message
