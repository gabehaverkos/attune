"""The graph memory keeps: what is known about the user: what is known about the user, how it is read, and how it is searched.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pgvector.sqlalchemy import Vector
from pydantic import BaseModel
from sqlalchemy import (
    Boolean, Computed, DateTime, delete, exists, ForeignKey, func, Index, select, String, text, Text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, Session
from time import perf_counter
from typing import Literal

from app.config import get_settings
from app.core.database import Base, SessionLocal
from app.core.observability import event, record_content, trace_embeddings, traced
from app.core.time import utc_now


# === The graph =====================================================================
#
# The graph memory keeps of one user, and every change to it.
#
# Every node is about the user. The root is the user; below it are things (Sarah,
# Attune, Columbus) and beliefs, each a sentence that makes sense on its own. An
# edge has one meaning: the child is about, or elaborates, the parent. A node can
# have several parents ("Sarah co-founded Attune with the user" sits under both),
# and no node is ever its own ancestor.
#
# What a belief rests on is evidence: pairs of a belief and one message (or typed
# statement) that established it. One message can establish several beliefs, and a
# belief can rest on several messages. A belief with no evidence of its own is
# derived: a summary of what is below it, written by memory rather than said by the
# user. A thing is derived in the same way. Derived nodes last only while something
# is below them.
#
# Memory is derived from what the user said. Every change here happens in the
# caller's transaction.

# The width of every stored embedding; changing it means rebuilding memory.
DIMENSIONS = 1024

USER, THING, BELIEF = "user", "thing", "belief"


class MemoryNode(Base):
    __tablename__ = "memory_nodes"
    __table_args__ = (
        Index("uq_memory_nodes_root", "user_id", unique=True, postgresql_where=text("kind = 'user'")),
        Index("ix_memory_nodes_user", "user_id", "created_at"),
        Index("ix_memory_nodes_search", "search", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    # A belief as the user reads it; for a thing, what it is to the user.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # A thing's name.
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Which of SECTIONS it is filed in while it is directly under the user. None
    # until it has been filed, which reads as "other".
    section: Mapped[str | None] = mapped_column(String, nullable=True)
    # A belief memory wrote to sum up what is below it, resting on no evidence of its own.
    derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # What is below it changed since its text was written.
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # None only for the root.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(DIMENSIONS), nullable=True)
    search: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(name, '') || ' ' || text)", persisted=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    def label(self) -> str:
        """How the node is named in a path: a thing by its name, anything else by its text."""
        return (self.name or self.text).strip()


class MemoryEdge(Base):
    """The child is about, or elaborates, the parent."""

    __tablename__ = "memory_edges"
    __table_args__ = (
        Index("ix_memory_edges_parent", "parent_id"),
        Index("ix_memory_edges_user", "user_id"),
    )

    child_id: Mapped[str] = mapped_column(
        String, ForeignKey("memory_nodes.id", ondelete="CASCADE"), primary_key=True)
    parent_id: Mapped[str] = mapped_column(
        String, ForeignKey("memory_nodes.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)


class MemoryEvidence(Base):
    """One message, or one typed statement, that a node rests on."""

    __tablename__ = "memory_node_evidence"
    __table_args__ = (
        Index("ix_memory_node_evidence_source", "source_id"),
        Index("ix_memory_node_evidence_thread", "thread_id"),
    )

    node_id: Mapped[str] = mapped_column(
        String, ForeignKey("memory_nodes.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String, ForeignKey("memory_sources.id", ondelete="CASCADE"), primary_key=True)
    # Empty for a typed statement, which is its own source.
    message_id: Mapped[str] = mapped_column(String, primary_key=True, default="")
    thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # When the user said it.
    said_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MemoryExclusion(Base):
    """A message the user said does not count toward a belief: they removed the belief.

    It is kept by the words of the belief rather than its id, because the node is
    gone. Saying the same thing again in another message still counts.
    """

    __tablename__ = "memory_exclusions"
    __table_args__ = (Index("ix_memory_exclusions_source", "source_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[str] = mapped_column(
        String, ForeignKey("memory_sources.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class NodeNotFoundError(LookupError):
    """The node is gone, or was never this user's to begin with."""


# --- Links -------------------------------------------------------------------------
#
# A user's graph holds hundreds of nodes, so its edges are read in one query and
# walked in memory.

