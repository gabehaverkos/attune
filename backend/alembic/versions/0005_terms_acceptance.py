"""Accounts record the terms they were opened under, and when they were agreed to."""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Accounts opened before there were terms keep both empty.
    op.add_column("auth_users", sa.Column("terms_version", sa.String(), nullable=True))
    op.add_column("auth_users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_users", "terms_accepted_at")
    op.drop_column("auth_users", "terms_version")
