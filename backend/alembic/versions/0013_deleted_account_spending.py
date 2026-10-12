"""What a deleted account spent stays against its address for a day.

Spending was counted from an account's replies, which go with the account, so
logging out of a demo and starting another started the day's allowance again.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deleted_account_spending",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("client_address", sa.String(), nullable=False),
        sa.Column("dollars", sa.Float(), nullable=False),
        sa.Column("spent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deleted_account_spending_address", "deleted_account_spending", ["client_address", "spent_at"])


def downgrade() -> None:
    op.drop_table("deleted_account_spending")
