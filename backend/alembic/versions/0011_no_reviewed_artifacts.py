"""An applied proposal is completed, and nothing more.

A completed proposal could once be marked reviewed, a second name for the same
thing that nothing in the app set any longer. Any still marked so read as
completed, and the status is no longer allowed.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WITHOUT = "status IN ('draft', 'ready', 'executing', 'completed', 'failed', 'denied', 'superseded', 'expired')"
WITH = "status IN ('draft', 'ready', 'executing', 'completed', 'reviewed', 'failed', 'denied', 'superseded', 'expired')"


def upgrade() -> None:
    op.execute("UPDATE conversation_artifacts SET status = 'completed' WHERE status = 'reviewed'")
    op.drop_constraint("ck_conversation_artifact_status", "conversation_artifacts", type_="check")
    op.create_check_constraint("ck_conversation_artifact_status", "conversation_artifacts", WITHOUT)


def downgrade() -> None:
    op.drop_constraint("ck_conversation_artifact_status", "conversation_artifacts", type_="check")
    op.create_check_constraint("ck_conversation_artifact_status", "conversation_artifacts", WITH)