@dataclass
class Links:
    parents: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    children: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add(self, child: str, parent: str) -> None:
        self.parents[child].append(parent)
        self.children[parent].append(child)

    def drop(self, child: str, parent: str) -> None:
        if parent in self.parents[child]:
            self.parents[child].remove(parent)
        if child in self.children[parent]:
            self.children[parent].remove(child)

    def forget(self, node: str) -> None:
        for parent in list(self.parents[node]):
            self.drop(node, parent)
        for child in list(self.children[node]):
            self.drop(child, node)

    def descendants(self, node: str) -> set[str]:
        found: set[str] = set()
        stack = list(self.children[node])
        while stack:
            current = stack.pop()
            if current not in found:
                found.add(current)
                stack.extend(self.children[current])
        return found

    def ancestors(self, nodes: set[str]) -> set[str]:
        found: set[str] = set()
        stack = [parent for node in nodes for parent in self.parents[node]]
        while stack:
            current = stack.pop()
            if current not in found:
                found.add(current)
                stack.extend(self.parents[current])
        return found


def links_of(db: Session, user_id: str) -> Links:
    links = Links()
    for child, parent in db.execute(select(MemoryEdge.child_id, MemoryEdge.parent_id).where(
            MemoryEdge.user_id == user_id)).all():
        links.add(child, parent)
    return links


# --- Changes -----------------------------------------------------------------------

def root(db: Session, user_id: str) -> MemoryNode:
    """The user's own node, made the first time it is needed."""
    node = db.scalars(select(MemoryNode).where(MemoryNode.user_id == user_id, MemoryNode.kind == USER)).first()
    if node is None:
        node = MemoryNode(id=str(uuid.uuid7()), user_id=user_id, kind=USER, text="The user", derived=True)
        db.add(node)
        db.flush()
    return node


def owned(db: Session, user_id: str, node_id: str) -> MemoryNode:
    """A node of the user's other than the root, or NodeNotFoundError.

    Ownership is checked against the node itself, never against what the caller
    was shown: a panel can be stale, and an id is guessable.
    """
    node = db.get(MemoryNode, node_id)
    if node is None or node.user_id != user_id or node.kind == USER:
        raise NodeNotFoundError("That memory is no longer there.")
    return node


def add_node(db: Session, user_id: str, kind: str, text_: str, embedding: list[float], *,
             name: str | None = None, derived: bool = False, section: str | None = None) -> MemoryNode:
    node = MemoryNode(id=str(uuid.uuid7()), user_id=user_id, kind=kind, text=text_, name=name,
                      derived=derived, embedding=embedding, section=section if section in SECTIONS else None)
    db.add(node)
    db.flush()
    return node


def attach(db: Session, user_id: str, links: Links, child: str, parents: list[str]) -> list[str]:
    """Put a node under each parent it can go under. Returns those it went under.

    A parent that is the node itself or already below it would make a cycle, and is
    skipped.
    """
    below = links.descendants(child)
    added = []
    for parent in dict.fromkeys(parents):
        if parent == child or parent in below or parent in links.parents[child]:
            continue
        db.add(MemoryEdge(child_id=child, parent_id=parent, user_id=user_id))
        links.add(child, parent)
        added.append(parent)
    return added


def detach(db: Session, links: Links, child: str, parent: str) -> None:
    db.execute(delete(MemoryEdge).where(MemoryEdge.child_id == child, MemoryEdge.parent_id == parent))
    links.drop(child, parent)


def add_evidence(db: Session, node_id: str, source_id: str, message_id: str | None,
                 thread_id: str | None, said_at: datetime) -> bool:
    """Rest a node on one more message. False when it already did."""
    key = (node_id, source_id, message_id or "")
    if db.get(MemoryEvidence, key) is not None:
        return False
    # Rows added earlier in this batch are not flushed yet, so db.get cannot see them.
    if any(isinstance(row, MemoryEvidence) and (row.node_id, row.source_id, row.message_id) == key
           for row in db.new):
        return False
    db.add(MemoryEvidence(node_id=node_id, source_id=source_id, message_id=message_id or "",
                          thread_id=thread_id, said_at=said_at))
    return True


