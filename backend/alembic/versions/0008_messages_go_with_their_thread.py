"""Deleting a conversation's owner deletes its messages too.

Messages were the one table that did not go with what owned them, so deleting an
account that had ever sent a message failed, and expiring idle guests with it.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "conversation_messages_thread_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_NAME, "conversation_messages", type_="foreignkey")
    op.create_foreign_key(
        _NAME, "conversation_messages", "conversation_threads", ["thread_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(_NAME, "conversation_messages", type_="foreignkey")
    op.create_foreign_key(_NAME, "conversation_messages", "conversation_threads", ["thread_id"], ["id"])
