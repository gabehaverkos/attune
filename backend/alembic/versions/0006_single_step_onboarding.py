"""Onboarding is one welcome card, so it keeps when it finished, not a step."""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("onboarding_progress", sa.Column("memory_introduced_at", sa.DateTime(timezone=True), nullable=True))
    # The old walkthrough showed memory on its way through; anyone who finished it has seen it.
    op.execute("UPDATE onboarding_progress SET memory_introduced_at = completed_at WHERE step = 6")
    # Anyone partway through starts over on the new card.
    op.execute("UPDATE onboarding_progress SET completed_at = NULL WHERE step < 6")
    op.drop_constraint("ck_onboarding_step", "onboarding_progress", type_="check")
    op.drop_column("onboarding_progress", "basics_submitted")
    op.drop_column("onboarding_progress", "revision")
    op.drop_column("onboarding_progress", "step")


def downgrade() -> None:
    op.add_column("onboarding_progress", sa.Column("step", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("onboarding_progress", sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("onboarding_progress", sa.Column("basics_submitted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE onboarding_progress SET step = 6, basics_submitted = true WHERE completed_at IS NOT NULL")
    for column in ("step", "revision", "basics_submitted"):
        op.alter_column("onboarding_progress", column, server_default=None)
    op.create_check_constraint("ck_onboarding_step", "onboarding_progress", "step >= 0 AND step <= 6")
    op.drop_column("onboarding_progress", "memory_introduced_at")
