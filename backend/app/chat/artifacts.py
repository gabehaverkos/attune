"""Artifacts: the structured output of a tool call, shown to the user as a proposal card.

Part of the conversation, not of the agent: the agent drafts one with a tool
(`chat/tools.py`), and the user edits and approves it here. In order below: what
an artifact is (its tables, contracts and the types on offer), what drafting and
approval both need, an artifact while it is being written, what happens once the
user acts on it, and the system's own types. An interface's types are in its own
package (`app/interfaces/<key>/chat.py`). The API is in `chat/router.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import ModelRetry
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, or_, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, Session
from typing import Any, cast, Literal, TYPE_CHECKING

from app.core.database import Base
from app.core.time import aware, utc_now


# === What an artifact is ===========================================================
#
# What an artifact is: its tables, its contracts, and the types that can be drafted.
#
# An artifact type is either the system's, offered whatever the user has enabled,
# or an interface's, offered while that interface is enabled. Each type is defined
# from this module, so the system's own types are at its end, and an interface's
# (`app/interfaces/<key>/chat.py`) are imported where they are used.

# --- Tables ----------------------------------------------------------------------

class ConversationMessageArtifactEvent(Base):
    __tablename__ = "conversation_message_artifact_events"
    __table_args__ = (
        CheckConstraint(
            "event IN ('created', 'updated', 'superseded')",
            name="ck_message_artifact_event",
        ),
        Index("ix_message_artifact_event_message", "message_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    message_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False
    )
    artifact_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation_artifacts.id", ondelete="CASCADE"), nullable=False
    )
    event: Mapped[str] = mapped_column(String, nullable=False)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # How much of the reply's text was written when the card was made: the reply
    # shows it there. Null for events from before this was kept, shown at the end.
    text_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ConversationArtifact(Base):
    __tablename__ = "conversation_artifacts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'ready', 'executing', 'completed', 'failed', 'denied', 'superseded', 'expired')",
            name="ck_conversation_artifact_status",
        ),
        CheckConstraint(
            "updated_by IN ('agent', 'user', 'system')",
            name="ck_conversation_artifact_updated_by",
        ),
        Index("ix_conversation_artifact_thread_status", "thread_id", "status", "updated_at"),
        Index("ix_conversation_artifact_user_thread", "user_id", "thread_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False
    )
    created_by_message_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_message_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    superseded_by_artifact_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("conversation_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    draft_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    approved_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    uncertain_fields: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    updated_by: Mapped[str] = mapped_column(String, nullable=False, default="system")
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    execution_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The user's last touch on the card; an open proposal left alone expires (see Access below).
    last_interacted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    revisions: Mapped[list[ConversationArtifactRevision]] = relationship(
        "ConversationArtifactRevision",
        cascade="all, delete-orphan",
        order_by="ConversationArtifactRevision.revision",
    )
    attempts: Mapped[list[ConversationArtifactExecutionAttempt]] = relationship(
        "ConversationArtifactExecutionAttempt",
        cascade="all, delete-orphan",
        order_by="ConversationArtifactExecutionAttempt.attempt_number",
    )


class ConversationArtifactRevision(Base):
    __tablename__ = "conversation_artifact_revisions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "revision", name="uq_conversation_artifact_revision"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation_artifacts.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    uncertain_fields: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    changed_by: Mapped[str] = mapped_column(String, nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ConversationArtifactExecutionAttempt(Base):
    __tablename__ = "conversation_artifact_execution_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('executing', 'completed', 'failed')",
            name="ck_conversation_artifact_attempt_status",
        ),
        UniqueConstraint("artifact_id", "attempt_number", name="uq_conversation_artifact_attempt"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversation_artifacts.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="executing")
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Contracts -------------------------------------------------------------------

class ArtifactDefinitionError(ValueError):
    def __init__(self, message: str, missing_fields: list[str] | None = None):
        super().__init__(message)
        self.missing_fields = missing_fields or []


class ArtifactExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class PreparedDraft:
    payload: dict[str, Any]
    missing_fields: list[str]
    ready: bool


@dataclass(frozen=True)
class ValidatedArtifactCommand:
    kind: Literal["create", "update", "replace"]
    title: str
    description: str
    draft_payload: dict[str, Any]
    uncertain_fields: list[Any]
    artifact_type: str = "work_routine"
    schema_version: int = 1
    artifact_id: str | None = None
    expected_revision: int | None = None


@dataclass(frozen=True)
class ArtifactDefinition:
    type: str
    schema_version: int
    draft_schema: type[BaseModel]
    prepare_draft: Callable[[dict[str, Any]], PreparedDraft]
    prepare_execution: Callable[[dict[str, Any], str], dict[str, Any]]
    execute: Callable[[Session, str, dict[str, Any], str], dict[str, Any]]
    # The interface this artifact acts on. Artifacts never reach beyond the
    # interfaces the user has enabled.
    required_interface: str | None = None
    # Fills a new draft from what already exists, before it is saved; given the
    # session, the user, the payload and the user's timezone.
    prepare_existing: Callable[[Session, str, dict[str, Any], str], dict[str, Any]] | None = None
    display_title: Callable[[dict[str, Any]], str] | None = None
    # A sentence the agent is told when a new draft of this type is saved, if any.
    draft_note: Callable[[Session, str, ValidatedArtifactCommand], str | None] | None = None
    # Why a ready draft could not be approved as things stand (it overlaps a
    # routine, say), or None; given the session, the user, the draft and the
    # user's timezone. A blocked card comes back ready once what blocks it goes.
    blocker: Callable[[Session, str, dict[str, Any], str], str | None] | None = None


ArtifactStatus = Literal[
    "draft", "ready", "executing", "completed", "failed", "denied", "superseded", "expired"
]
ArtifactUpdater = Literal["agent", "user", "system"]


class ArtifactUncertainty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1, max_length=200,
        description="A field inside draft_payload, without the draft_payload prefix: \"end\", or \"days.0\" in a list.",
    )
    reason: str = Field(min_length=1, max_length=500)


class ArtifactError(BaseModel):
    code: str
    message: str
    retryable: bool


class ArtifactResponse(BaseModel):
    id: str
    thread_id: str
    type: str
    schema_version: int
    title: str
    description: str
    status: ArtifactStatus
    revision: int
    draft_payload: dict[str, Any]
    approved_payload: dict[str, Any] | None
    result_payload: dict[str, Any] | None
    missing_fields: list[str]
    uncertain_fields: list[ArtifactUncertainty]
    updated_by: ArtifactUpdater
    error: ArtifactError | None
    execution_attempts: int
    superseded_by_artifact_id: str | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    last_attempted_at: datetime | None
    resolved_at: datetime | None
    last_interacted_at: datetime
    # When an open proposal expires unless the user touches it again.
    expires_at: datetime | None = None


class ArtifactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    draft_payload: dict[str, Any]


class ArtifactDeny(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class ArtifactMessageEvent(BaseModel):
    artifact_id: str
    event: Literal["created", "updated", "superseded"]
    snapshot: ArtifactResponse | None = None
    text_offset: int | None = None


# --- Definitions -----------------------------------------------------------------
#
# An artifact type is either the system's, offered whatever the user has enabled
# (changing Attune's own settings, say), or an interface's, offered only while
# that interface is enabled (a calendar routine). The system's are listed in
# `SYSTEM_ARTIFACTS` at the end of this module, and like an interface's, each gets a
# drafting tool.


def _types() -> tuple[tuple[ArtifactDefinition, ...], dict[str, tuple[ArtifactDefinition, ...]]]:
    """The system's types, and each interface's by its key.

    Imported here, not at the top: every type is defined from this module.
    """
    from app.chat.tools import interface_module
    from app.interfaces.registry import INTERFACE_CATALOG

    return SYSTEM_ARTIFACTS, {
        descriptor.key: module.ARTIFACTS for descriptor in INTERFACE_CATALOG
        if (module := interface_module(descriptor.key))
    }


def offered_artifacts(enabled_interfaces: Iterable[str]) -> list[ArtifactDefinition]:
    """The artifact types the agent may draft this turn: the system's, then each enabled interface's."""
    system_types, interface_types = _types()
    return [*system_types, *(
        definition for key in sorted(interface_types) if key in set(enabled_interfaces)
        for definition in interface_types[key]
    )]


def get_artifact_definition(artifact_type: str, schema_version: int) -> ArtifactDefinition:
    # Every type, enabled or not: an artifact outlives the interface it was drafted
    # for, and acting on it is gated separately (`required_interface`).
    definition = next((
        definition for definition in offered_artifacts(_types()[1])
        if (definition.type, definition.schema_version) == (artifact_type, schema_version)
    ), None)
    if definition is None:
        raise ArtifactDefinitionError(
            f"Unsupported artifact type or schema version: {artifact_type} v{schema_version}."
        )
    return definition


# === Access ========================================================================
#
# What drafting and approval both need: the errors, who owns what, and the response shape.

if TYPE_CHECKING:
    from app.chat.threads import ConversationMessage, ConversationThread


class ArtifactNotFoundError(LookupError):
    pass


class ArtifactConflictError(RuntimeError):
    pass


class ArtifactInterfaceDisabledError(RuntimeError):
    pass


class ArtifactValidationError(ValueError):
    def __init__(self, message: str, missing_fields: list[str] | None = None):
        super().__init__(message)
        self.missing_fields = missing_fields or []


# A proposal is meant to be handled when it's shown, not kept to finish later: one
# left open without the user touching it expires. The card counts down the same
# five minutes and grays itself out; the server allows a little more, since the
# card only reports touches every few seconds.
IDLE_EXPIRY = timedelta(minutes=5)
EXPIRY_GRACE = timedelta(seconds=30)
EXPIRING_STATUSES = ("draft", "ready")


def idle_expired(artifact: ConversationArtifact) -> bool:
    return (artifact.status in EXPIRING_STATUSES
            and aware(artifact.last_interacted_at) + IDLE_EXPIRY + EXPIRY_GRACE < utc_now())


def expire_if_idle(artifact: ConversationArtifact) -> bool:
    """Mark an idle open proposal expired. The caller commits."""
    if not idle_expired(artifact):
        return False
    artifact.status = "expired"
    artifact.updated_by = "system"
    artifact.resolved_at = utc_now()
    return True


def expire_idle_artifacts(db: Session, user_id: str, thread_id: str | None = None) -> None:
    """Persist expiry for a user's idle proposals, so every reader sees them expired."""
    query = db.query(ConversationArtifact).filter(
        ConversationArtifact.user_id == user_id,
        ConversationArtifact.status.in_(EXPIRING_STATUSES),
        ConversationArtifact.last_interacted_at < utc_now() - IDLE_EXPIRY - EXPIRY_GRACE,
    )
    if thread_id is not None:
        query = query.filter(ConversationArtifact.thread_id == thread_id)
    expired = [artifact for artifact in query.with_for_update(skip_locked=True) if expire_if_idle(artifact)]
    db.flush()
    # Imported here: runs are built on this module.
    from app.chat.runs import end_expired_wait

    # The named thread is checked either way, so a reply left waiting on a card that
    # expired earlier is ended too.
    for waiting_thread in {thread_id} if thread_id else {artifact.thread_id for artifact in expired}:
        end_expired_wait(db, user_id, waiting_thread)


