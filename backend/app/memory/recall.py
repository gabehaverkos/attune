"""What memory gives back, in the two shapes it is asked for.

First what a turn reads as it is assembled: the top of the graph, whatever it
recalled earlier, and anything the user said that is not learned yet. Then what
the user sees in the panel: the whole graph with where each belief came from,
and the sources memory learned from.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Literal

from app.core.queue import PENDING as ITEM_PENDING, RUNNING, WorkItem
from app.core.time import utc_now
from app.memory.sources import (
    ADDED, ALSO_SAID, CHANGED, CONVERSATION, CORRECTION, FAILED, FORGOT, GROUPED, LEARNED, LEARNING, LINKED,
    MemoryChange, MemorySource, MERGED, PENDING, recent_threads, REMOVAL, REVIEW, SPLIT, STATED_KINDS,
    TOOK_BACK, UNRESOLVED,
)
from app.memory.learning import unlearned_statements
from app.memory.work import lane, REVIEW as REVIEW_WORK, review_state, waiting_counts
from app.memory.graph import facts_by_id, load, memory_outline, MemoryEvidence, section_of, SECTIONS, THING


# === A turn ========================================================================
#
# What a turn reads from memory as it is assembled.
#
# It is read in the turn's own session, from the same database as everything else
# the turn reads. What it carries is the top of the graph, the nodes this session
# already recalled, and anything the user said that has not been learned yet.

@dataclass
class PendingMemory:
    """What memory owes the user: things they said that it has not finished learning.

    Read in the turn's own transaction, so it agrees with the conversation it
    accompanies.
    """

    # What the user stated outright (onboarding, the panel, corrections), as they said it.
    statements: list[str] = field(default_factory=list)
    # Conversations with something the user said that is waiting to be learned,
    # being learned, or failed to be.
    unlearned_threads: set[str] = field(default_factory=set)


@dataclass
class TurnMemory:
    """What memory holds for a turn, read as the turn is assembled."""

    index_text: str
    recalled: list[str] = field(default_factory=list)


def pending_memory(db: Session, user_id: str) -> PendingMemory:
    return PendingMemory(
        statements=unlearned_statements(db, user_id),
        unlearned_threads={thread_id for thread_id in db.scalars(select(MemorySource.thread_id).where(
            MemorySource.user_id == user_id, MemorySource.kind == CONVERSATION,
            MemorySource.status.in_(UNRESOLVED))) if thread_id},
    )


def read_turn_memory(db: Session, user_id: str, *, recalled_ids: list[str], pending: PendingMemory,
                     budget: int) -> TurnMemory:
    """Everything a turn reads from memory."""
    return TurnMemory(
        index_text=memory_outline(db, user_id, budget=budget, awaiting_ingestion=bool(pending.statements)),
        recalled=facts_by_id(db, user_id, recalled_ids),
    )


# === The panel =====================================================================
#
# What the memory panel shows: the graph, where each belief came from, and what memory learned from.
#
# One read builds the whole panel. The graph comes as a flat list of nodes with
# their parents and children, from which the panel draws the outline; a node under
# several parents appears under each. Each node carries its evidence: when the
# user said it, and where, so the panel can open the conversation at that message.
# Beside the graph: statements still being learned, and every source with what it
# changed.
#
# Everything is read when the panel asks, so it shows memory as it stands now.

SOURCE_LIMIT = 50

SourceKind = Literal["conversation", "statement", "onboarding", "correction", "review", "removal"]


class Evidence(BaseModel):
    """One time the user said what a node rests on."""

    source_id: str
    kind: SourceKind
    said_at: datetime
    # Where to open the conversation; null for something typed.
    thread_id: str | None = None
    message_id: str | None = None
    # What they typed, for something typed.
    text: str | None = None


class Node(BaseModel):
    id: str
    kind: Literal["thing", "belief"]
    # What the panel shows: a belief's content, or a thing's name.
    label: str
    # What a thing is, shown when it is opened; for a belief, the same as its label.
    text: str
    # Written by memory to sum up what is below it.
    derived: bool
    # Where it is listed while it is directly about the user: a key of `sections`.
    section: str
    # Empty when it is directly about the user.
    parents: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    learned_at: datetime


class PendingStatement(BaseModel):
    """Something the user told memory directly that it has not learned yet."""

    source_id: str
    text: str
    status: Literal["learning", "failed"]


SourceStatus = Literal["learning", "waiting", "learned", "failed", "empty"]

CHANGE_LABELS = {ADDED: "Added", ALSO_SAID: "Also said", CHANGED: "Changed", TOOK_BACK: "Took back",
                 SPLIT: "Split out", MERGED: "Merged into", LINKED: "Linked", GROUPED: "Grouped under",
                 FORGOT: "Forgot"}


class Change(BaseModel):
    """One change a source made to memory."""

    label: Literal["Added", "Also said", "Changed", "Took back", "Split out", "Merged into", "Linked", "Grouped under",
                   "Forgot"]
    text: str
    # The node it left, while it is still there.
    node_id: str | None = None


class Source(BaseModel):
    id: str
    kind: SourceKind
    # "waiting": the user said something memory has not learned and nothing is
    # queued to learn it.
    status: SourceStatus
    created_at: datetime
    statement: str | None = None
    thread_id: str | None = None
    error: str | None = None
    # Nodes resting on this source that are still there.
    taught: list[str] = Field(default_factory=list)
    # For a conversation: the user's messages in it memory has not learned yet.
    waiting: int = 0
    changes: list[Change] = Field(default_factory=list)


class Section(BaseModel):
    key: str
    label: str


class MemoryPanel(BaseModel):
    working: bool
    failed: int
    # A review the user asked for is under way, and how many more they may ask for today.
    reviewing: bool = False
    reviews_left: int = 0
    # Every node but the user's own, and which ones are directly about the user, in order.
    nodes: list[Node]
    top: list[str]
    # What the top is listed under, in order.
    sections: list[Section] = Field(default_factory=list)
    pending: list[PendingStatement] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    observed_at: datetime


# --- Reading it --------------------------------------------------------------------

def _evidence(db: Session, node_ids: list[str], sources: dict[str, MemorySource]) -> dict[str, list[Evidence]]:
    found: dict[str, list[Evidence]] = defaultdict(list)
    if not node_ids:
        return found
    for row in db.scalars(select(MemoryEvidence).where(MemoryEvidence.node_id.in_(node_ids))
                          .order_by(MemoryEvidence.said_at.desc())):
        source = sources.get(row.source_id)
        if source is None:
            continue
        typed = source.kind in STATED_KINDS
        found[row.node_id].append(Evidence(
            source_id=source.id, kind=source.kind, said_at=row.said_at,  # type: ignore[arg-type]
            thread_id=None if typed else row.thread_id, message_id=None if typed else row.message_id or None,
            text=source.text if typed else None,
        ))
    return found


def _pending(sources: list[MemorySource]) -> list[PendingStatement]:
    return [PendingStatement(source_id=source.id, text=source.text or "",
                             status="failed" if source.status == FAILED else "learning")
            for source in sorted(sources, key=lambda source: source.created_at)
            if source.kind in STATED_KINDS and source.kind != CORRECTION
            and source.status in (PENDING, LEARNING, FAILED)]


def _status(source: MemorySource, queued: bool, waiting: int) -> SourceStatus:
    if source.status == FAILED:
        return "failed"
    if queued or source.status in (PENDING, LEARNING):
        return "learning"
    if waiting:
        return "waiting"
    return "learned" if source.status == LEARNED else "empty"


def _sources(db: Session, user_id: str, rows: list[MemorySource], taught: dict[str, set[str]],
             current: set[str]) -> list[Source]:
    """What memory learned from, newest first: each statement, and each conversation,
    placed by the last thing the user said in it."""
    queued = {row[0] for row in db.query(WorkItem.subject).filter(
        WorkItem.lane == lane(user_id), WorkItem.status.in_((ITEM_PENDING, RUNNING)))}
    said_at = dict(recent_threads(db, user_id, SOURCE_LIMIT))
    listed = [source for source in rows
              if source.kind in STATED_KINDS or source.kind in (REVIEW, REMOVAL) or source.thread_id in said_at]
    listed.sort(key=lambda source: said_at.get(source.thread_id or "", source.created_at), reverse=True)
    listed = listed[:SOURCE_LIMIT]
    changes: dict[str, list[Change]] = defaultdict(list)
    if listed:
        for row in db.scalars(select(MemoryChange).where(MemoryChange.source_id.in_([s.id for s in listed]))
                              .order_by(MemoryChange.created_at, MemoryChange.id)):
            changes[row.source_id].append(Change(
                label=CHANGE_LABELS[row.op],  # type: ignore[arg-type]
                text=row.text, node_id=row.node_id if row.node_id in current else None))
    waiting_by = waiting_counts(db, [source for source in listed if source.kind == CONVERSATION])
    sources = []
    for source in listed:
        conversation = source.kind == CONVERSATION
        waiting = waiting_by.get(source.id, 0)
        status = _status(source, (source.thread_id if conversation else source.id) in queued, waiting)
        sources.append(Source(
            id=source.id, kind=source.kind, status=status,  # type: ignore[arg-type]
            created_at=said_at.get(source.thread_id or "", source.created_at),
            statement=None if conversation else source.text, thread_id=source.thread_id,
            error="Attune couldn't learn from this." if status == "failed" else None,
            taught=sorted(taught.get(source.id, set()) & current), waiting=waiting,
            changes=changes[source.id],
        ))
    return sources


def build_panel(db: Session, user_id: str, *, timezone_name: str = "UTC") -> MemoryPanel:
    observed_at = utc_now()
    graph = load(db, user_id)
    rows = list(db.scalars(select(MemorySource).where(MemorySource.user_id == user_id)))
    by_id = {source.id: source for source in rows}
    node_ids = [node_id for node_id in graph.nodes if node_id != graph.root_id]
    evidence = _evidence(db, node_ids, by_id)
    taught: dict[str, set[str]] = defaultdict(set)
    for node_id, items in evidence.items():
        for item in items:
            taught[item.source_id].add(node_id)

    nodes = []
    for node_id in node_ids:
        node = graph.nodes[node_id]
        thing = node.kind == THING
        nodes.append(Node(
            id=node.id, kind=node.kind,  # type: ignore[arg-type]
            label=(node.name or node.text).strip(), text=node.text.strip(), derived=node.derived and not thing,
            section=section_of(node),
            parents=[parent for parent in graph.links.parents[node_id]
                     if parent in graph.nodes and parent != graph.root_id],
            children=graph.children(node_id), evidence=evidence.get(node_id, []), learned_at=node.created_at,
        ))

    failed = sum(source.status == FAILED for source in rows)
    # A review waits up to a day for its turn, which is not memory being busy.
    working = db.query(WorkItem).filter(
        WorkItem.lane == lane(user_id), WorkItem.status.in_((ITEM_PENDING, RUNNING)),
        WorkItem.kind != REVIEW_WORK).count() > 0
    reviewing, reviews_left = review_state(db, user_id)
    return MemoryPanel(
        working=working, failed=failed, reviewing=reviewing, reviews_left=reviews_left, nodes=nodes,
        top=graph.children(graph.root_id) if graph.root_id else [],
        sections=[Section(key=key, label=label) for key, label in SECTIONS.items()],
        pending=_pending(rows),
        sources=_sources(db, user_id, rows, taught, set(node_ids)),
        observed_at=observed_at,
    )
