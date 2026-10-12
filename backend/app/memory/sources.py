"""What memory learns from, and what each source changed.

A source is a conversation the user wrote in, or something they typed straight to
memory: a statement from the panel, their onboarding introduction, or a
correction. Sources are the truth; the graph is only what memory made of them.

A conversation is one source however long it runs. It is learned a passage at a
time as each reply ends, and `learned_through` is how far it has been learned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, func, Index, select, String, text, Text
from sqlalchemy.orm import Mapped, mapped_column, Session
from typing import Literal

from app.chat.runs import ConversationRun, spent_today
from app.chat.threads import ConversationMessage, ConversationThread
from app.core.database import Base
from app.core.time import utc_now


# === Sources =======================================================================

CONVERSATION = "conversation"
# The user stating something outright, which skips the gate: it was typed to be kept.
STATED_KINDS = ("statement", "onboarding", "correction")
# A correction is written into the belief as it is typed, so it is never waiting to be known.
CORRECTION = "correction"
# A look over the graph that rearranged it (`review.py`): nothing the user said,
# kept as a source so Activity can show what it did.
REVIEW = "review"
# The user having something forgotten, from the panel or in chat: also nothing
# they said, and kept for the same reason.
REMOVAL = "removal"

# A source's life. "empty" is learned with nothing lasting in it.
PENDING, LEARNING, LEARNED, EMPTY, FAILED = "pending", "learning", "learned", "empty", "failed"
UNRESOLVED = (PENDING, LEARNING, FAILED)


class MemorySource(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (
        Index("uq_memory_sources_thread", "thread_id", unique=True, postgresql_where=text("thread_id IS NOT NULL")),
        Index("ix_memory_sources_user", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    # "conversation", one of the stated kinds, "review" or "removal".
    kind: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # What the user typed, for a stated kind.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For a correction: the node it corrects, and what that said when they corrected it.
    corrects_node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    corrects_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For a conversation: the last message learned.
    learned_through: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=PENDING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    learned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# What each change is called in Activity.
ADDED, ALSO_SAID, CHANGED, TOOK_BACK = "added", "also_said", "changed", "took_back"
# What a review does: none of these changes what memory says.
SPLIT, MERGED, LINKED, GROUPED = "split", "merged", "linked", "grouped"
# The user had it forgotten.
FORGOT = "forgot"


class MemoryChange(Base):
    """One change a source made to memory, as Activity shows it."""

    __tablename__ = "memory_changes"
    __table_args__ = (Index("ix_memory_changes_source", "source_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String, ForeignKey("memory_sources.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    op: Mapped[str] = mapped_column(String, nullable=False)
    # The belief as it read after the change, or as it read when it was taken back.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("memory_nodes.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


# === Reading =======================================================================
#
# What learning reads: a stretch of conversation, or one typed statement.
#
# A conversation is shown the same way every time: each message by a rule that
# depends only on that message, long assistant replies cut to their opening and
# closing, which keeps a question a reply ends with. Assistant text can say what the
# user was answering, but only the user's own words are evidence.

# An assistant reply longer than this keeps its opening and its closing.
REPLY_HEAD, REPLY_TAIL = 300, 300
# Roughly what a reading may hold before its oldest context goes.
STATE_CHARACTERS = 60_000
# Messages before the new ones that are always kept.
KEPT_BEFORE = 6


@dataclass
class Said:
    """One message, or one statement."""

    author: Literal["user", "assistant"]
    text: str
    said_at: datetime
    # The message it is; None for a typed statement.
    message_id: str | None = None


@dataclass
class Reading:
    """A conversation up to the end of what is being learned, and where that starts."""

    conversation: list[Said]
    first_new: int = 0
    # A note on what a statement was typed as, such as the belief a correction replaces.
    hint: str | None = None
    # By short id: messages that do not count toward a belief the user removed.
    not_counting: list[dict[str, str]] = field(default_factory=list)

    def short_ids(self) -> dict[str, Said]:
        """Entries by the short id a model sees ("m1", "m2", …)."""
        return {f"m{index}": said for index, said in enumerate(self.conversation, start=1)}

    def new_from_user(self) -> dict[str, Said]:
        """What is being learned: the user's messages from `first_new` on, by short id."""
        return {short: said for index, (short, said) in enumerate(self.short_ids().items())
                if index >= self.first_new and said.author == "user"}