def _expired_now(db: Session, artifact: ConversationArtifact) -> bool:
    """Expire this proposal if it sat idle, ending any reply waiting on it. Commits when it did."""
    if not expire_if_idle(artifact):
        return False
    db.flush()
    from app.chat.runs import end_expired_wait

    end_expired_wait(db, artifact.user_id, artifact.thread_id)
    db.commit()
    return True


def artifact_to_response(artifact: ConversationArtifact) -> ArtifactResponse:
    error = None
    if artifact.error_code and artifact.error_message:
        error = ArtifactError(
            code=artifact.error_code,
            message=artifact.error_message,
            retryable=bool(artifact.error_retryable),
        )
    return ArtifactResponse(
        id=artifact.id,
        thread_id=artifact.thread_id,
        type=artifact.type,
        schema_version=artifact.schema_version,
        title=artifact.title,
        description=artifact.description,
        status=cast(ArtifactStatus, "expired" if idle_expired(artifact) else artifact.status),
        revision=artifact.revision,
        draft_payload=artifact.draft_payload,
        approved_payload=artifact.approved_payload,
        result_payload=artifact.result_payload,
        missing_fields=artifact.missing_fields,
        uncertain_fields=[ArtifactUncertainty.model_validate(item) for item in artifact.uncertain_fields],
        updated_by=cast(ArtifactUpdater, artifact.updated_by),
        error=error,
        execution_attempts=artifact.execution_attempts,
        superseded_by_artifact_id=artifact.superseded_by_artifact_id,
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
        approved_at=artifact.approved_at,
        last_attempted_at=artifact.last_attempted_at,
        resolved_at=artifact.resolved_at,
        last_interacted_at=aware(artifact.last_interacted_at),
        expires_at=(aware(artifact.last_interacted_at) + IDLE_EXPIRY
                    if artifact.status in EXPIRING_STATUSES else None),
    )


