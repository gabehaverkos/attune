"""The chat API: threads, sessions, and turns and their event streams.

A session handoff is atomic and non-canceling. The new session carries nothing
copied from the old one; what it remembers of the previous conversation is read
when each turn is assembled, so it always reflects that conversation as it now
stands, including a reply that was still being written when the handoff happened.
"""

import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.chat import runs
from app.chat.runs import (
    active_run, ActiveRunError, ConversationRun, DailyLimitError, observe_run, RequestReuseError,
)
from app.chat.artifacts import (
    accept_artifact, ArtifactDeny, ArtifactResponse, ArtifactUpdate, deny_artifact, expire_idle_artifacts,
    get_thread_artifacts, retry_artifact, touch_artifact, update_artifact,
)
from app.chat.threads import (
    ActiveRun, ChatThread as ChatThreadContract, ChatThreadPage, ConversationThread, get_thread_messages,
    NewSession, RetryRequest, TurnAccepted, TurnRequest,
)
from app.core.auth import get_current_user, User
from app.core.database import get_db
from app.core.time import request_timezone
from app.memory import public as memory


# === Conversations =================================================================

# --- Threads and turns -----------------------------------------------------------

def client_address(
    x_attune_client_address: str | None = Header(default=None, alias="X-Attune-Client-Address"),
) -> str | None:
    """The visitor's address, set by the web app's proxy, the only caller this API
    has; it replaces any value the browser sent."""
    return x_attune_client_address or None


router = APIRouter()

STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _thread_list(db: Session, user_id: str) -> list[ChatThreadContract]:
    threads = db.query(ConversationThread).filter(
        ConversationThread.user_id == user_id,
        # Empty sessions (including fresh handoffs) stay hidden until their first message.
        ConversationThread.messages.any(),
    ).all()
    ordered = sorted(threads, key=lambda t: t.id, reverse=True)
    return [
        ChatThreadContract(
            id=t.id, created_at=t.created_at, archived=t.status == "archived",
        )
        for t in ordered
    ]


def _active_run_contract(db: Session, thread_id: str) -> ActiveRun | None:
    run = active_run(db, thread_id)
    if run is None:
        return None
    return ActiveRun(
        id=run.id, status=run.status, assistant_message_id=run.assistant_message_id,  # type: ignore[arg-type]
        cursor=run.event_sequence, activity=runs.current_activity(db, run),
        cancel_requested=run.cancel_requested_at is not None,
        held_artifact_ids=list((run.hold or {}).get("calls", {}).values()),
    )


