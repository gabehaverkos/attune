"""What the rest of the application may ask of memory.

Chat, onboarding and the shell call this module and nothing else inside
`app.memory`, which is otherwise private to memory. In the other direction, memory
reads chat only through the Conversations section of `sources.py`.
Everything behind it — the graph (`graph.py`), how it learns (`learning.py`,
`review.py`, `work.py`), and what it gives back (`recall.py`) — can change without
them noticing. The assistant's memory tools live with the agent's other tools
(`app/chat/tools.py`); what they read here is in the "For the assistant's tools"
section. The memory panel's API is at the end.
"""

from datetime import timedelta
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, User
from app.core.database import get_db
from app.core.time import request_timezone, utc_now
from app.memory import graph, learning, work
from app.memory.sources import CORRECTION, MemoryChange, MemorySource, ordered_messages, TOOK_BACK
from app.memory.learning import correct_fact, MAX_STATEMENT_CHARACTERS, remove_fact
from app.memory.work import correction_refused, request_review
from app.memory.graph import (
    line, load, MemoryEvidence, MemoryExclusion, MemoryNodeFact, NodeNotFoundError, THING,
)
from app.memory.recall import (
    build_panel, CHANGE_LABELS, MemoryPanel, pending_memory, PendingMemory, read_turn_memory,
    TurnMemory,
)

# === In the application ============================================================

__all__ = [
    "MAX_STATEMENT_CHARACTERS", "MemoryNodeFact", "NodeNotFoundError", "PendingMemory", "TurnMemory",
    "correct", "fact_evidence", "forget", "forget_conversation", "forget_everything",
    "forgotten_messages", "held", "held_count",
    "pending_memory", "read_turn_memory", "recent_changes", "reply_finished", "retry_failed",
    "review_spend", "review_spend_by_address", "search",
]


# --- Conversations ---------------------------------------------------------------

def reply_finished(db: Session, user_id: str, thread_id: str) -> None:
    """A reply ended: learn what the user said, now. The caller commits.

    What the user said since the last time is learned, read in the conversation
    around it. Nothing in chat notices or announces anything; what is learned
    shows in the memory panel.
    """
    work.reply_finished(db, user_id, thread_id)


def forget_conversation(db: Session, user_id: str, thread_id: str) -> None:
    """Take back what a conversation taught, before it is deleted. The caller commits.

    Deleting has to mean forgetting, or it only destroys the evidence for beliefs
    that stay. This runs before the conversation goes, in the same transaction.
    """
    from app.core import queue

    work.forget_conversation(db, user_id, thread_id)
    queue.cancel(db, subject=thread_id, kinds=(work.CONVERSATION,))


def review_spend(db: Session, *, client_address: str | None, user_id: str) -> float:
    """Dollars of memory reviews charged in the last day to this address, or to
    the account without one. They share chat's daily allowance."""
    return work.review_spend(db, client_address=client_address, user_id=user_id)


def review_spend_by_address(db: Session, user_id: str) -> dict[str, float]:
    """Dollars of this account's reviews charged in the last day, by the address each was asked from."""
    return work.review_spend_by_address(db, user_id)


def retry_failed(db: Session, user_id: str) -> int:
    """Hand everything that failed back to the worker. Returns how much. The caller commits."""
    return work.retry_failed(db, user_id)


# --- For the assistant's tools ---------------------------------------------------

# Messages shown around the one a belief rests on.
EVIDENCE_BEFORE, EVIDENCE_AFTER = 6, 2


async def search(user_id: str, query: str, *, limit: int) -> list[MemoryNodeFact]:
    """Nodes relevant to a query, most relevant first, with where each sits."""
    return await graph.search_facts(user_id, query, limit=limit)


def held(db: Session, user_id: str, fact: str) -> tuple[str, str] | None:
    """A node the assistant names, by its id or by its line as the turn's memory
    context gives it: its id, and how it reads to the user. None when memory holds
    no such node."""
    graph = load(db, user_id)
    wanted = _plain(fact)
    node = graph.nodes.get(fact.strip()) or next(
        (node for node in graph.nodes.values() if wanted and _plain(line(node)) == wanted), None)
    if node is None or node.id == graph.root_id:
        return None
    return node.id, (node.name or node.text).strip() if node.kind == THING else node.text.strip()


def _plain(text: str) -> str:
    """A line without what the outline puts around it: its dash, and how much more is below."""
    text = " ".join(text.casefold().split()).lstrip("- ")
    return text.split(" (+", 1)[0].rstrip(".")


def correct(db: Session, user_id: str, node_id: str, text: str) -> None:
    """Make a node read as given, now. The caller commits. Raises NodeNotFoundError,
    or ValueError when the text is empty or too long."""
    learning.correct_fact(db, user_id, node_id, text, commit=False)


def forget(db: Session, user_id: str, node_ids: list[str]) -> list[str]:
    """Take nodes out of memory together. Returns those that were there to take.
    The caller commits."""
    return learning.remove_facts(db, user_id, node_ids, commit=False)


# What memory did with a conversation stays worth saying for this long.
CHANGES_WINDOW = timedelta(hours=24)


