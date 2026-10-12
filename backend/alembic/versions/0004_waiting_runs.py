"""A reply can wait on a card the user has to decide, and carry on once they do.

Adds the `waiting` run status, counted as active, and the `hold` it resumes from.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _statuses(active: str, all_: str) -> None:
    op.drop_constraint("ck_conversation_run_status", "conversation_runs", type_="check")
    op.create_check_constraint("ck_conversation_run_status", "conversation_runs", f"status IN ({all_})")
    op.drop_index("uq_conversation_run_active_thread", table_name="conversation_runs")
    op.create_index("uq_conversation_run_active_thread", "conversation_runs", ["thread_id"], unique=True,
                    postgresql_where=sa.text(f"status IN ({active})"))


def upgrade() -> None:
    op.add_column("conversation_runs", sa.Column("hold", sa.JSON(), nullable=True))
    _statuses("'queued', 'running', 'waiting'",
              "'queued', 'running', 'waiting', 'completed', 'failed', 'canceled'")


def downgrade() -> None:
    op.execute("UPDATE conversation_runs SET status = 'completed', hold = NULL WHERE status = 'waiting'")
    _statuses("'queued', 'running'", "'queued', 'running', 'completed', 'failed', 'canceled'")
    op.drop_column("conversation_runs", "hold")