def require_artifact_interface(
    db: Session, user_id: str, definition: ArtifactDefinition
) -> None:
    """Refuse artifacts belonging to an interface the user has turned off.

    The planning agent is never offered these tools, so this covers the direct
    artifact routes and artifacts left over from before an interface was
    disabled.
    """

    from app.interfaces.registry import get_interface, interface_enabled

    key = definition.required_interface
    if key is None or interface_enabled(user_id, key, db):
        return
    descriptor = get_interface(key)
    name = descriptor.name if descriptor else key
    raise ArtifactInterfaceDisabledError(
        f"Enable the {name} interface in settings to use this proposal."
    )


def owned_thread(db: Session, user_id: str, thread_id: str) -> ConversationThread:
    from app.chat.threads import ConversationThread

    thread = db.query(ConversationThread).filter(
        ConversationThread.id == thread_id,
        ConversationThread.user_id == user_id,
    ).one_or_none()
    if thread is None:
        raise ArtifactNotFoundError("Conversation thread not found.")
    return thread


def thread_message(
    db: Session, user_id: str, thread_id: str, message_id: str | None
) -> ConversationMessage | None:
    from app.chat.threads import ConversationMessage, ConversationThread

    if message_id is None:
        return None
    message = (
        db.query(ConversationMessage)
        .join(ConversationThread, ConversationMessage.thread_id == ConversationThread.id)
        .filter(
            ConversationMessage.id == message_id,
            ConversationMessage.thread_id == thread_id,
            ConversationThread.user_id == user_id,
        )
        .one_or_none()
    )
    if message is None:
        raise ArtifactValidationError("The source message does not belong to this thread.")
    return message


def owned_artifact(
    db: Session, user_id: str, artifact_id: str, *, lock: bool = False
) -> ConversationArtifact:
    query = db.query(ConversationArtifact).filter(
        ConversationArtifact.id == artifact_id,
        ConversationArtifact.user_id == user_id,
    )
    if lock:
        query = query.with_for_update()
    artifact = query.one_or_none()
    if artifact is None:
        raise ArtifactNotFoundError("Artifact not found.")
    return artifact