def recent_changes(db: Session, user_id: str, thread_id: str, *, limit: int = 8) -> list[str]:
    """What memory lately took from a conversation, and from corrections the user
    typed, oldest first: "Added: …", "Took back: …". A belief that has since gone
    is left out, so this never says memory holds what it no longer does."""
    rows = db.execute(select(MemoryChange.op, MemoryChange.text).join(
        MemorySource, MemorySource.id == MemoryChange.source_id).where(
        MemorySource.user_id == user_id,
        (MemorySource.thread_id == thread_id) | (MemorySource.kind == CORRECTION),
        (MemoryChange.op == TOOK_BACK) | MemoryChange.node_id.is_not(None),
        MemoryChange.created_at >= utc_now() - CHANGES_WINDOW,
    ).order_by(MemoryChange.created_at.desc(), MemoryChange.id.desc()).limit(limit)).all()
    return [f"{CHANGE_LABELS[op]}: {text}" for op, text in rows][::-1]


def held_count(db: Session, user_id: str) -> int:
    """How many things memory holds about the user."""
    return learning.held_count(db, user_id)


def forget_everything(db: Session, user_id: str) -> int:
    """Take everything out of memory. Returns how much there was. The caller commits."""
    return learning.remove_everything(db, user_id)


def forgotten_messages(db: Session, user_id: str) -> set[str]:
    """The messages a belief the user removed rested on. They stay in their
    conversations, but nothing should bring them back up as though still known."""
    return set(db.scalars(select(MemoryExclusion.message_id).where(
        MemoryExclusion.user_id == user_id, MemoryExclusion.message_id != "")))


def fact_evidence(db: Session, user_id: str, node_id: str) -> tuple[str, str] | None:
    """The conversation a node was most recently learned from, around the message
    it rests on; None when it came from no conversation that still exists.

    A summary rests on what is below it, so its evidence is theirs.
    """
    graph = load(db, user_id)
    if node_id not in graph.nodes or node_id == graph.root_id:
        return None
    nodes = {node_id} | graph.links.descendants(node_id)
    row = db.scalars(select(MemoryEvidence).where(
        MemoryEvidence.node_id.in_(nodes), MemoryEvidence.thread_id.is_not(None), MemoryEvidence.message_id != "",
    ).order_by(MemoryEvidence.said_at.desc())).first()
    if row is None or row.thread_id is None:
        return None
    messages = ordered_messages(db, row.thread_id)
    ids = [message.id for message in messages]
    if row.message_id not in ids:
        return None
    at = ids.index(row.message_id)
    return row.thread_id, "\n\n".join(
        f"Author: {message.author}\nSent at: {message.created_at}\nContent: {message.content}"
        for message in messages[max(0, at - EVIDENCE_BEFORE):at + EVIDENCE_AFTER + 1]
    )


# === The panel's API ===============================================================
#
# The memory panel's API. Every change answers with the panel as it now stands.

router = APIRouter(prefix="/api/memory", tags=["memory"])


class StatementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_STATEMENT_CHARACTERS)


@router.get("/panel", response_model=MemoryPanel)
def read_panel(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    timezone_name: str = Depends(request_timezone),
) -> MemoryPanel:
    """Everything the memory panel shows: the graph and its evidence, and sources."""
    return build_panel(db, current_user.id, timezone_name=timezone_name)


@router.post("/nodes/{node_id}/correct", response_model=MemoryPanel)
def correct_memory_node(
    node_id: str,
    payload: StatementRequest,
    # The visitor's address, set by the web app's proxy: the day's allowance is kept by it.
    address: str | None = Header(default=None, alias="X-Attune-Client-Address"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    timezone_name: str = Depends(request_timezone),
) -> MemoryPanel:
    """Learn what the user says instead of a belief; it wins because it is newer."""
    if refused := correction_refused(db, current_user.id, address or None):
        raise HTTPException(status_code=429, detail=refused)
    try:
        correct_fact(db, current_user.id, node_id, payload.text)
    except NodeNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return build_panel(db, current_user.id, timezone_name=timezone_name)


@router.post("/nodes/{node_id}/remove", response_model=MemoryPanel)
def remove_memory_node(
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    timezone_name: str = Depends(request_timezone),
) -> MemoryPanel:
    """Take a belief out of memory; the messages it rested on stop counting toward it."""
    try:
        remove_fact(db, current_user.id, node_id)
    except NodeNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return build_panel(db, current_user.id, timezone_name=timezone_name)


@router.post("/retry", response_model=MemoryPanel)
def retry_memory(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    timezone_name: str = Depends(request_timezone),
) -> MemoryPanel:
    """Hand everything that failed back to the worker."""
    retry_failed(db, current_user.id)
    db.commit()
    return build_panel(db, current_user.id, timezone_name=timezone_name)


@router.post("/review", response_model=MemoryPanel)
def review_memory(
    # The visitor's address, set by the web app's proxy: a review is charged to its allowance.
    address: str | None = Header(default=None, alias="X-Attune-Client-Address"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    timezone_name: str = Depends(request_timezone),
) -> MemoryPanel:
    """Look over memory now and arrange it better, a few times a day at most."""
    if refused := request_review(db, current_user.id, address or None):
        raise HTTPException(status_code=429, detail=refused)
    db.commit()
    return build_panel(db, current_user.id, timezone_name=timezone_name)