@router.get("/api/chat/thread", response_model=ChatThreadPage)
def get_thread(
    thread_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatThreadPage:
    thread_id = thread_id or "draft"
    if thread_id == "draft":
        return ChatThreadPage(threads=_thread_list(db, current_user.id), active_thread_id="draft", thread=[])
    owned = db.query(ConversationThread.id).filter(
        ConversationThread.id == thread_id, ConversationThread.user_id == current_user.id,
    ).first()
    if owned is None:
        # Someone else's session reads the same as one that is not there.
        return ChatThreadPage(threads=_thread_list(db, current_user.id), active_thread_id=thread_id, thread=[])
    # Proposals left alone expire; settle that first so the page and the agent agree.
    expire_idle_artifacts(db, current_user.id, thread_id)
    # One snapshot: messages, artifacts and the run cursor are read in a single
    # transaction, so replaying events after the cursor applies each change once.
    db.commit()
    if db.get_bind().dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    messages = get_thread_messages(thread_id, current_user.id, db)
    return ChatThreadPage(
        threads=_thread_list(db, current_user.id),
        active_thread_id=thread_id,
        thread=messages,
        artifacts=get_thread_artifacts(db, current_user.id, thread_id),
        active_run=_active_run_contract(db, thread_id),
    )


def _resolve_owned_thread(db: Session, thread_id: str, user_id: str) -> ConversationThread:
    db_thread = db.query(ConversationThread).filter(
        ConversationThread.id == thread_id,
        ConversationThread.user_id == user_id,
    ).first()
    if not db_thread:
        raise HTTPException(status_code=404, detail="Chat thread not found.")
    return db_thread


@router.post("/api/chat/thread/{thread_id}/archive")
def archive_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Hide a session from the list. Its transcript and its facts both stay."""
    db_thread = _resolve_owned_thread(db, thread_id, current_user.id)
    db_thread.status = "archived"
    db_thread.archive_reason = "user"
    db.commit()
    return {"status": "ok", "detail": "Thread archived."}


@router.post("/api/chat/thread/{thread_id}/unarchive")
def unarchive_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    db_thread = _resolve_owned_thread(db, thread_id, current_user.id)
    db_thread.status = "active"
    db_thread.archive_reason = None
    db.commit()
    return {"status": "ok", "detail": "Thread restored."}


@router.delete("/api/chat/thread/{thread_id}")
def delete_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    db_thread = _resolve_owned_thread(db, thread_id, current_user.id)
    db.query(ConversationThread).filter(ConversationThread.id == db_thread.id).with_for_update().one()
    resolved_thread_id = db_thread.id
    if (run := active_run(db, resolved_thread_id)) is not None and run.status != "waiting":
        raise HTTPException(status_code=409, detail="Wait for the assistant response to finish.")
    # Deleting means forgetting what it taught; archiving is the verb for tidying up.
    memory.forget_conversation(db, current_user.id, resolved_thread_id)
    db.query(ConversationRun).filter(ConversationRun.thread_id == resolved_thread_id).delete(synchronize_session=False)
    db.delete(db_thread)
    db.commit()
    return {"status": "ok", "detail": "Thread deleted successfully"}


def _accepted(turn: runs.AcceptedTurn, user_id: str) -> TurnAccepted:
    return TurnAccepted(
        run_id=turn.run_id, thread_id=turn.thread_id,
        user_message_id=turn.user_message_id, assistant_message_id=turn.assistant_message_id,
        thread_created=turn.thread_created, cursor=turn.cursor,
    )


class Allowance(BaseModel):
    # How much of the day's allowance is spent, from 0 to 1.
    used: float


@router.get("/api/chat/allowance", response_model=Allowance)
def read_allowance(
    address: str | None = Depends(client_address),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Allowance:
    """How much of the day's allowance the visitor has spent, for the composer to show."""
    return Allowance(used=runs.allowance_used(db, client_address=address, user_id=current_user.id))


@router.post("/api/chat/turns", response_model=TurnAccepted, status_code=status.HTTP_202_ACCEPTED)
def send_message(
    payload: TurnRequest,
    current_user: User = Depends(get_current_user),
    timezone_name: str = Depends(request_timezone),
    address: str | None = Depends(client_address),
    db: Session = Depends(get_db),
) -> TurnAccepted:
    """Accept a message and queue its reply. Observing the reply is a separate request."""
    # The reply reads the proposals in this conversation; expired ones read as expired.
    if payload.thread_id and payload.thread_id != "draft":
        expire_idle_artifacts(db, current_user.id, payload.thread_id)
        db.commit()
    try:
        turn = runs.accept_turn(
            db, user_id=current_user.id,
            thread_id=payload.thread_id,
            content=payload.content.strip() or payload.content,
            client_request_id=payload.client_request_id, timezone_name=timezone_name,
            client_address=address,
        )
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ActiveRunError, RequestReuseError) as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DailyLimitError as error:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(error)) from error
    return _accepted(turn, current_user.id)


@router.post("/api/chat/runs/{run_id}/retry", response_model=TurnAccepted, status_code=status.HTTP_202_ACCEPTED)
def retry_run(
    run_id: str,
    payload: RetryRequest,
    current_user: User = Depends(get_current_user),
    timezone_name: str = Depends(request_timezone),
    address: str | None = Depends(client_address),
    db: Session = Depends(get_db),
) -> TurnAccepted:
    try:
        turn = runs.retry_turn(
            db, user_id=current_user.id, run_id=run_id,
            client_request_id=payload.client_request_id, timezone_name=timezone_name,
            client_address=address,
        )
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ActiveRunError, RequestReuseError) as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DailyLimitError as error:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(error)) from error
    return _accepted(turn, current_user.id)


