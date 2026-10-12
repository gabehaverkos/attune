"""What is directly about the user is filed in a section.

The top of a memory was one list, a person beside a habit beside a trip. Each
node there now says which section it is listed in. Nothing held is filed yet:
it reads as unfiled until memory's next review files it.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_nodes", sa.Column("section", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("memory_nodes", "section")
