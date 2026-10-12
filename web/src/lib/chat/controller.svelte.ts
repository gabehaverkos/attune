import type {
	ChatThreadMessage,
	ChatThreadPage,
	RunEvent,
	RunStreamLine,
	TurnAccepted
} from '#lib/chat/types.ts';
import { captureFlightOrigin, type FlightOrigin } from '#styles/motion.ts';
import { ApiError, apiRequest } from '#lib/core/api.ts';
import {
	applyRunEvents,
	type ChatViewState,
	emptyView,
	outcomeOf,
	QUEUED_LABEL,
	type RunOutcome,
	viewFromSnapshot
} from '#lib/chat/reducer.ts';

// === The event stream ==================================================================

class NdjsonStreamError extends Error {
	constructor(
		message: string,
		readonly line?: string
	) {
		super(message);
		this.name = 'NdjsonStreamError';
	}
}

const isString = (value: unknown) => typeof value === 'string';
const isObject = (value: unknown) => !!value && typeof value === 'object';

// Required fields per line type; anything else about a line is left to the reducer.
const SHAPES: Record<string, (record: Record<string, unknown>) => boolean> = {
	'run.accepted': (r) => isString(r.run_id) && isString(r.assistant_message_id),
	'run.activity': (r) => isString(r.kind) && isString(r.label),
	'run.held': (r) => Array.isArray(r.artifact_ids) && isString(r.label),
	'text.delta': (r) => isString(r.message_id) && isString(r.delta),
	'artifact.committed': (r) => isString(r.message_id) && isObject(r.artifact),
	'run.cancel_requested': () => true,
	'run.completed': (r) => isObject(r.message),
	'run.failed': (r) => isString(r.code) && isString(r.message) && isObject(r.partial),
	'run.canceled': (r) => isObject(r.partial),
	'stream.heartbeat': () => true
};

function parseLine(line: string): RunStreamLine {
	let value: unknown;
	try {
		value = JSON.parse(line);
	} catch {
		throw new NdjsonStreamError('The server returned a malformed stream event.', line);
	}
	if (!isObject(value)) {
		throw new NdjsonStreamError('The server returned an invalid stream event.', line);
	}
	const record = value as Record<string, unknown>;
	if (record.type === 'stream.reset') return value as RunStreamLine;
	const shape = typeof record.type === 'string' ? SHAPES[record.type] : undefined;
	if (!shape || typeof record.seq !== 'number' || !shape(record)) {
		throw new NdjsonStreamError('The server returned an invalid stream event.', line);
	}
	return value as RunStreamLine;
}

async function* parseNdjsonStream(
	stream: ReadableStream<Uint8Array>
): AsyncGenerator<RunStreamLine> {
	const reader = stream.getReader();
	const decoder = new TextDecoder();
	let buffered = '';
	try {
		while (true) {
			const { done, value } = await reader.read();
			buffered += decoder.decode(value, { stream: !done });
			const lines = buffered.split('\n');
			buffered = lines.pop() ?? '';
			for (const rawLine of lines) {
				const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
				if (line.trim()) yield parseLine(line);
			}
			if (done) break;
		}
		if (buffered.trim()) yield parseLine(buffered);
	} finally {
		reader.releaseLock();
	}
}

// === Messages waiting to be sent =======================================================

/**
 * Messages typed while the assistant is mid-turn wait here, oldest first, and go
 * out once that reply finishes. Each queue belongs to its session and survives
 * navigation; nothing reaches the server until it is actually sent.
 */

export type QueuedMessage = { id: string; content: string };

export class MessageQueue {
	#byThread = $state<Record<string, QueuedMessage[]>>({});
	// A session whose last reply ended early holds its backlog until the user releases it.
	#paused = $state<Record<string, boolean>>({});
	#flushing = false;

	items(threadId: string): QueuedMessage[] {
		return this.#byThread[threadId] ?? [];
	}

	paused(threadId: string): boolean {
		return this.#paused[threadId] ?? false;
	}

