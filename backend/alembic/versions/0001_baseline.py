"""The Attune schema, created on an empty PostgreSQL database.

The first release's starting point: it replaces the development history that came
before it, and is not an upgrade path from a database that history built.
"""
from collections.abc import Sequence

from alembic import op
import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A routine's windows are read as part of the routine, so editing a window has to
# age the routine too, or the change is never picked up.
_TOUCH_ROUTINE = """
CREATE OR REPLACE FUNCTION touch_calendar_routine_from_window()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE interfaces_calendar_routines
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.routine_id ELSE NEW.routine_id END;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('auth_users',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('email_verified', sa.Boolean(), nullable=False),
    sa.Column('image', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('auth_verifications',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('identifier', sa.String(), nullable=False),
    sa.Column('value', sa.String(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('work_queue',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('lane', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=True),
    sa.Column('subject', sa.String(), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('dedupe_key', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('owner', sa.String(), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_work_queue_due', 'work_queue', ['status', 'run_after'], unique=False)
    op.create_index('ix_work_queue_subject', 'work_queue', ['subject'], unique=False)
    op.create_index('ix_work_queue_user', 'work_queue', ['user_id', 'created_at'], unique=False)
    op.create_index('ux_work_queue_pending_dedupe', 'work_queue', ['dedupe_key'], unique=True, postgresql_where=sa.text("status = 'pending'"), sqlite_where=sa.text("status = 'pending'"))
    op.create_index('ux_work_queue_running_lane', 'work_queue', ['lane'], unique=True, postgresql_where=sa.text("status = 'running'"), sqlite_where=sa.text("status = 'running'"))
    op.create_table('auth_accounts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('account_id', sa.String(), nullable=False),
    sa.Column('provider_id', sa.String(), nullable=False),
    sa.Column('access_token', sa.String(), nullable=True),
    sa.Column('refresh_token', sa.String(), nullable=True),
    sa.Column('id_token', sa.String(), nullable=True),
    sa.Column('access_token_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refresh_token_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('scope', sa.String(), nullable=True),
    sa.Column('password', sa.String(), nullable=True),
    sa.Column('password_expired', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('auth_sessions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('token', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ip_address', sa.String(), nullable=True),
    sa.Column('user_agent', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token')
    )
    op.create_table('conversation_threads',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), server_default='active', nullable=False),
    sa.Column('archive_reason', sa.String(), nullable=True),
    sa.Column('handoff_request_id', sa.String(), nullable=True),
    sa.Column('previous_thread_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recalled_fact_ids', sa.JSON(), nullable=True),
    sa.CheckConstraint("status IN ('active', 'archived')", name='ck_conversation_thread_status'),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'handoff_request_id', name='uq_thread_handoff_request')
    )
    op.create_index('ix_conversation_thread_user_status', 'conversation_threads', ['user_id', 'status'], unique=False)
    op.create_table('interfaces_calendar_events',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('timezone', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_calendar_event_user_time', 'interfaces_calendar_events', ['user_id', 'starts_at', 'ends_at'], unique=False)
    op.create_table('interfaces_calendar_routines',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('archive_reason', sa.String(), nullable=True),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('effective_until', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind IN ('sleep', 'work', 'other')", name='ck_calendar_routine_kind'),
    sa.CheckConstraint("status IN ('active', 'archived')", name='ck_calendar_routine_status'),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_calendar_routine_user_status_kind', 'interfaces_calendar_routines', ['user_id', 'status', 'kind'], unique=False)
    op.create_table('interfaces_preferences',
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'key')
    )
    op.create_table('memory_nodes',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('derived', sa.Boolean(), nullable=False),
    sa.Column('stale', sa.Boolean(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('search', postgresql.TSVECTOR(), sa.Computed("to_tsvector('english', coalesce(name, '') || ' ' || text)", persisted=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_nodes_search', 'memory_nodes', ['search'], unique=False, postgresql_using='gin')
    op.create_index('ix_memory_nodes_user', 'memory_nodes', ['user_id', 'created_at'], unique=False)
    op.create_index('uq_memory_nodes_root', 'memory_nodes', ['user_id'], unique=True, postgresql_where=sa.text("kind = 'user'"))
    op.create_table('memory_sources',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('thread_id', sa.String(), nullable=True),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('corrects_node_id', sa.String(), nullable=True),
    sa.Column('corrects_text', sa.Text(), nullable=True),
    sa.Column('learned_through', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('learned_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_sources_user', 'memory_sources', ['user_id', 'created_at'], unique=False)
    op.create_index('uq_memory_sources_thread', 'memory_sources', ['thread_id'], unique=True, postgresql_where=sa.text('thread_id IS NOT NULL'))
    op.create_table('onboarding_progress',
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('step', sa.Integer(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('basics_submitted', sa.Boolean(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('step >= 0 AND step <= 6', name='ck_onboarding_step'),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('conversation_messages',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('thread_id', sa.String(), nullable=False),
    sa.Column('author', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('timestamp', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_code', sa.String(), nullable=True),
    sa.CheckConstraint("status IN ('streaming', 'completed', 'interrupted', 'failed')", name='ck_conversation_message_status'),
    sa.ForeignKeyConstraint(['thread_id'], ['conversation_threads.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('conversation_runs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('thread_id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('user_message_id', sa.String(), nullable=False),
    sa.Column('assistant_message_id', sa.String(), nullable=False),
    sa.Column('client_request_id', sa.String(), nullable=False),
    sa.Column('request_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('retry_of_run_id', sa.String(), nullable=True),
    sa.Column('timezone_name', sa.String(), nullable=False),
    sa.Column('enabled_interfaces', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('failure_code', sa.String(), nullable=True),
    sa.Column('worker_id', sa.String(), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('event_sequence', sa.Integer(), nullable=False),
    sa.Column('context_stats', sa.JSON(), nullable=True),
    sa.Column('usage', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'canceled')", name='ck_conversation_run_status'),
    sa.ForeignKeyConstraint(['thread_id'], ['conversation_threads.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'client_request_id', name='uq_conversation_run_request')
    )
    op.create_index('ix_conversation_run_status_created', 'conversation_runs', ['status', 'created_at'], unique=False)
    op.create_index(op.f('ix_conversation_runs_thread_id'), 'conversation_runs', ['thread_id'], unique=False)
    op.create_index('uq_conversation_run_active_thread', 'conversation_runs', ['thread_id'], unique=True, postgresql_where=sa.text("status IN ('queued', 'running')"), sqlite_where=sa.text("status IN ('queued', 'running')"))
    op.create_table('interfaces_calendar_routine_windows',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('routine_id', sa.String(), nullable=False),
    sa.Column('weekdays', sa.JSON(), nullable=False),
    sa.Column('start_local_time', sa.String(), nullable=False),
    sa.Column('end_local_time', sa.String(), nullable=False),
    sa.Column('end_day_offset', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('end_day_offset IN (0, 1)', name='ck_calendar_routine_window_day_offset'),
    sa.ForeignKeyConstraint(['routine_id'], ['interfaces_calendar_routines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_calendar_routine_window_routine', 'interfaces_calendar_routine_windows', ['routine_id'], unique=False)
    op.create_table('memory_changes',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('source_id', sa.String(), nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('op', sa.String(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('node_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['node_id'], ['memory_nodes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['memory_sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_changes_source', 'memory_changes', ['source_id', 'created_at'], unique=False)
    op.create_table('memory_edges',
    sa.Column('child_id', sa.String(), nullable=False),
    sa.Column('parent_id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['child_id'], ['memory_nodes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['memory_nodes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('child_id', 'parent_id')
    )
    op.create_index('ix_memory_edges_parent', 'memory_edges', ['parent_id'], unique=False)
    op.create_index('ix_memory_edges_user', 'memory_edges', ['user_id'], unique=False)
    op.create_table('memory_exclusions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('source_id', sa.String(), nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['memory_sources.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_exclusions_source', 'memory_exclusions', ['source_id'], unique=False)
    op.create_table('memory_node_evidence',
    sa.Column('node_id', sa.String(), nullable=False),
    sa.Column('source_id', sa.String(), nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('thread_id', sa.String(), nullable=True),
    sa.Column('said_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['node_id'], ['memory_nodes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['memory_sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('node_id', 'source_id', 'message_id')
    )
    op.create_index('ix_memory_node_evidence_source', 'memory_node_evidence', ['source_id'], unique=False)
    op.create_index('ix_memory_node_evidence_thread', 'memory_node_evidence', ['thread_id'], unique=False)
    op.create_table('conversation_artifacts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('thread_id', sa.String(), nullable=False),
    sa.Column('created_by_message_id', sa.String(), nullable=True),
    sa.Column('updated_by_message_id', sa.String(), nullable=True),
    sa.Column('superseded_by_artifact_id', sa.String(), nullable=True),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('draft_payload', sa.JSON(), nullable=False),
    sa.Column('approved_payload', sa.JSON(), nullable=True),
    sa.Column('result_payload', sa.JSON(), nullable=True),
    sa.Column('missing_fields', sa.JSON(), nullable=False),
    sa.Column('uncertain_fields', sa.JSON(), nullable=False),
    sa.Column('updated_by', sa.String(), nullable=False),
    sa.Column('error_code', sa.String(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('error_retryable', sa.Boolean(), nullable=True),
    sa.Column('execution_attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_interacted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_attempted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('draft', 'ready', 'executing', 'completed', 'reviewed', 'failed', 'denied', 'superseded', 'expired')", name='ck_conversation_artifact_status'),
    sa.CheckConstraint("updated_by IN ('agent', 'user', 'system')", name='ck_conversation_artifact_updated_by'),
    sa.ForeignKeyConstraint(['created_by_message_id'], ['conversation_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['superseded_by_artifact_id'], ['conversation_artifacts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['thread_id'], ['conversation_threads.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by_message_id'], ['conversation_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['auth_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversation_artifact_thread_status', 'conversation_artifacts', ['thread_id', 'status', 'updated_at'], unique=False)
    op.create_index('ix_conversation_artifact_user_thread', 'conversation_artifacts', ['user_id', 'thread_id'], unique=False)
    op.create_table('conversation_run_events',
    sa.Column('run_id', sa.String(), nullable=False),
    sa.Column('sequence', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['conversation_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_id', 'sequence')
    )
    op.create_table('conversation_tool_receipts',
    sa.Column('run_id', sa.String(), nullable=False),
    sa.Column('tool_call_id', sa.String(), nullable=False),
    sa.Column('tool_name', sa.String(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['conversation_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_id', 'tool_call_id')
    )
    op.create_table('interfaces_calendar_occurrence_notes',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('window_id', sa.String(), nullable=False),
    sa.Column('occurrence_date', sa.Date(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['window_id'], ['interfaces_calendar_routine_windows.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_calendar_occurrence_note', 'interfaces_calendar_occurrence_notes', ['window_id', 'occurrence_date'], unique=True)
    op.create_table('interfaces_calendar_routine_exceptions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('window_id', sa.String(), nullable=False),
    sa.Column('occurrence_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('operation', sa.String(), nullable=True),
    sa.Column('margin_minutes', sa.Integer(), nullable=False),
    sa.Column('start_offset_minutes', sa.Integer(), nullable=True),
    sa.Column('end_offset_minutes', sa.Integer(), nullable=True),
    sa.Column('event_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("source IN ('event', 'direct')", name='ck_calendar_routine_exception_source'),
    sa.ForeignKeyConstraint(['event_id'], ['interfaces_calendar_events.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['window_id'], ['interfaces_calendar_routine_windows.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_calendar_routine_exception_window_date', 'interfaces_calendar_routine_exceptions', ['window_id', 'occurrence_date'], unique=False)
    op.create_index('uq_calendar_routine_exception_event', 'interfaces_calendar_routine_exceptions', ['window_id', 'occurrence_date', 'event_id'], unique=True, postgresql_where=sa.text('event_id IS NOT NULL'))
    op.create_index('uq_calendar_routine_exception_source', 'interfaces_calendar_routine_exceptions', ['window_id', 'occurrence_date', 'source'], unique=True, postgresql_where=sa.text('event_id IS NULL'))
    op.create_table('conversation_artifact_execution_attempts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('artifact_id', sa.String(), nullable=False),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('error_code', sa.String(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('retryable', sa.Boolean(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('executing', 'completed', 'failed')", name='ck_conversation_artifact_attempt_status'),
    sa.ForeignKeyConstraint(['artifact_id'], ['conversation_artifacts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('artifact_id', 'attempt_number', name='uq_conversation_artifact_attempt')
    )
    op.create_table('conversation_artifact_revisions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('artifact_id', sa.String(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('missing_fields', sa.JSON(), nullable=False),
    sa.Column('uncertain_fields', sa.JSON(), nullable=False),
    sa.Column('changed_by', sa.String(), nullable=False),
    sa.Column('source_message_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['artifact_id'], ['conversation_artifacts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_message_id'], ['conversation_messages.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('artifact_id', 'revision', name='uq_conversation_artifact_revision')
    )
    op.create_table('conversation_message_artifact_events',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('artifact_id', sa.String(), nullable=False),
    sa.Column('event', sa.String(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=True),
    sa.Column('text_offset', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("event IN ('created', 'updated', 'superseded')", name='ck_message_artifact_event'),
    sa.ForeignKeyConstraint(['artifact_id'], ['conversation_artifacts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['message_id'], ['conversation_messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_message_artifact_event_message', 'conversation_message_artifact_events', ['message_id', 'created_at'], unique=False)
    op.execute(_TOUCH_ROUTINE)
    op.execute(
        "CREATE TRIGGER trg_calendar_routine_windows_touch_routine "
        "AFTER INSERT OR UPDATE OR DELETE ON interfaces_calendar_routine_windows "
        "FOR EACH ROW EXECUTE FUNCTION touch_calendar_routine_from_window()"
    )


def downgrade() -> None:
    raise RuntimeError("The baseline cannot be downgraded; drop the schema and re-create it instead.")