def ensure_thread_available(db: Session, artifact: ConversationArtifact) -> None:
    """Approval and edits wait for the conversation's response to settle, unless it
    is waiting on the user to decide a card."""
    from app.chat.runs import active_run

    from app.chat.threads import ConversationThread

    db.query(ConversationThread).filter(
        ConversationThread.id == artifact.thread_id
    ).with_for_update().one()
    if (run := active_run(db, artifact.thread_id)) is not None and run.status != "waiting":
        raise ArtifactConflictError(
            "Artifacts cannot be changed while the assistant is responding."
        )


# === Drafts ========================================================================
#
# An artifact while it is being written: drafted by the agent, edited by the user.
#
# The agent drafts one with a tool (`chat/tools.py`) that builds on
# `create_artifact`, `apply_edit` and `supersede_artifact`; the draft is saved the
# moment that tool succeeds, and changes nothing until the user approves it
# (see Approval below).
#
# A ready draft that could not be approved as things stand (a routine that
# overlaps another) is marked blocked, and the agent offers ways to clear it.
# Approving any card changes what the others are checked against, so
# `recheck_after_approval` checks every other card in the conversation again: one
# it blocks is marked, and one it clears comes back as a fresh copy after it.

def _value_at_path(payload: Any, path: str) -> tuple[bool, Any]:
    current = payload
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return False, None
    return True, current


def _artifact_title(
    definition: ArtifactDefinition, payload: dict[str, Any], fallback: str
) -> str:
    return definition.display_title(payload) if definition.display_title else fallback