	set(threadId: string, items: QueuedMessage[]) {
		const rest = Object.fromEntries(
			Object.entries(this.#byThread).filter(([key]) => key !== threadId)
		);
		this.#byThread = items.length ? { ...rest, [threadId]: items } : rest;
		if (!items.length && this.#paused[threadId]) {
			this.#paused = Object.fromEntries(
				Object.entries(this.#paused).filter(([key]) => key !== threadId)
			);
		}
	}

	/** A draft became a real session: its backlog goes with it. */
	move(fromThreadId: string, toThreadId: string) {
		const moved = this.#byThread[fromThreadId];
		if (fromThreadId === toThreadId || !moved) return;
		this.set(fromThreadId, []);
		this.set(toThreadId, moved);
	}

	add(threadId: string, content: string) {
		const items = this.items(threadId);
		this.set(threadId, [...items, { id: `queued-${Date.now()}-${items.length}`, content }]);
	}

	cancel(threadId: string, id: string) {
		this.set(
			threadId,
			this.items(threadId).filter((item) => item.id !== id)
		);
	}

	pause(threadId: string) {
		this.#paused = { ...this.#paused, [threadId]: true };
	}

	resume(threadId: string) {
		this.#paused = { ...this.#paused, [threadId]: false };
	}

	/**
	 * Sends the backlog one turn at a time. Each waits for the reply before it to
	 * finish; a reply that ends early pauses the rest.
	 */
	async flush(
		threadId: string,
		canSend: () => boolean,
		send: (content: string, origin?: FlightOrigin) => Promise<void>
	) {
		if (this.#flushing) return;
		this.#flushing = true;
		try {
			const [next, ...rest] = this.items(threadId);
			if (!next || !canSend() || this.paused(threadId)) return;
			const origin = captureFlightOrigin(
				document.querySelector(`[data-queued-message="${CSS.escape(next.id)}"]`)
			);
			this.set(threadId, rest);
			await send(next.content, origin);
		} finally {
			this.#flushing = false;
		}
	}
}

// === The controller ====================================================================

/**
 * Owns the server lifecycle of one chat view: sending, observing, stopping, retrying.
 *
 * Components render `view` and call methods; they never infer run state. Leaving
 * a session only detaches this viewer. Stopping is a server command. Every
 * stream, first or resumed, goes through the same reducer from a cursor.
 */

export interface ChatControllerOptions {
	/** Loads a fresh snapshot, used when a stream can no longer resume. */
	loadThread: (threadId: string) => Promise<ChatThreadPage>;
	/** A draft became a real session, or a send landed in one. */
	onThreadAccepted?: (previousThreadId: string, threadId: string) => void;
	/** A run this view was following reached its outcome. */
	onRunSettled?: (outcome: RunOutcome, threadId: string) => void;
	fetch?: typeof fetch;
}

type PendingSend = { requestId: string; content: string; threadId: string };

const SEND_ATTEMPTS = 3;
const MAX_RECONNECT_DELAY_MS = 5000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function nextFrame(callback: () => void): () => void {
	if (typeof requestAnimationFrame === 'function' && document.visibilityState === 'visible') {
		const handle = requestAnimationFrame(callback);
		return () => cancelAnimationFrame(handle);
	}
	const handle = setTimeout(callback, 16);
	return () => clearTimeout(handle);
}

export class ChatController {
	view = $state<ChatViewState>(emptyView());
	threadId = $state('draft');
	/** A message is on its way to the server and not yet accepted. */
	sending = $state(false);
	/**
	 * The reply has paused on cards for the user to decide. The session is open
	 * meanwhile: deciding them carries the reply on, and a new message moves past it.
	 */
	readonly held = $derived((this.view.run?.held.length ?? 0) > 0);
	readonly busy = $derived(this.sending || (this.view.run !== null && !this.held));

	#options: ChatControllerOptions;
	#viewer: AbortController | null = null;
	#pending: RunEvent[] = [];
	#cancelFrame: (() => void) | null = null;
	// Bumped whenever the view switches sessions, so late responses cannot land in the wrong one.
	#generation = 0;
	#unsent = new Map<string, PendingSend>();

	constructor(options: ChatControllerOptions) {
		this.#options = options;
	}

	get #fetch() {
		return this.#options.fetch ?? fetch;
	}

	/** Show a snapshot and follow its active run, if it has one. */
	adopt(page: ChatThreadPage) {
		this.detach();
		this.#generation += 1;
		this.threadId = page.active_thread_id;
		this.view = viewFromSnapshot(page);
		this.sending = false;
		if (this.view.run) void this.#observe(this.view.run.id);
	}

	/** Stop watching. The run, if any, carries on without this view. */
	detach() {
		this.#viewer?.abort();
		this.#viewer = null;
		this.#flush();
	}

	async send(content: string): Promise<boolean> {
		const requestId = crypto.randomUUID();
		const localId = `local-${requestId}`;
		const pending = { requestId, content, threadId: this.threadId };
		this.view = {
			...this.view,
			messages: [
				...this.view.messages,
				{
					id: localId,
					client_key: localId,
					thread_id: this.threadId,
					author: 'user',
					content,
					timestamp: new Date().toISOString(),
					status: 'completed'
				}
			]
		};
		return this.#submit(localId, pending);
	}

	/** Send a message again under its original request ID, so it cannot be doubled. */
	async resend(localId: string): Promise<boolean> {
		const pending = this.#unsent.get(localId);
		if (!pending || this.busy) return false;
		this.#setMessage(localId, (message) => ({
			...message,
			send_failed: false,
			send_error: undefined
		}));
		return this.#submit(localId, pending);
	}

	discard(localId: string) {
		this.#unsent.delete(localId);
		this.view = { ...this.view, messages: this.view.messages.filter((m) => m.id !== localId) };
	}

	async retry(runId: string): Promise<boolean> {
		if (this.busy) return false;
		const generation = this.#generation;
		this.sending = true;
		try {
			const accepted = await apiRequest<TurnAccepted>(
				`/api/chat/runs/${encodeURIComponent(runId)}/retry`,
				{ method: 'POST', body: { client_request_id: crypto.randomUUID() } },
				this.#fetch
			);
			if (generation === this.#generation) this.#follow(accepted);
			return true;
		} catch {
			return false;
		} finally {
			if (generation === this.#generation) this.sending = false;
		}
	}

	async cancel() {
		const run = this.view.run;
		if (!run || run.cancelRequested) return;
		this.view = { ...this.view, run: { ...run, cancelRequested: true } };
		try {
			await apiRequest(
				`/api/chat/runs/${encodeURIComponent(run.id)}/cancel`,
				{ method: 'POST' },
				this.#fetch
			);
		} catch {
			if (this.view.run?.id === run.id) {
				this.view = { ...this.view, run: { ...this.view.run, cancelRequested: false } };
			}
		}
	}

	async #submit(localId: string, pending: PendingSend): Promise<boolean> {
		const generation = this.#generation;
		this.#unsent.set(localId, pending);
		this.sending = true;
		let accepted: TurnAccepted | null = null;
		let failure = 'This message could not be sent.';
		for (let attempt = 0; attempt < SEND_ATTEMPTS && !accepted; attempt += 1) {
			try {
				accepted = await apiRequest<TurnAccepted>(
					'/api/chat/turns',
					{
						method: 'POST',
						body: {
							thread_id: pending.threadId,
							content: pending.content,
							client_request_id: pending.requestId
						}
					},
					this.#fetch
				);
			} catch (error) {
				// A refusal will not change on retry; a lost connection might.
				if (error instanceof ApiError && error.status < 500) {
					failure = error.message;
					break;
				}
				if (attempt < SEND_ATTEMPTS - 1) await sleep(400 * 2 ** attempt);
			}
		}
		if (generation !== this.#generation) {
			if (accepted) this.#options.onThreadAccepted?.(pending.threadId, accepted.thread_id);
			return accepted !== null;
		}
		this.sending = false;
		if (!accepted) {
			this.#setMessage(localId, (message) => ({
				...message,
				send_failed: true,
				send_error: failure
			}));
			return false;
		}
		this.#unsent.delete(localId);
		const previousThreadId = this.threadId;
		this.threadId = accepted.thread_id;
		this.#setMessage(localId, (message) => ({
			...message,
			id: accepted.user_message_id,
			thread_id: accepted.thread_id
		}));
		this.#options.onThreadAccepted?.(previousThreadId, accepted.thread_id);
		this.#follow(accepted);
		return true;
	}

	#follow(accepted: TurnAccepted) {
		// A reply that was waiting on a card ends as it stands once a new message is sent.
		const paused = this.view.run?.assistantMessageId;
		this.view = {
			...this.view,
			messages: [
				...this.view.messages
					.filter((message) => message.id !== accepted.assistant_message_id)
					.map((message) =>
						message.id === paused && message.status === 'streaming'
							? { ...message, status: 'completed' as const }
							: message
					),
				{
					id: accepted.assistant_message_id,
					thread_id: accepted.thread_id,
					author: 'assistant',
					content: '',
					timestamp: new Date().toISOString(),
					status: 'streaming',
					run_id: accepted.run_id
				}
			],
			run: {
				id: accepted.run_id,
				assistantMessageId: accepted.assistant_message_id,
				cursor: accepted.cursor,
				activity: QUEUED_LABEL,
				cancelRequested: false,
				held: []
			}
		};
		void this.#observe(accepted.run_id);
	}

	#setMessage(id: string, change: (message: ChatThreadMessage) => ChatThreadMessage) {
		this.view = {
			...this.view,
			messages: this.view.messages.map((message) => (message.id === id ? change(message) : message))
		};
	}

	async #observe(runId: string) {
		this.detach();
		const viewer = new AbortController();
		this.#viewer = viewer;
		const following = () => !viewer.signal.aborted && this.view.run?.id === runId;
		let failures = 0;
		while (following()) {
			try {
				const cursor = this.#cursor(runId);
				const response = await this.#fetch(
					`/api/chat/runs/${encodeURIComponent(runId)}/events?after=${cursor}`,
					{ headers: { Accept: 'application/x-ndjson' }, signal: viewer.signal }
				);
				if (response.status === 404) return void (await this.#resync(viewer));
				if (!response.ok || !response.body)
					throw new Error(`Stream unavailable (${response.status})`);
				for await (const line of parseNdjsonStream(response.body)) {
					if (!following()) return;
					failures = 0;
					if (line.type === 'stream.reset') return void (await this.#resync(viewer));
					if (line.type === 'stream.heartbeat') continue;
					const outcome = outcomeOf(line);
					this.#pending.push(line);
					if (outcome) {
						this.#flush(runId);
						this.#options.onRunSettled?.(outcome, this.threadId);
						return;
					}
					this.#schedule(runId);
				}
			} catch {
				if (viewer.signal.aborted) return;
			}
			// The stream ended without an outcome: resume from what was applied.
			failures += 1;
			await sleep(Math.min(250 * 2 ** failures, MAX_RECONNECT_DELAY_MS));
		}
	}

	#cursor(runId: string): number {
		this.#flush(runId);
		return this.view.run?.id === runId ? this.view.run.cursor : 0;
	}

	#schedule(runId: string) {
		if (this.#cancelFrame) return;
		this.#cancelFrame = nextFrame(() => {
			this.#cancelFrame = null;
			this.#flush(runId);
		});
	}

	/** Apply everything received so far in one update, at most once per frame. */
	#flush(runId?: string) {
		this.#cancelFrame?.();
		this.#cancelFrame = null;
		if (!this.#pending.length) return;
		const events = this.#pending;
		this.#pending = [];
		const target = runId ?? this.view.run?.id;
		if (target) this.view = applyRunEvents(this.view, target, events);
	}

	async #resync(viewer: AbortController) {
		if (viewer.signal.aborted) return;
		const generation = this.#generation;
		try {
			const page = await this.#options.loadThread(this.threadId);
			if (generation !== this.#generation || viewer.signal.aborted) return;
			const wasRunning = this.view.run;
			this.adopt(page);
			if (wasRunning && !this.view.run) {
				const reply = page.thread.find((message) => message.id === wasRunning.assistantMessageId);
				const outcome: RunOutcome =
					reply?.status === 'completed'
						? 'completed'
						: reply?.status === 'interrupted'
							? 'canceled'
							: 'failed';
				this.#options.onRunSettled?.(outcome, this.threadId);
			}
		} catch {
			// The next navigation or send reloads the session.
		}
	}
}
