import type { ISODateTime } from '#lib/core/api.ts';

export type ArtifactStatus =
	| 'draft'
	| 'ready'
	| 'executing'
	| 'completed'
	| 'failed'
	| 'denied'
	| 'superseded'
	| 'expired';

interface ArtifactUncertainty {
	path: string;
	reason: string;
}

interface ArtifactError {
	code: string;
	message: string;
	retryable: boolean;
}

export interface Artifact {
	id: string;
	thread_id: string;
	type: string;
	schema_version: number;
	title: string;
	description: string;
	status: ArtifactStatus;
	revision: number;
	draft_payload: Record<string, unknown>;
	approved_payload: Record<string, unknown> | null;
	result_payload: Record<string, unknown> | null;
	missing_fields: string[];
	uncertain_fields: ArtifactUncertainty[];
	updated_by: 'agent' | 'user' | 'system';
	error: ArtifactError | null;
	execution_attempts: number;
	superseded_by_artifact_id: string | null;
	created_at: string;
	updated_at: string;
	approved_at: string | null;
	last_attempted_at: string | null;
	resolved_at: string | null;
	/** The user's last touch on the card; an open proposal left alone expires. */
	last_interacted_at: string;
	expires_at: string | null;
}

type MessageStatus = 'streaming' | 'completed' | 'interrupted' | 'failed';
type ArtifactMessageEventType = 'created' | 'updated' | 'superseded';

export interface ArtifactMessageEvent {
	artifact_id: string;
	event: ArtifactMessageEventType;
	snapshot?: Artifact | null;
	/** How much of the reply's text was written when the card was made; it shows there. */
	text_offset?: number | null;
}

export interface ChatThreadMessage {
	id: string;
	thread_id: string;
	author: 'user' | 'assistant';
	content: string;
	timestamp: string;
	status?: MessageStatus;
	failure_code?: string | null;
	/** The run that wrote an assistant reply; what a retry names. */
	run_id?: string | null;
	artifact_events?: ArtifactMessageEvent[];
	/** Local only: a message this tab could not get accepted, and why. */
	send_failed?: boolean;
	send_error?: string;
	/** Local only: keeps an optimistic message's identity once its saved ID arrives. */
	client_key?: string;
}

export interface ChatThread {
	id: string;
	/** When the session began; it is named and grouped by this, in the user's own time. */
	created_at: ISODateTime;
	/** Archived sessions ship with every list; the drawer's toggle decides if they show. */
	archived?: boolean;
}

export interface ActiveRun {
	id: string;
	status: 'queued' | 'running' | 'waiting';
	assistant_message_id: string;
	/** The snapshot already reflects every event up to this sequence. */
	cursor: number;
	activity?: string | null;
	cancel_requested?: boolean;
	/** While waiting: the cards it waits on the user to decide. */
	held_artifact_ids?: string[];
}

export interface ChatThreadPage {
	threads: ChatThread[];
	active_thread_id: string;
	thread: ChatThreadMessage[];
	artifacts: Artifact[];
	active_run?: ActiveRun | null;
}

export interface TurnAccepted {
	run_id: string;
	thread_id: string;
	user_message_id: string;
	assistant_message_id: string;
	thread_created: boolean;
	cursor: number;
}

type RunActivityKind = 'queued' | 'context' | 'thinking' | 'tool';

/** One entry in a run's ordered event log, as the server replays it. */
export type RunEvent =
	| {
			type: 'run.accepted';
			seq: number;
			run_id: string;
			thread_id: string;
			user_message_id: string;
			assistant_message_id: string;
	  }
	| {
			type: 'run.activity';
			seq: number;
			kind: RunActivityKind;
			label: string;
			tool_call_id?: string | null;
	  }
	| { type: 'text.delta'; seq: number; message_id: string; delta: string }
	| {
			type: 'artifact.committed';
			seq: number;
			message_id: string;
			action: ArtifactMessageEventType;
			artifact: Artifact;
			text_offset?: number | null;
	  }
	| { type: 'run.held'; seq: number; artifact_ids: string[]; label: string }
	| { type: 'run.cancel_requested'; seq: number }
	| { type: 'run.completed'; seq: number; message: ChatThreadMessage }
	| {
			type: 'run.failed';
			seq: number;
			code: string;
			message: string;
			retryable: boolean;
			partial: ChatThreadMessage;
	  }
	| { type: 'run.canceled'; seq: number; partial: ChatThreadMessage };

export type RunStreamLine =
	| RunEvent
	| { type: 'stream.reset'; reason: string }
	| { type: 'stream.heartbeat'; seq: number };
