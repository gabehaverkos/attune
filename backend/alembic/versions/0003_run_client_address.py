"""The daily chat budget is kept per visitor address, across accounts."""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversation_runs", sa.Column("client_address", sa.String(), nullable=True))
    op.create_index(
        "ix_conversation_run_address_created", "conversation_runs", ["client_address", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_run_address_created", table_name="conversation_runs")
    op.drop_column("conversation_runs", "client_address")