def remove_node(db: Session, user_id: str, links: Links, node_id: str) -> list[str]:
    """Delete a node; what was below it moves up to its parents. Returns those parents."""
    parents = list(links.parents[node_id])
    home = parents or [root(db, user_id).id]
    for child in list(links.children[node_id]):
        detach(db, links, child, node_id)
        if not links.parents[child]:
            attach(db, user_id, links, child, home)
    links.forget(node_id)
    db.execute(delete(MemoryNode).where(MemoryNode.id == node_id))
    return parents


def prune(db: Session, user_id: str, links: Links) -> set[str]:
    """Settle nodes whose support is gone. Returns the parents of what changed.

    A belief whose evidence is all gone is a summary of what is below it, when
    anything is; otherwise it goes. A derived belief or a thing goes when nothing
    is below it. Removing one can leave its parent bare, so this repeats.
    """
    db.flush()
    touched: set[str] = set()
    while True:
        bare = list(db.scalars(select(MemoryNode).where(
            MemoryNode.user_id == user_id, MemoryNode.kind != USER, MemoryNode.derived.is_(False),
            MemoryNode.kind == BELIEF,
            ~exists().where(MemoryEvidence.node_id == MemoryNode.id),
        )))
        derived = list(db.scalars(select(MemoryNode).where(
            MemoryNode.user_id == user_id, MemoryNode.kind != USER,
            (MemoryNode.derived.is_(True)) | (MemoryNode.kind == THING),
        )))
        gone = [node for node in bare if not links.children[node.id]]
        gone += [node for node in derived if not links.children[node.id]]
        for node in bare:
            if links.children[node.id]:
                node.derived, node.stale = True, True
                touched.add(node.id)
        if not gone:
            break
        for node in gone:
            touched.update(links.parents[node.id])
            touched.discard(node.id)
            links.forget(node.id)
            db.execute(delete(MemoryNode).where(MemoryNode.id == node.id))
        db.flush()
    return touched


def mark_stale(db: Session, links: Links, node_ids: set[str]) -> bool:
    """Mark every summary and thing at or above these nodes for rewriting. True when
    any was marked."""
    ids = node_ids | links.ancestors(node_ids)
    if not ids:
        return False
    marked = False
    for node in db.scalars(select(MemoryNode).where(MemoryNode.id.in_(ids))):
        if node.kind == THING or (node.kind == BELIEF and node.derived):
            node.stale = True
            marked = True
    return marked


def forget_all(db: Session, user_id: str) -> int:
    """Delete the user's whole graph."""
    return db.query(MemoryNode).filter(MemoryNode.user_id == user_id).delete(synchronize_session=False)


# === Reads =========================================================================
#
# Every read of the graph, as plain queries on the caller's session.
#
# A read is taken fresh whenever it is needed, so it always describes memory as it
# stands, including a belief the user removed a moment ago. Search is in `search.py`.

class MemoryNodeFact(BaseModel):
    """A node as the assistant is given it."""

    id: str
    text: str
    kind: str
    # Where it sits: the labels from the top down to it ("Computing › AI").
    path: str = ""


@dataclass
class Graph:
    nodes: dict[str, MemoryNode]
    links: Links
    # Evidence each node rests on directly.
    evidence: Counter
    root_id: str | None

    @cached_property
    def weight(self) -> dict[str, int]:
        """How much the user has said toward each node: its evidence and everything below it."""
        return {node_id: self.evidence[node_id] + sum(self.evidence[below] for below in self.links.descendants(node_id))
                for node_id in self.nodes}

    def children(self, node_id: str) -> list[str]:
        """What is below a node, most said about first, then newest.

        Below the user, a node that is also under something more specific shows
        only there: "Sarah co-founded Attune" belongs with Sarah.
        """
        found = [child for child in self.links.children[node_id] if child in self.nodes]
        if node_id == self.root_id:
            found = [child for child in found
                     if not any(parent in self.nodes and parent != node_id for parent in self.links.parents[child])]
        return sorted(found, key=lambda child: (-self.weight[child], -self.nodes[child].created_at.timestamp()))

    def path(self, node_id: str) -> str:
        """The labels above a node down to its parent, by the heaviest parent at each step."""
        labels: list[str] = []
        seen = {node_id}
        current = node_id
        while True:
            parents = [parent for parent in self.links.parents[current]
                       if parent in self.nodes and parent not in seen and parent != self.root_id]
            if not parents:
                return " › ".join(reversed(labels))
            current = max(parents, key=lambda parent: self.weight[parent])
            seen.add(current)
            labels.append(self.nodes[current].label())

    def fact(self, node_id: str) -> MemoryNodeFact:
        node = self.nodes[node_id]
        return MemoryNodeFact(id=node.id, text=line(node), kind=node.kind, path=self.path(node_id))


