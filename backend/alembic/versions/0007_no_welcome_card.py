"""Onboarding has no welcome card, so nothing records finishing one."""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("onboarding_progress", "completed_at")


def downgrade() -> None:
    op.add_column("onboarding_progress", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