def _validated_uncertainties(
    payload: dict[str, Any], values: list[ArtifactUncertainty | dict[str, str]]
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        uncertainty = (
            value if isinstance(value, ArtifactUncertainty) else ArtifactUncertainty.model_validate(value)
        )
        exists, _ = _value_at_path(payload, uncertainty.path)
        if not exists:
            raise ArtifactValidationError(
                f"Uncertain field does not exist in the artifact payload: {uncertainty.path}. Paths are "
                f"inside draft_payload, without that prefix; its fields are {', '.join(payload)}."
            )
        if uncertainty.path not in seen:
            normalized.append(uncertainty.model_dump())
            seen.add(uncertainty.path)
    return normalized


def _add_revision(
    artifact: ConversationArtifact,
    *,
    changed_by: ArtifactUpdater,
    source_message_id: str | None,
) -> None:
    artifact.revisions.append(
        ConversationArtifactRevision(
            id=str(uuid.uuid7()),
            revision=artifact.revision,
            payload=artifact.draft_payload,
            missing_fields=artifact.missing_fields,
            uncertain_fields=artifact.uncertain_fields,
            changed_by=changed_by,
            source_message_id=source_message_id,
        )
    )


def create_artifact(
    db: Session,
    *,
    user_id: str,
    thread_id: str,
    artifact_type: str,
    schema_version: int,
    title: str,
    description: str,
    draft_payload: dict[str, Any],
    uncertain_fields: list[ArtifactUncertainty | dict[str, str]] | None = None,
    source_message_id: str | None = None,
    changed_by: ArtifactUpdater = "agent",
) -> ConversationArtifact:
    owned_thread(db, user_id, thread_id)
    thread_message(db, user_id, thread_id, source_message_id)
    try:
        definition = get_artifact_definition(artifact_type, schema_version)
        prepared = definition.prepare_draft(draft_payload)
    except ArtifactDefinitionError as error:
        raise ArtifactValidationError(str(error), error.missing_fields) from error
    require_artifact_interface(db, user_id, definition)
    normalized_uncertainties = _validated_uncertainties(
        prepared.payload, uncertain_fields or []
    )
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > 200:
        raise ArtifactValidationError("Artifact titles must be between 1 and 200 characters.")
    if len(description) > 2000:
        raise ArtifactValidationError("Artifact descriptions cannot exceed 2000 characters.")
    artifact = ConversationArtifact(
        id=str(uuid.uuid7()),
        user_id=user_id,
        thread_id=thread_id,
        created_by_message_id=source_message_id,
        updated_by_message_id=source_message_id,
        type=artifact_type,
        schema_version=schema_version,
        title=_artifact_title(definition, prepared.payload, normalized_title),
        description=description.strip(),
        status="ready" if prepared.ready else "draft",
        revision=1,
        draft_payload=prepared.payload,
        missing_fields=prepared.missing_fields,
        uncertain_fields=normalized_uncertainties,
        updated_by=changed_by,
        last_interacted_at=utc_now(),
    )
    _add_revision(artifact, changed_by=changed_by, source_message_id=source_message_id)
    db.add(artifact)
    db.flush()
    return artifact


def get_thread_artifacts(db: Session, user_id: str, thread_id: str) -> list[ArtifactResponse]:
    if thread_id == "draft":
        return []
    artifacts = db.query(ConversationArtifact).filter(
        ConversationArtifact.user_id == user_id,
        ConversationArtifact.thread_id == thread_id,
    ).order_by(ConversationArtifact.updated_at.desc()).all()
    return [artifact_to_response(artifact) for artifact in artifacts]


def apply_edit(
    db: Session,
    *,
    user_id: str,
    artifact_id: str,
    expected_revision: int,
    draft_payload: dict[str, Any],
    changed_by: ArtifactUpdater = "user",
    source_message_id: str | None = None,
    uncertain_fields: list[ArtifactUncertainty | dict[str, str]] | None = None,
) -> ConversationArtifact:
    artifact = owned_artifact(db, user_id, artifact_id, lock=True)
    if artifact.status == "expired" or idle_expired(artifact):
        raise ArtifactConflictError("This proposal expired. Ask for it again in the chat.")
    if artifact.status not in {"draft", "ready"}:
        raise ArtifactConflictError("This artifact can no longer be edited.")
    if artifact.revision != expected_revision:
        raise ArtifactConflictError("The artifact changed before this edit was saved.")
    thread_message(db, user_id, artifact.thread_id, source_message_id)
    try:
        definition = get_artifact_definition(artifact.type, artifact.schema_version)
        prepared = definition.prepare_draft(draft_payload)
    except ArtifactDefinitionError as error:
        raise ArtifactValidationError(str(error), error.missing_fields) from error
    require_artifact_interface(db, user_id, definition)

    if uncertain_fields is None:
        normalized_uncertainties = []
        for uncertainty in artifact.uncertain_fields:
            old_exists, old_value = _value_at_path(artifact.draft_payload, uncertainty["path"])
            new_exists, new_value = _value_at_path(prepared.payload, uncertainty["path"])
            if old_exists and new_exists and old_value == new_value:
                normalized_uncertainties.append(uncertainty)
    else:
        normalized_uncertainties = _validated_uncertainties(prepared.payload, uncertain_fields)

    artifact.title = _artifact_title(
        definition, prepared.payload, artifact.title
    )
    artifact.draft_payload = prepared.payload
    artifact.missing_fields = prepared.missing_fields
    artifact.uncertain_fields = normalized_uncertainties
    artifact.status = "ready" if prepared.ready else "draft"
    artifact.revision += 1
    artifact.updated_by = changed_by
    artifact.updated_by_message_id = source_message_id
    # An edit, the user's or the agent's, puts the proposal back in front of them.
    artifact.last_interacted_at = utc_now()
    _add_revision(artifact, changed_by=changed_by, source_message_id=source_message_id)
    db.flush()
    return artifact


BLOCKED = "blocked"


def check_blocker(db: Session, artifact: ConversationArtifact, timezone_name: str = "UTC") -> str | None:
    """Mark a ready draft blocked, with why, or clear the mark; returns why."""
    definition = get_artifact_definition(artifact.type, artifact.schema_version)
    reason = None
    if artifact.status == "ready" and definition.blocker:
        reason = definition.blocker(db, artifact.user_id, artifact.draft_payload, timezone_name)
    if reason:
        artifact.error_code, artifact.error_message, artifact.error_retryable = BLOCKED, reason, None
    elif artifact.error_code == BLOCKED:
        artifact.error_code = artifact.error_message = artifact.error_retryable = None
    return reason


def recheck_after_approval(db: Session, done: ConversationArtifact, timezone_name: str = "UTC") -> None:
    """After `done` is approved, check every other card in its conversation again.

    An open card it now blocks is marked blocked, so the card says so before the
    user tries it. A blocked card (even one left to expire meanwhile), or one that
    failed on an overlap, that could now be approved is superseded by a fresh copy,
    shown at the end of the reply that made `done`, so the user meets it right
    after the card that cleared the way.
    """
    for artifact in db.query(ConversationArtifact).filter(
        ConversationArtifact.thread_id == done.thread_id, ConversationArtifact.user_id == done.user_id,
        ConversationArtifact.id != done.id, ConversationArtifact.status == "ready",
        or_(ConversationArtifact.error_code.is_(None), ConversationArtifact.error_code != BLOCKED),
    ).all():
        check_blocker(db, artifact, timezone_name)
    db.commit()
    waiting = [
        artifact for artifact in db.query(ConversationArtifact).filter(
            ConversationArtifact.thread_id == done.thread_id, ConversationArtifact.user_id == done.user_id,
            ConversationArtifact.id != done.id,
            ConversationArtifact.status.in_(("draft", "ready", "expired", "failed")),
        ).all()
        if (artifact.status != "failed" and artifact.error_code == BLOCKED)
        or (artifact.status == "failed" and artifact.error_retryable and (artifact.error_code or "").endswith("_overlap"))
    ]
    if not waiting:
        return
    from app.chat.threads import ConversationMessage

    found = db.query(ConversationMessageArtifactEvent.message_id).filter(
        ConversationMessageArtifactEvent.artifact_id == done.id,
    ).order_by(ConversationMessageArtifactEvent.created_at.desc()).first()
    reply = db.get(ConversationMessage, found[0]) if found else None
    if reply is None:
        return
    for old in waiting:
        definition = get_artifact_definition(old.type, old.schema_version)
        if not definition.blocker or definition.blocker(db, old.user_id, old.draft_payload, timezone_name):
            continue
        fresh = create_artifact(
            db, user_id=old.user_id, thread_id=old.thread_id, artifact_type=old.type,
            schema_version=old.schema_version, title=old.title, description=old.description,
            draft_payload=old.draft_payload, uncertain_fields=list(old.uncertain_fields),
            source_message_id=reply.id, changed_by="system",
        )
        supersede_artifact(db, user_id=old.user_id, artifact_id=old.id, replacement_artifact_id=fresh.id)
        for artifact, action in ((old, "superseded"), (fresh, "created")):
            db.add(ConversationMessageArtifactEvent(
                id=str(uuid.uuid7()), message_id=reply.id, artifact_id=artifact.id, event=action,
                snapshot=artifact_to_response(artifact).model_dump(mode="json"),
                text_offset=len(reply.content),
            ))
    db.commit()


def update_artifact(
    db: Session,
    *,
    user_id: str,
    artifact_id: str,
    expected_revision: int,
    draft_payload: dict[str, Any],
) -> ArtifactResponse:
    """The user's own edit to a draft, from its card."""
    owned = owned_artifact(db, user_id, artifact_id)
    ensure_thread_available(db, owned)
    artifact = apply_edit(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        expected_revision=expected_revision,
        draft_payload=draft_payload,
    )
    check_blocker(db, artifact)
    db.commit()
    db.refresh(artifact)
    return artifact_to_response(artifact)


def supersede_artifact(
    db: Session,
    *,
    user_id: str,
    artifact_id: str,
    replacement_artifact_id: str,
) -> ConversationArtifact:
    artifact = owned_artifact(db, user_id, artifact_id, lock=True)
    replacement = owned_artifact(db, user_id, replacement_artifact_id)
    if artifact.status not in {"draft", "ready", "failed", "expired"}:
        raise ArtifactConflictError("This artifact can no longer be superseded.")
    if artifact.id == replacement.id or artifact.thread_id != replacement.thread_id:
        raise ArtifactValidationError("Replacement artifacts must belong to the same thread.")
    artifact.status = "superseded"
    artifact.superseded_by_artifact_id = replacement.id
    artifact.updated_by = "system"
    artifact.resolved_at = utc_now()
    db.flush()
    return artifact


# === Approval ======================================================================
#
# An artifact once the user acts on it: accepted and executed, retried, or denied.
#
# Approval runs the type's own `execute`, which is the only place an artifact
# reaches the interface it acts on. A reply waiting on the card carries on once it
# is applied or denied.

def _begin_execution(
    db: Session,
    artifact: ConversationArtifact,
    approved_payload: dict[str, Any],
    *,
    first_attempt: bool,
) -> ConversationArtifactExecutionAttempt:
    now = utc_now()
    artifact.status = "executing"
    artifact.updated_by = "system"
    artifact.error_code = None
    artifact.error_message = None
    artifact.error_retryable = None
    artifact.last_attempted_at = now
    artifact.execution_attempts += 1
    if first_attempt:
        artifact.approved_payload = approved_payload
        artifact.approved_at = now
    attempt = ConversationArtifactExecutionAttempt(
        id=str(uuid.uuid7()),
        attempt_number=artifact.execution_attempts,
        status="executing",
        started_at=now,
    )
    artifact.attempts.append(attempt)
    db.commit()
    return attempt


def _execute_claimed(
    db: Session,
    artifact: ConversationArtifact,
    attempt: ConversationArtifactExecutionAttempt,
    timezone_name: str = "UTC",
) -> ArtifactResponse:
    definition = get_artifact_definition(artifact.type, artifact.schema_version)
    assert artifact.approved_payload is not None
    artifact_id = artifact.id
    user_id = artifact.user_id
    attempt_id = attempt.id
    try:
        result = definition.execute(db, user_id, artifact.approved_payload, artifact_id)
    except ArtifactExecutionError as error:
        db.rollback()
        artifact = owned_artifact(db, user_id, artifact_id, lock=True)
        loaded_attempt = db.get(ConversationArtifactExecutionAttempt, attempt_id)
        assert loaded_attempt is not None
        now = utc_now()
        artifact.status = "failed"
        artifact.error_code = error.code
        artifact.error_message = str(error)
        artifact.error_retryable = error.retryable
        artifact.updated_by = "system"
        artifact.resolved_at = None
        loaded_attempt.status = "failed"
        loaded_attempt.error_code = error.code
        loaded_attempt.error_message = str(error)
        loaded_attempt.retryable = error.retryable
        loaded_attempt.completed_at = now
        db.commit()
        db.refresh(artifact)
        return artifact_to_response(artifact)
    except Exception:
        db.rollback()
        artifact = owned_artifact(db, user_id, artifact_id, lock=True)
        loaded_attempt = db.get(ConversationArtifactExecutionAttempt, attempt_id)
        assert loaded_attempt is not None
        now = utc_now()
        artifact.status = "failed"
        artifact.error_code = "artifact_execution_failed"
        artifact.error_message = "The requested work could not be completed."
        artifact.error_retryable = False
        artifact.updated_by = "system"
        loaded_attempt.status = "failed"
        loaded_attempt.error_code = artifact.error_code
        loaded_attempt.error_message = artifact.error_message
        loaded_attempt.retryable = False
        loaded_attempt.completed_at = now
        db.commit()
        db.refresh(artifact)
        return artifact_to_response(artifact)

    artifact = owned_artifact(db, user_id, artifact_id, lock=True)
    loaded_attempt = db.get(ConversationArtifactExecutionAttempt, attempt_id)
    assert loaded_attempt is not None
    now = utc_now()
    artifact.status = "completed"
    artifact.result_payload = result
    artifact.resolved_at = now
    artifact.updated_by = "system"
    loaded_attempt.status = "completed"
    loaded_attempt.retryable = None
    loaded_attempt.completed_at = now
    db.commit()
    recheck_after_approval(db, artifact, timezone_name)
    db.refresh(artifact)
    return artifact_to_response(artifact)


def accept_artifact(
    db: Session,
    *,
    user_id: str,
    artifact_id: str,
    timezone_name: str = "UTC",
) -> ArtifactResponse:
    artifact = owned_artifact(db, user_id, artifact_id)
    ensure_thread_available(db, artifact)
    artifact = owned_artifact(db, user_id, artifact_id, lock=True)
    if artifact.status in {"executing", "completed"}:
        return artifact_to_response(artifact)
    _expired_now(db, artifact)
    if artifact.status == "expired":
        raise ArtifactConflictError("This proposal expired. Ask for it again in the chat.")
    if artifact.status != "ready":
        raise ArtifactConflictError("Only a ready artifact can be accepted.")
    try:
        definition = get_artifact_definition(artifact.type, artifact.schema_version)
        approved_payload = definition.prepare_execution(
            artifact.draft_payload, timezone_name
        )
    except ArtifactDefinitionError as error:
        raise ArtifactValidationError(str(error), error.missing_fields) from error
    require_artifact_interface(db, user_id, definition)
    thread_id = artifact.thread_id
    attempt = _begin_execution(db, artifact, approved_payload, first_attempt=True)
    return _resumed(db, thread_id, _execute_claimed(db, artifact, attempt, timezone_name))


def _resumed(db: Session, thread_id: str, response: ArtifactResponse) -> ArtifactResponse:
    """A decided card lets the reply waiting on it, if any, carry on."""
    if response.status in {"completed", "denied"}:
        # Imported here: runs are built on this module.
        from app.chat.runs import resume_held

        resume_held(db, response.id, thread_id)
    return response


def retry_artifact(db: Session, *, user_id: str, artifact_id: str) -> ArtifactResponse:
    artifact = owned_artifact(db, user_id, artifact_id)
    ensure_thread_available(db, artifact)
    artifact = owned_artifact(db, user_id, artifact_id, lock=True)
    if artifact.status in {"executing", "completed"}:
        return artifact_to_response(artifact)
    if artifact.status != "failed":
        raise ArtifactConflictError("Only a failed artifact can be retried.")
    if artifact.approved_payload is None:
        raise ArtifactConflictError("This artifact failure cannot be retried.")
    require_artifact_interface(
        db, user_id, get_artifact_definition(artifact.type, artifact.schema_version)
    )
    thread_id = artifact.thread_id
    attempt = _begin_execution(
        db, artifact, artifact.approved_payload, first_attempt=False
    )
    return _resumed(db, thread_id, _execute_claimed(db, artifact, attempt))


def deny_artifact(
    db: Session,
    *,
    user_id: str,
    artifact_id: str,
    reason: str | None = None,
) -> ArtifactResponse:
    artifact = owned_artifact(db, user_id, artifact_id)
    ensure_thread_available(db, artifact)
    artifact = owned_artifact(db, user_id, artifact_id, lock=True)
    if artifact.status in {"denied", "expired"}:
        return artifact_to_response(artifact)
    if artifact.status not in {"draft", "ready"}:
        raise ArtifactConflictError("This artifact can no longer be denied.")
    artifact.status = "denied"
    artifact.result_payload = {"denial_reason": reason.strip()} if reason and reason.strip() else None
    artifact.updated_by = "user"
    artifact.resolved_at = utc_now()
    db.commit()
    db.refresh(artifact)
    return _resumed(db, artifact.thread_id, artifact_to_response(artifact))


def touch_artifact(db: Session, *, user_id: str, artifact_id: str) -> ArtifactResponse:
    """The user is working on this proposal: its idle clock starts again.

    Too late, and it is expired instead. Touching works while the assistant is
    responding, since reading and dragging a card changes nothing.
    """
    artifact = owned_artifact(db, user_id, artifact_id, lock=True)
    if _expired_now(db, artifact):
        raise ArtifactConflictError("This proposal expired. Ask for it again in the chat.")
    if artifact.status in EXPIRING_STATUSES:
        artifact.last_interacted_at = utc_now()
        db.commit()
        db.refresh(artifact)
    return artifact_to_response(artifact)


# === System type: turning an interface on or off ===================================
#
# Turning an interface on or off, proposed in chat like any other change.
#
# Nothing gates it: turning an interface on is how the user reaches one they have
# not enabled. Turning one off keeps its data, so turning it back on brings
# everything back. A reply that proposes turning one on waits for the decision,
# then carries on with the interface open (`chat/tools.py`).

class InterfaceToggleDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, description="The interface's key, from the Interfaces section of the turn context.")
    enabled: bool = Field(default=True, description="true turns it on, false turns it off.")
    # Filled in when drafted, for the card.
    name: str | None = None
    description: str | None = None


