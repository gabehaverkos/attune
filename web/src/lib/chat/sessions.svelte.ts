import type { ChatThread, ChatThreadPage } from '#lib/chat/types.ts';
import type { ChatController } from '#lib/chat/controller.svelte.ts';
import { ApiError, apiRequest, cachedFetch, invalidateCache } from '#lib/core/api.ts';

// === Links in from memory ==============================================================

/**
 * How many links from memory to a message have been followed. A link followed
 * again has the same address as before, so the count is what says to land on
 * its message once more.
 */
export const sourceLinks = $state({ followed: 0 });

// === Grouping ==========================================================================

/** Sessions are named, and grouped by day, from when they began, in the user's own time. */

export interface SessionGroup {
	key: string;
	label: string;
	threads: ChatThread[];
}

/** When a session that begins now began, as the list records it. */
function startedNow(): string {
	return new Date().toISOString();
}

export function sessionLabel(createdAt: string): string {
	return new Date(createdAt).toLocaleString('en-US', {
		month: 'short',
		day: '2-digit',
		hour: '2-digit',
		minute: '2-digit'
	});
}

function dayKey(date: Date): string {
	return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function dayLabel(date: Date): string {
	const today = new Date();
	const startOfDay = (value: Date) =>
		new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
	const diffDays = Math.round((startOfDay(today) - startOfDay(date)) / 86_400_000);
	if (diffDays === 0) return 'Today';
	if (diffDays === 1) return 'Yesterday';
	const sameYear = date.getFullYear() === today.getFullYear();
	return date.toLocaleDateString('en-US', {
		weekday: 'long',
		month: 'long',
		day: 'numeric',
		year: sameYear ? undefined : 'numeric'
	});
}

/** Most recent day first; threads within a day keep the order they arrived in, newest first. */
export function groupSessions(threads: ChatThread[]): SessionGroup[] {
	const groups = new Map<string, SessionGroup & { order: number }>();
	for (const thread of threads) {
		const started = new Date(thread.created_at);
		const key = dayKey(started);
		let group = groups.get(key);
		if (!group) {
			group = { key, label: dayLabel(started), order: started.getTime(), threads: [] };
			groups.set(key, group);
		}
		group.order = Math.max(group.order, started.getTime());
		group.threads.push(thread);
	}
	return Array.from(groups.values())
		.sort((a, b) => b.order - a.order)
		.map(({ key, label, threads }) => ({ key, label, threads }));
}

// === Sessions ==========================================================================

/**
 * The user's sessions: the list, and moving between them — selecting one,
 * handing off to a new one, archiving and deleting.
 */

export interface SessionsOptions {
	chat: ChatController;
	/** Loads a fresh snapshot of a session. */
	loadThread: (threadId: string) => Promise<ChatThreadPage>;
	/** Puts a session's snapshot on screen, as a deliberate switch. */
	show: (page: ChatThreadPage) => void;
	/** A session is gone for good: drop anything held for it. */
	forget: (threadId: string) => void;
}

export class Sessions {
	threads = $state<ChatThread[]>([]);
	open = $state(false);
	loading = $state(false);
	newPending = $state(false);

	#options: SessionsOptions;
	// Reused when the same handoff is retried, so a repeat cannot create a second session.
	#newRequest: { previousThreadId: string; requestId: string } | null = null;

	constructor(options: SessionsOptions) {
		this.#options = options;
	}

	get #chat() {
		return this.#options.chat;
	}

	get canStartNew(): boolean {
		return (
			!this.newPending && this.#chat.threadId !== 'draft' && this.#chat.view.messages.length > 0
		);
	}

	#show(page: ChatThreadPage) {
		this.threads = page.threads;
		this.#options.show(page);
	}

	#leaveForDraft() {
		this.#options.show({
			threads: this.threads,
			active_thread_id: 'draft',
			thread: [],
			artifacts: [],
			active_run: null
		});
	}

	/** Put the view back on a session after an optimistic change the server refused. */
	async #restore(threadId: string) {
		if (threadId === 'draft') return;
		try {
			this.#show(await this.#options.loadThread(threadId));
		} catch {
			// The next navigation reloads it.
		}
	}

	/** A draft became a real session: list it until the server's list arrives. */
	accepted(threadId: string) {
		if (this.threads.some((thread) => thread.id === threadId)) return;
		this.threads = [
			{ id: threadId, created_at: startedNow() },
			...this.threads.filter((thread) => thread.id !== 'draft')
		];
	}

	async refresh() {
		try {
			this.threads = (await apiRequest<ChatThreadPage>('/api/chat/thread?thread_id=draft')).threads;
		} catch {
			// The local list stays usable; the next navigation reloads it.
		}
	}

	async select(threadId: string) {
		if (this.loading) return;
		this.loading = true;
		try {
			// Load before switching so a failed switch leaves the current view intact.
			this.#show(await this.#options.loadThread(threadId));
		} catch (err) {
			console.error('Error switching thread:', err);
		} finally {
			this.loading = false;
		}
	}

	/**
	 * Hands the current session off to a fresh one without interrupting it: a
	 * running response finishes in its own session, which stays reachable here.
	 */
	async startNew() {
		if (!this.canStartNew) return;
		const previousThreadId = this.#chat.threadId;
		if (this.#newRequest?.previousThreadId !== previousThreadId) {
			this.#newRequest = { previousThreadId, requestId: crypto.randomUUID() };
		}
		this.newPending = true;
		try {
			const page = await apiRequest<ChatThreadPage>('/api/chat/sessions', {
				method: 'POST',
				body: { previous_thread_id: previousThreadId, request_id: this.#newRequest.requestId }
			});
			this.#newRequest = null;
			invalidateCache('/api/chat');
			if (this.#chat.threadId !== previousThreadId) {
				// The user moved on while the request was in flight; don't pull them away.
				this.threads = page.threads;
				return;
			}
			this.#show(page);
			this.open = false;
		} catch (error) {
			// A rejected request will not succeed on retry; a network failure might.
			if (error instanceof ApiError) this.#newRequest = null;
			alert(
				error instanceof ApiError
					? error.message
					: 'Unable to start a new session. Please try again.'
			);
		} finally {
			this.newPending = false;
		}
	}

	/**
	 * Archiving only moves a session out of the list. Nothing it taught memory is
	 * touched, so there is nothing to warn about and nothing to confirm — which is
	 * the whole difference between this and deleting.
	 */
	async archive(threadId: string, archived: boolean) {
		const previousThreads = this.threads;
		const previousThreadId = this.#chat.threadId;
		this.threads = this.threads.map((t) => (t.id === threadId ? { ...t, archived } : t));
		// Putting a session away is a way of finishing with it, so leave it for a fresh
		// one. Any response still running keeps running: it is the server's, not this
		// view's, and the session stays reachable behind the archived toggle.
		const leaving = archived && threadId === previousThreadId;
		if (leaving) this.#leaveForDraft();
		try {
			const action = archived ? 'archive' : 'unarchive';
			const response = await fetch(`/api/chat/thread/${encodeURIComponent(threadId)}/${action}`, {
				method: 'POST'
			});
			if (!response.ok) throw new Error('API server failed');
			invalidateCache('/api/chat');
		} catch (err) {
			console.error('Error archiving thread:', err);
			this.threads = previousThreads;
			if (leaving) await this.#restore(previousThreadId);
			alert('Unable to update this session. Please try again.');
		}
	}

	async delete(threadId: string) {
		const wasActive = this.#chat.threadId === threadId;
		if (wasActive && this.#chat.busy) {
			alert('Wait for the assistant to finish responding before deleting this session.');
			return;
		}
		// Deleting retracts what the session taught, and it cannot be undone, so the
		// warning says both. Archive is the way out for anyone who only wanted the
		// session off the list.
		if (
			!confirm(
				'Delete this session?\n\nIts messages are erased, and what it taught memory is ' +
					'taken back out — except for anything another session also established. This ' +
					'cannot be undone. To just clear it from the list, archive it instead.'
			)
		) {
			return;
		}

		const previousThreads = this.threads;
		this.threads = this.threads.filter((t) => t.id !== threadId);
		if (wasActive) this.#leaveForDraft();

		try {
			const response = await fetch(`/api/chat/thread/${threadId}`, { method: 'DELETE' });
			if (!response.ok) {
				throw new Error(
					response.status === 409
						? 'Wait for the assistant to finish responding before deleting this session.'
						: 'API server failed'
				);
			}
			this.#options.forget(threadId);
			invalidateCache('/api/chat');
			cachedFetch<ChatThreadPage>('/api/chat/thread?thread_id=draft', fetch).catch(() => {});
		} catch (err) {
			console.error('Error deleting thread:', err);
			this.threads = previousThreads;
			if (wasActive) await this.#restore(threadId);
			alert(
				err instanceof Error ? err.message : 'Unable to delete this session. Please try again.'
			);
		}
	}
}