# The light structure memory has: what is directly about the user is filed in one
# of these, in this order, in the panel and in what chat is given. The list is
# fixed, so a model can file a node but never invent a section. Nothing below the
# top is filed: a node there is already under what it is about.
SECTIONS = {
    "about": "Personal",
    "people": "People",
    "pets": "Pets",
    "work": "Work",
    "plans": "Plans and goals",
    "habits": "Habits and preferences",
    "other": "Other",
}
UNFILED = "other"

# Given to each model that files a node.
FILING = """Sections: a node directly under n0 is filed in one "section", the one the
user would look for it in:
- "about": who they are: name, home, background, health, what they eat.
- "people": a person in their life, whatever is known of them.
- "pets": an animal of theirs.
- "work": a job, employer, school, or project of theirs, and how things stand there.
- "plans": something ahead: a goal, a trip, an event.
- "habits": how they do things, and what they like or avoid.
- "other": anything else.
"""


def section_of(node: MemoryNode) -> str:
    return node.section if node.section in SECTIONS else UNFILED


# How every node's text is written, given to each model that writes one. Memory
# keeps a text as the user reads it: the panel shows what is stored, and nothing
# rewords it on the way.
FORM = """How a text is written: as a short note the user will read exactly as it
is, starting with a capital, with no full stop at the end.
- A belief about the user has no subject: "Lives in Columbus", "Product designer
  at Freightline", "Name is John", "Thinking of becoming a rancher".
- A belief about someone or something else starts with its name: "Maria is
  allergic to shellfish", "Dev thinks the map view is the risky part".
- Never "the user", "you" or "your", and never he, she or they for the user:
  nothing says which they are. Anyone else is their name, or simply what they are
  to the user with nothing in front: "Wants to help girlfriend make income",
  "Sister Elena lives in Denver", "Manager wants the redesign in pilot by June".
- Leave out what goes without saying: "Does best work in the mornings", "Coffee
  after noon was wrecking sleep".
- A thing's text says what it is to the user without its name, which is shown
  beside it: for Sarah, "Sister, and Attune co-founder".
"""


def line(node: MemoryNode) -> str:
    """A node as one line: a thing with what it is, a belief as it is written."""
    text_ = " ".join(node.text.split())
    if node.name and not text_.casefold().startswith(node.name.casefold()):
        return f"{node.name}: {text_}"
    return text_


def load(db: Session, user_id: str) -> Graph:
    nodes = {node.id: node for node in db.scalars(select(MemoryNode).where(MemoryNode.user_id == user_id))}
    evidence: Counter[str] = Counter(dict(db.execute(
        select(MemoryEvidence.node_id, func.count()).join(MemoryNode, MemoryNode.id == MemoryEvidence.node_id)
        .where(MemoryNode.user_id == user_id).group_by(MemoryEvidence.node_id)).tuples().all()))
    root_id = next((node.id for node in nodes.values() if node.kind == USER), None)
    return Graph(nodes=nodes, links=links_of(db, user_id), evidence=evidence, root_id=root_id)


def facts_by_id(db: Session, user_id: str, node_ids: list[str]) -> list[str]:
    """The named nodes as lines, in the order given, dropping any that are gone.

    A belief the user removed since it was recalled simply does not come back,
    which is how a session stops repeating something it was told to forget.
    """
    if not node_ids:
        return []
    found = {node.id: node for node in db.scalars(select(MemoryNode).where(
        MemoryNode.user_id == user_id, MemoryNode.id.in_(node_ids), MemoryNode.kind != USER))}
    return [line(found[node_id]) for node_id in node_ids if node_id in found]


# --- The turn's outline ------------------------------------------------------------
#
# A turn opens with the top of the graph: what is directly about the user, then
# what is about that, as far down as the budget allows. The agent answers from
# that or searches for detail when the request benefits from it.