@router.get("/api/chat/runs/{run_id}/events")
def observe_events(
    run_id: str,
    after: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Replay a run's events after a cursor, then follow it until it ends."""
    owned = db.query(ConversationRun.id).filter(
        ConversationRun.id == run_id, ConversationRun.user_id == current_user.id,
    ).first()
    if owned is None:
        raise HTTPException(404, detail="Response not found.")
    db.close()
    return StreamingResponse(
        observe_run(current_user.id, run_id, after),
        media_type="application/x-ndjson", headers=STREAM_HEADERS,
    )


@router.post("/api/chat/runs/{run_id}/cancel")
def cancel_run(
    run_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict[str, str]:
    """The only way to stop a reply. Leaving the page never does."""
    try:
        run_status = runs.request_cancel(db, user_id=current_user.id, run_id=run_id)
    except LookupError as error:
        db.rollback()
        raise HTTPException(404, detail=str(error)) from error
    return {"status": run_status}


@router.post("/api/chat/sessions", response_model=ChatThreadPage)
def new_session(
    payload: NewSession,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatThreadPage:
    previous_id = payload.previous_thread_id
    try:
        thread = start_session(db, current_user.id, previous_id, payload.request_id)
        thread_id = thread.id
        db.commit()
    except LookupError as error:
        db.rollback()
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(409, detail=str(error)) from error
    return get_thread(thread_id=thread_id, current_user=current_user, db=db)


# --- Session handoff -------------------------------------------------------------

def start_session(
    db: Session, user_id: str, previous_thread_id: str, request_id: str,
) -> ConversationThread:
    # Serialize retries across tabs before checking the idempotency key.
    db.query(User).filter(User.id == user_id).with_for_update().one()
    existing = db.query(ConversationThread).filter(
        ConversationThread.user_id == user_id, ConversationThread.handoff_request_id == request_id,
    ).one_or_none()
    if existing:
        if existing.previous_thread_id != previous_thread_id:
            raise ValueError("This request ID was already used for another session.")
        return existing
    previous = db.query(ConversationThread).filter(
        ConversationThread.id == previous_thread_id, ConversationThread.user_id == user_id,
    ).with_for_update().one_or_none()
    if previous is None:
        raise LookupError("Previous session not found.")
    thread = ConversationThread(
        id=str(uuid.uuid7()), user_id=user_id, handoff_request_id=request_id, previous_thread_id=previous.id,
    )
    db.add(thread)
    db.flush()
    return thread


# === Artifacts =====================================================================
#
# The artifact API: the user editing a draft, and approving, denying or retrying it.
#
# The paths sit under `/api/chat`, beside the conversation the artifact belongs to.



@router.patch("/api/chat/artifacts/{artifact_id}", response_model=ArtifactResponse)
def patch_artifact(
    artifact_id: str,
    payload: ArtifactUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactResponse:
    return update_artifact(
        db,
        user_id=current_user.id,
        artifact_id=artifact_id,
        expected_revision=payload.expected_revision,
        draft_payload=payload.draft_payload,
    )


@router.post("/api/chat/artifacts/{artifact_id}/accept", response_model=ArtifactResponse)
def post_accept_artifact(
    artifact_id: str,
    timezone_name: str = Depends(request_timezone),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactResponse:
    return accept_artifact(
        db,
        user_id=current_user.id,
        artifact_id=artifact_id,
        timezone_name=timezone_name,
    )


@router.post("/api/chat/artifacts/{artifact_id}/deny", response_model=ArtifactResponse)
def post_deny_artifact(
    artifact_id: str,
    payload: ArtifactDeny | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactResponse:
    return deny_artifact(
        db,
        user_id=current_user.id,
        artifact_id=artifact_id,
        reason=payload.reason if payload else None,
    )


@router.post("/api/chat/artifacts/{artifact_id}/retry", response_model=ArtifactResponse)
def post_retry_artifact(
    artifact_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactResponse:
    return retry_artifact(db, user_id=current_user.id, artifact_id=artifact_id)


@router.post("/api/chat/artifacts/{artifact_id}/touch", response_model=ArtifactResponse)
def post_touch_artifact(
    artifact_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactResponse:
    return touch_artifact(db, user_id=current_user.id, artifact_id=artifact_id)
