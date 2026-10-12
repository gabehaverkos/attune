/**
 * The chat view as a pure function of server snapshots and run events.
 *
 * A snapshot and its cursor arrive together; every event after the cursor is
 * applied exactly once, in order, whether it came from a fresh send or a
 * reconnect. Nothing here knows about fetch, timers, or components.
 */
import type { ActiveRun, Artifact, ChatThreadMessage, ChatThreadPage, RunEvent } from './types';

interface RunView {
	id: string;
	assistantMessageId: string;
	/** Highest event sequence already reflected in this view. */
	cursor: number;
	/** What the run is doing now; null once reply text is arriving. */
	activity: string | null;
	cancelRequested: boolean;
	/**
	 * The cards the reply has paused on until the user decides them; empty while it
	 * is working. Deciding them sends it on in the same reply.
	 */
	held: string[];
}

export interface ChatViewState {
	messages: ChatThreadMessage[];
	artifacts: Artifact[];
	run: RunView | null;
}

export type RunOutcome = 'completed' | 'failed' | 'canceled';

export const QUEUED_LABEL = 'Waiting to start…';

export const emptyView = (): ChatViewState => ({ messages: [], artifacts: [], run: null });

function runView(run: ActiveRun): RunView {
	return {
		id: run.id,
		assistantMessageId: run.assistant_message_id,
		cursor: run.cursor,
		activity: run.activity ?? (run.status === 'queued' ? QUEUED_LABEL : null),
		cancelRequested: run.cancel_requested ?? false,
		held: run.held_artifact_ids ?? []
	};
}

export function viewFromSnapshot(page: ChatThreadPage): ChatViewState {
	return {
		messages: page.thread,
		artifacts: page.artifacts,
		run: page.active_run ? runView(page.active_run) : null
	};
}

export function outcomeOf(event: RunEvent): RunOutcome | null {
	if (event.type === 'run.completed') return 'completed';
	if (event.type === 'run.failed') return 'failed';
	if (event.type === 'run.canceled') return 'canceled';
	return null;
}

function updateMessage(
	messages: ChatThreadMessage[],
	id: string,
	change: (message: ChatThreadMessage) => ChatThreadMessage
): ChatThreadMessage[] {
	return messages.map((message) => (message.id === id ? change(message) : message));
}

function upsertArtifact(artifacts: Artifact[], artifact: Artifact): Artifact[] {
	return artifacts.some((item) => item.id === artifact.id)
		? artifacts.map((item) => (item.id === artifact.id ? artifact : item))
		: [artifact, ...artifacts];
}

/** Settle the reply to its canonical saved form. */
function settle(state: ChatViewState, message: ChatThreadMessage): ChatViewState {
	const known = state.messages.some((item) => item.id === message.id);
	return {
		...state,
		messages: known
			? updateMessage(state.messages, message.id, () => message)
			: [...state.messages, message],
		run: null
	};
}

function applyRunEvent(state: ChatViewState, runId: string, event: RunEvent): ChatViewState {
	const run = state.run;
	if (!run || run.id !== runId || event.seq <= run.cursor) return state;
	const next: ChatViewState = { ...state, run: { ...run, cursor: event.seq } };
	const current = next.run as RunView;

	switch (event.type) {
		case 'run.accepted':
			if (!next.messages.some((message) => message.id === event.assistant_message_id)) {
				next.messages = [
					...next.messages,
					{
						id: event.assistant_message_id,
						thread_id: event.thread_id,
						author: 'assistant',
						content: '',
						timestamp: new Date().toISOString(),
						status: 'streaming',
						run_id: event.run_id
					}
				];
			}
			return next;
		case 'run.activity':
			current.activity = event.label;
			current.held = [];
			return next;
		case 'run.held':
			current.activity = event.label;
			current.held = event.artifact_ids;
			return next;
		case 'text.delta':
			current.activity = null;
			next.messages = updateMessage(next.messages, event.message_id, (message) => ({
				...message,
				content: message.content + event.delta
			}));
			return next;
		case 'artifact.committed':
			next.artifacts = upsertArtifact(next.artifacts, event.artifact);
			next.messages = updateMessage(next.messages, event.message_id, (message) => ({
				...message,
				artifact_events: [
					...(message.artifact_events ?? []).filter(
						(item) => item.artifact_id !== event.artifact.id
					),
					{
						artifact_id: event.artifact.id,
						event: event.action,
						snapshot: event.artifact,
						text_offset: event.text_offset
					}
				]
			}));
			return next;
		case 'run.cancel_requested':
			current.cancelRequested = true;
			return next;
		case 'run.completed':
			return settle(next, event.message);
		case 'run.failed':
		case 'run.canceled':
			return settle(next, event.partial);
	}
}

export function applyRunEvents(
	state: ChatViewState,
	runId: string,
	events: RunEvent[]
): ChatViewState {
	return events.reduce((view, event) => applyRunEvent(view, runId, event), state);
}

/** The latest assistant reply, when it ended early and nothing has replaced it. */
export function retryableRunId(state: ChatViewState): string | null {
	if (state.run) return null;
	const last = state.messages.at(-1);
	if (!last || last.author !== 'assistant' || !last.run_id) return null;
	return last.status === 'failed' || last.status === 'interrupted' ? last.run_id : null;
}

const FAILURE_TEXT: Record<string, string> = {
	response_stopped: 'Stopped.',
	budget_exhausted: 'This response hit its work limit before it finished.',
	model_unavailable: 'The assistant service could not be reached.',
	model_rate_limited: 'The assistant service is busy. Try again shortly.',
	generation_timed_out: 'This response took too long and was ended.',
	assistant_unconfigured: 'The assistant is not configured.'
};

export function failureText(message: ChatThreadMessage): string {
	if (message.status === 'interrupted') {
		return message.failure_code === 'response_stopped'
			? 'Stopped.'
			: 'This response was interrupted.';
	}
	return (
		FAILURE_TEXT[message.failure_code ?? ''] ?? 'The assistant could not finish this response.'
	);
}
