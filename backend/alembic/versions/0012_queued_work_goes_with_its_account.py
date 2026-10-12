"""Deleting an account deletes the work queued for it too.

The queue was the one table naming a user that did not go with the account, so a
deleted account left behind what memory had been asked to do for it. What is
already left behind goes now.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "work_queue_user_id_fkey"


def upgrade() -> None:
    op.execute("DELETE FROM work_queue WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM auth_users)")
    op.create_foreign_key(_NAME, "work_queue", "auth_users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint(_NAME, "work_queue", type_="foreignkey")