def _toggle_prepare_draft(payload: dict[str, Any]) -> PreparedDraft:
    from app.interfaces.registry import get_interface, INTERFACE_CATALOG

    try:
        draft = InterfaceToggleDraftPayload.model_validate(payload).model_dump(mode="json")
    except ValidationError as error:
        raise ArtifactDefinitionError(str(error)) from error
    if not draft["key"]:
        return PreparedDraft(payload=draft, missing_fields=["key"], ready=False)
    descriptor = get_interface(draft["key"])
    if descriptor is None:
        keys = ", ".join(descriptor.key for descriptor in INTERFACE_CATALOG)
        raise ArtifactDefinitionError(f"There is no interface {draft['key']}. Use one of: {keys}.")
    draft |= {"name": descriptor.name, "description": descriptor.description}
    return PreparedDraft(payload=draft, missing_fields=[], ready=True)


def _toggle_existing(db: Session, user_id: str, payload: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    """A new proposal must change something."""
    from app.interfaces.registry import get_interface, interface_enabled

    key = payload.get("key")
    descriptor = get_interface(key) if key else None
    enabled = payload.get("enabled", True)
    if descriptor and interface_enabled(user_id, descriptor.key, db) == enabled:
        raise ModelRetry(f"The {descriptor.name} is already {'on' if enabled else 'off'}; there is nothing to propose.")
    return payload


def _toggle_execution(payload: dict[str, Any], timezone_name: str = "UTC") -> dict[str, Any]:
    prepared = _toggle_prepare_draft(payload)
    if not prepared.ready:
        raise ArtifactDefinitionError("The change needs to name an interface.", prepared.missing_fields)
    return prepared.payload


def _toggle_execute(db: Session, user_id: str, approved: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    from app.interfaces.registry import set_interface_enabled, UnknownInterfaceError

    try:
        set_interface_enabled(user_id, approved["key"], approved["enabled"], db)
    except UnknownInterfaceError as error:
        raise ArtifactExecutionError("interface_missing", "Attune no longer offers that interface.", retryable=False) from error
    return {
        "resource_type": "interface", "resource_id": approved["key"], "enabled": approved["enabled"],
        "idempotency_key": artifact_id,
    }


def _draft_note(db: Session, user_id: str, command: ValidatedArtifactCommand) -> str | None:
    if command.draft_payload.get("enabled"):
        return None  # The reply waits for the decision instead.
    return "Its data stays, and comes back if it is turned on again."


def _title(payload: dict[str, Any]) -> str:
    name = payload.get("name") or "an interface"
    return f"Turn on {name}" if payload.get("enabled", True) else f"Turn off {name}"


INTERFACE_TOGGLE = ArtifactDefinition(
    type="interface_toggle", schema_version=1, draft_schema=InterfaceToggleDraftPayload,
    prepare_draft=_toggle_prepare_draft, prepare_execution=_toggle_execution, execute=_toggle_execute,
    prepare_existing=_toggle_existing, display_title=_title, draft_note=_draft_note,
)


# === System type: forgetting everything ============================================
#
# Forgetting everything memory holds, proposed in chat and approved on a card.
#
# Correcting or forgetting one fact needs no card: the user asking is the approval,
# and the assistant does it with a tool (`chat/tools.py`).
# Forgetting everything is the exception. It is one sentence that may have been
# misread, it takes years of memory with it, and it cannot be undone, so the card
# says how much will go and the user confirms it there. The reply waits for that
# decision and then says what happened. Memory is reached only through
# `app.memory.public`.

class MemoryWipeDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Filled in when drafted, for the card: how many things memory holds.
    held: int | None = None


def _wipe_prepare_draft(payload: dict[str, Any]) -> PreparedDraft:
    try:
        draft = MemoryWipeDraftPayload.model_validate(payload).model_dump(mode="json")
    except ValidationError as error:
        raise ArtifactDefinitionError(str(error)) from error
    return PreparedDraft(payload=draft, missing_fields=[], ready=True)


def _wipe_existing(db: Session, user_id: str, payload: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    """A new proposal says how much there is to forget, and there must be something."""
    from app.memory import public as memory

    held = memory.held_count(db, user_id)
    if not held:
        raise ModelRetry("Memory holds nothing about this user; there is nothing to propose. Say so.")
    return payload | {"held": held}


def _wipe_execution(payload: dict[str, Any], timezone_name: str = "UTC") -> dict[str, Any]:
    return _wipe_prepare_draft(payload).payload


def _wipe_execute(db: Session, user_id: str, approved: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    from app.memory import public as memory

    forgotten = memory.forget_everything(db, user_id)
    db.commit()
    return {"resource_type": "memory", "resource_id": user_id, "forgotten": forgotten,
            "idempotency_key": artifact_id}


MEMORY_WIPE = ArtifactDefinition(
    type="memory_wipe", schema_version=1, draft_schema=MemoryWipeDraftPayload,
    prepare_draft=_wipe_prepare_draft, prepare_execution=_wipe_execution, execute=_wipe_execute,
    prepare_existing=_wipe_existing, display_title=lambda payload: "Forget everything Attune knows about you",
)


# Offered to the agent in this order, ahead of any interface's.
SYSTEM_ARTIFACTS = (INTERFACE_TOGGLE, MEMORY_WIPE)
