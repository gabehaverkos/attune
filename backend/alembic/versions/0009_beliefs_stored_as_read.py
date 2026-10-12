"""Memory keeps each belief as the user reads it.

Beliefs were stored as sentences about "the user" ("The user lives in Columbus.")
and reworded on the way to the panel ("Lives in Columbus"). They are now written
in the form they are read in, and shown as stored. This gives what is already
held the wording the panel showed for it, once. Things and summaries, which
memory writes itself, are marked to be written again in the new form.
"""
from collections.abc import Sequence
import re

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The panel's rewording as it was, kept here because this is its last use.
_SUBJECT = re.compile(r"^(?:[Tt]he )?[Uu]ser(?:'s|\s+(?:is|are|was|were)(?=\s))?\s+")
_USER = [(r"\b[Tt]he user's\b", "your"), (r"\b[Tt]he user\b", "you"), (r"\bUser's\b", "your"), (r"\bUser\b", "you")]


def _content(text: str, *, about_user: bool = True) -> str:
    text = " ".join(text.split())
    if about_user:
        text = _SUBJECT.sub("", text, count=1)
    for pattern, replacement in _USER:
        text = re.sub(pattern, replacement, text)
    return text


def _shown(text: str) -> str:
    text = _content(text)
    if text.split(" ", 1)[0].islower():
        text = text[:1].upper() + text[1:]
    last = text.rsplit(" ", 1)[-1]
    if last.endswith(".") and "." not in last[:-1]:
        text = text[:-1]
    return text


def upgrade() -> None:
    db = op.get_bind()
    nodes = sa.table("memory_nodes", sa.column("id"), sa.column("kind"), sa.column("text"),
                     sa.column("derived"), sa.column("stale"))
    for node_id, kind, text, derived in db.execute(
            sa.select(nodes.c.id, nodes.c.kind, nodes.c.text, nodes.c.derived).where(nodes.c.kind != "user")).all():
        thing = kind == "thing"
        values = {"text": _content(text, about_user=False) if thing else _shown(text)}
        if thing or derived:
            values["stale"] = True
        db.execute(nodes.update().where(nodes.c.id == node_id).values(**values))
    changes = sa.table("memory_changes", sa.column("id"), sa.column("text"))
    for change_id, text in db.execute(sa.select(changes.c.id, changes.c.text)).all():
        if text:
            db.execute(changes.update().where(changes.c.id == change_id).values(text=_shown(text)))


def downgrade() -> None:
    # The wording is the same to read either way; what was stored before is not kept.
    pass