def shown(said: Said) -> str:
    """A message as a model sees it. Depends on the message alone, so a
    conversation renders the same way every time and its prefix stays cached."""
    if said.author == "user" or len(said.text) <= REPLY_HEAD + REPLY_TAIL + 20:
        return said.text
    return f"{said.text[:REPLY_HEAD]} […] {said.text[-REPLY_TAIL:]}"


def conversation_state(reading: Reading) -> list[dict]:
    """The conversation as a model sees it, cut from the front to fit.

    Everything from a few messages before the new ones is always kept; what goes is
    the oldest context.
    """
    entries = [{"id": short, "author": said.author, "text": shown(said), "sent_at": said.said_at.isoformat()}
               for short, said in reading.short_ids().items()]
    keep_from = max(0, reading.first_new - KEPT_BEFORE)
    while keep_from > 0 and sum(len(entry["text"]) for entry in entries) > STATE_CHARACTERS:
        entries.pop(0)
        keep_from -= 1
    return entries


# === Conversations =================================================================
#
# Everything memory needs from chat, behind one door.
#
# Memory learns from conversations and links back into them, so it has to read
# chat's records. This is the only module in memory that imports chat's models or
# run queries; the rest of memory asks it.

# How much of each stretch of an earlier conversation a reply read is passed on.
LOOKED_UP_CHARACTERS = 2000


def ordered_messages(db: Session, thread_id: str) -> list[ConversationMessage]:
    """A conversation's messages in the order they were written."""
    return db.query(ConversationMessage).filter(
        ConversationMessage.thread_id == thread_id,
    ).order_by(ConversationMessage.created_at, ConversationMessage.id).all()


def find_thread(db: Session, user_id: str, thread_id: str) -> ConversationThread | None:
    return db.query(ConversationThread).filter(
        ConversationThread.id == thread_id, ConversationThread.user_id == user_id,
    ).one_or_none()


def lock_thread(db: Session, user_id: str, thread_id: str) -> ConversationThread | None:
    """The thread, locked for the rest of the transaction, so its boundary cannot move."""
    return db.query(ConversationThread).filter(
        ConversationThread.id == thread_id, ConversationThread.user_id == user_id,
    ).with_for_update().one_or_none()


def last_address(db: Session, user_id: str) -> str | None:
    """The address the user last wrote from, which their background work is charged to."""
    return db.query(ConversationRun.client_address).filter(
        ConversationRun.user_id == user_id, ConversationRun.client_address.is_not(None),
    ).order_by(ConversationRun.created_at.desc()).limit(1).scalar()


def chat_spent(db: Session, client_address: str | None, user_id: str) -> float:
    """Dollars of chat charged in the last day to this address, or to the account without one."""
    return spent_today(db, client_address=client_address, user_id=user_id)


def recent_threads(db: Session, user_id: str, limit: int) -> list[tuple[str, datetime]]:
    """The user's conversations they have said something in, and when they last
    did, most recent first."""
    last = func.max(ConversationMessage.created_at)
    return [(thread_id, said_at) for thread_id, said_at in db.execute(
        select(ConversationMessage.thread_id, last)
        .join(ConversationThread, ConversationThread.id == ConversationMessage.thread_id)
        .where(ConversationThread.user_id == user_id, ConversationMessage.author == "user")
        .group_by(ConversationMessage.thread_id).order_by(last.desc()).limit(limit)
    ).all()]


def turn_lookups(db: Session, user_id: str, reply_ids: list[str]) -> tuple[list[str], list[str]]:
    """What the turns that wrote these replies looked up to answer: the beliefs they
    found, and the stretches of earlier conversations they read, as text."""
    if not reply_ids:
        return [], []
    beliefs: list[str] = []
    passages: list[str] = []
    for (usage,) in db.query(ConversationRun.usage).filter(
        ConversationRun.user_id == user_id, ConversationRun.assistant_message_id.in_(reply_ids),
    ).all():
        looked_up = (usage or {}).get("looked_up") or {}
        beliefs.extend(looked_up.get("beliefs") or [])
        for read in looked_up.get("passages") or []:
            if find_thread(db, user_id, read["thread_id"]) is None:
                continue
            text = "\n\n".join(f"{message.author}: {message.content}"
                                for message in ordered_messages(db, read["thread_id"])
                                if message.status == "completed")
            start = int(read.get("offset") or 0)
            end = min(int(read.get("end") or len(text)), start + LOOKED_UP_CHARACTERS)
            if text[start:end].strip():
                passages.append(text[start:end])
    return list(dict.fromkeys(beliefs)), passages