HEADER = ("Memory context (what is known about the user, most general first, under a heading for each kind; each "
          "line indented under what it is about; a line that names no one is about the user):")

GUIDANCE = (
    "Use this when it is enough to respond. Search memory only when additional detail "
    "would materially improve the answer, not merely because a topic or name appears. "
    "This is a bounded view, not everything known. The user's current statements take precedence."
)

# Said outright, because the alternative is silence: a turn with no memory section
# reads the same as a turn whose section was lost, and the agent then searches an
# empty memory — twice, in the turn that prompted this — before answering.
EMPTY = (
    "Memory holds nothing about this user yet: nothing has been learned from a "
    "conversation or direct assertion so far. Do not search memory this turn; it has nothing to return. "
    "Rely on what the user says now."
)

# Memory is equally empty here, but the user is not a stranger: they have already
# told the application something, and it is in this prompt, below. Saying only
# that memory is empty is what had the assistant tell someone it had no idea who
# they were, minutes after they typed their name into a form.
EMPTY_WITH_SUBMITTED = (
    "Memory holds nothing about this user yet, but information they submitted is still on "
    "its way in and is included below as a user-provided introduction. Do not search memory "
    "this turn; it has nothing to return. Use what they submitted, and never tell them you "
    "have no record of something they have already given you."
)

LINE_CHARACTERS = 300


def _shown_line(node: MemoryNode, depth: int) -> str:
    return f"{'  ' * depth}- {line(node)[:LINE_CHARACTERS]}"


def outline(graph: Graph, budget: int) -> list[str]:
    """The graph from the top down, breadth first, until the budget is spent.

    A node under several parents is shown once, under the first. A node with
    more below it than fits says how much more.
    """
    if graph.root_id is None:
        return []
    included: set[str] = set()
    # The headings the top is filed under.
    used = sum(len(label) + 2 for label in SECTIONS.values())
    frontier, depth, full = [graph.root_id], 0, False
    while frontier and not full:
        following = []
        for parent in frontier:
            for child in graph.children(parent):
                if child in included:
                    continue
                cost = len(_shown_line(graph.nodes[child], depth)) + 1
                if used + cost > budget:
                    full = True
                    break
                included.add(child)
                used += cost
                following.append(child)
            if full:
                break
        frontier, depth = following, depth + 1

    lines: list[str] = []
    placed: set[str] = set()

    def render(children: list[str], depth: int) -> None:
        for child in children:
            if child in included and child not in placed:
                placed.add(child)
                hidden = sum(1 for below in graph.links.children[child]
                             if below in graph.nodes and below not in included)
                suffix = f" (+{hidden} more; search memory)" if hidden else ""
                lines.append(_shown_line(graph.nodes[child], depth) + suffix)
                render(graph.children(child), depth + 1)

    top = graph.children(graph.root_id)
    if not any(graph.nodes[child].section for child in top):
        # Nothing has been filed yet: a single "Other" would say nothing.
        render(top, 0)
        return lines
    for key, label in SECTIONS.items():
        filed = [child for child in top if child in included and section_of(graph.nodes[child]) == key]
        if filed:
            lines.append(f"{label}:")
            render(filed, 0)
    return lines


def memory_outline(db: Session, user_id: str, *, budget: int, awaiting_ingestion: bool = False) -> str:
    """The turn's memory section."""
    graph = load(db, user_id)
    lines = outline(graph, budget - len(HEADER) - len(GUIDANCE) - 4)
    if not lines:
        return EMPTY_WITH_SUBMITTED if awaiting_ingestion else EMPTY
    return "\n".join([HEADER, *lines, "", GUIDANCE])


# === Search ========================================================================
#
# Hybrid search over one user's graph: words and meaning, fused by rank.
#
# Full-text search finds nodes that share the query's words; vector search finds
# ones that mean something close to it, above a similarity floor. Each ranks its
# own candidates, and reciprocal rank fusion merges the two lists, so a node both
# find rises to the top. A user's graph holds hundreds of nodes, so vector search
# compares against every one exactly rather than through an approximate index.
#
# A node found comes with where it sits (its path) and the first few things below
# it, so a search for Attune returns the project and what is known of it together.

# --- Embeddings --------------------------------------------------------------------
#
# Embeddings for nodes and for what is searched, from Voyage.
#
# Fixed for a stronger reason than the models in config.py: every stored embedding
# was made by this model at this width, and search compares against them, so
# changing either quietly degrades search until memory is rebuilt.

VOYAGE_MODEL = "voyage-4"


async def embed(texts: list[str], purpose: Literal["document", "query"]) -> list[list[float]]:
    """One embedding per text, in order. A node is a document; a search is a query."""
    from voyageai.client_async import AsyncClient

    if not texts:
        return []
    api_key = get_settings().voyage_api_key
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY is required for memory.")
    # A client per call: it is cheap, and one kept across calls outlives the
    # event loop it was made on.
    client = AsyncClient(api_key=api_key)
    trace_embeddings(client)
    result = await client.embed(texts, model=VOYAGE_MODEL, input_type=purpose,
                                output_dimension=DIMENSIONS)
    return [[float(value) for value in embedding] for embedding in result.embeddings]


# --- Search ------------------------------------------------------------------------

MAX_RESULT_LIMIT = 50
# Cosine similarity below this is not a match, however few matches there are.
SIMILARITY_FLOOR = 0.6
# Reciprocal rank fusion scores a node 1 / (rank + this) in each list it is in.
RANK_CONSTANT = 1
# What a node found brings from below it.
CHILDREN_PER_HIT = 5


def _fuse(*rankings: list[str]) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, node_id in enumerate(ranking):
            scores[node_id] += 1 / (rank + RANK_CONSTANT)
    return sorted(scores, key=lambda node_id: scores[node_id], reverse=True)


def ranked(db: Session, user_id: str, query: str, vector: list[float], limit: int) -> list[str]:
    """Node ids matching a query by its words and by its embedding, best first."""
    pool = min(limit * 2, MAX_RESULT_LIMIT)
    scope = [MemoryNode.user_id == user_id, MemoryNode.kind != USER]
    # Any of the query's words, not all of them: plainto_tsquery joins its
    # stemmed terms with AND, and they are rejoined with OR.
    terms = func.to_tsquery("simple", func.replace(
        func.cast(func.plainto_tsquery("english", query), Text), "&", "|"))
    distance = MemoryNode.embedding.cosine_distance(vector)
    by_words = list(db.scalars(
        select(MemoryNode.id).where(*scope, MemoryNode.search.op("@@")(terms))
        .order_by(func.ts_rank(MemoryNode.search, terms).desc()).limit(pool)))
    by_meaning = list(db.scalars(
        select(MemoryNode.id).where(*scope, distance <= 1 - SIMILARITY_FLOOR)
        .order_by(distance).limit(pool)))
    return _fuse(by_words, by_meaning)[:limit]


@traced("memory.retrieval")
async def search_facts(user_id: str, query: str, *, limit: int = 10) -> list[MemoryNodeFact]:
    """Relevant nodes, most relevant first, each followed by the first few below it."""
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query is required.")
    if limit < 1 or limit > MAX_RESULT_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_RESULT_LIMIT}.")

    started = perf_counter()
    query_hash = hashlib.sha256(normalized_query.encode()).hexdigest()
    event("memory.retrieval_started", user_id=user_id, query_hash=query_hash, limit=limit)
    try:
        [vector] = await embed([normalized_query], "query")
        def read() -> tuple[list[str], Graph]:
            with SessionLocal() as db:
                return ranked(db, user_id, normalized_query, vector, limit), load(db, user_id)

        hits, graph = await asyncio.to_thread(read)
        facts: list[MemoryNodeFact] = []
        seen: set[str] = set()
        for node_id in hits:
            for found in [node_id, *graph.children(node_id)[:CHILDREN_PER_HIT]] if node_id in graph.nodes else []:
                if found not in seen:
                    seen.add(found)
                    facts.append(graph.fact(found))
        record_content(input=normalized_query, output=[f"{fact.path} › {fact.text}" if fact.path else fact.text
                                                       for fact in facts])
        event("memory.retrieval_completed", user_id=user_id, query_hash=query_hash,
              fact_ids=[fact.id for fact in facts], duration_ms=round((perf_counter() - started) * 1000))
        return facts
    except Exception as error:
        event("memory.retrieval_failed", level=logging.ERROR, user_id=user_id,
              query_hash=query_hash, error_type=type(error).__name__,
              duration_ms=round((perf_counter() - started) * 1000))
        raise
