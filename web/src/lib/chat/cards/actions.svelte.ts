/**
 * What the user does to the artifacts in the visible session: edit a draft,
 * accept, retry or deny it. One action at a time, and none while the
 * assistant is responding.
 */
import type { ChatController } from '#lib/chat/controller.svelte.ts';
import type { Artifact, ChatThreadPage } from '#lib/chat/types.ts';
import { ApiError, apiRequest, hydrateCache } from '#lib/core/api.ts';
import type { InterfacesPayload } from '#lib/shell/interfaces.ts';
import { artifactData, artifactView } from '#lib/chat/cards/registry.ts';

export interface ArtifactActionsOptions {
	chat: ChatController;
	/** Loads a fresh snapshot of a session. */
	loadThread: (threadId: string) => Promise<ChatThreadPage>;
	/** Which interfaces are on, after an artifact turned one on or off. */
	onInterfaces?: (payload: InterfacesPayload) => void;
	/** The data an applied artifact changed. */
	onChanged?: (urls: readonly string[]) => void;
}

function errorMessage(error: unknown): string {
	if (
		error instanceof ApiError &&
		error.detail &&
		typeof error.detail === 'object' &&
		'message' in error.detail
	) {
		const message = (error.detail as { message?: unknown }).message;
		if (typeof message === 'string') return message;
	}
	return error instanceof Error ? error.message : 'Unable to update this artifact.';
}

export class ArtifactActions {
	pendingId = $state<string | null>(null);
	error = $state('');

	#options: ArtifactActionsOptions;

	constructor(options: ArtifactActionsOptions) {
		this.#options = options;
	}

	get #chat() {
		return this.#options.chat;
	}

	clearError() {
		this.error = '';
	}

	#replace(updated: Artifact) {
		this.#chat.view = {
			...this.#chat.view,
			artifacts: this.#chat.view.artifacts.map((artifact) =>
				artifact.id === updated.id ? updated : artifact
			)
		};
	}

	/** Every card in the session as the server has it, and where each reply shows them. */
	async #refresh() {
		const threadId = this.#chat.threadId;
		if (threadId === 'draft') return;
		const refreshed = await this.#options.loadThread(threadId);
		const events = Object.fromEntries(
			refreshed.thread.map((message) => [message.id, message.artifact_events])
		);
		this.#chat.view = {
			...this.#chat.view,
			artifacts: refreshed.artifacts,
			messages: this.#chat.view.messages.map((message) =>
				message.id in events
					? { ...message, artifact_events: events[message.id] ?? undefined }
					: message
			)
		};
	}

	/** Runs one action, holding the others off until it settles. */
	async #run(
		id: string,
		action: () => Promise<Artifact>,
		recover?: (error: unknown) => Promise<void>
	) {
		if (this.pendingId || this.#chat.busy) return;
		this.pendingId = id;
		this.error = '';
		try {
			this.#replace(await action());
		} catch (error) {
			if (recover) await recover(error);
			else this.error = errorMessage(error);
		} finally {
			this.pendingId = null;
		}
	}

	update(id: string, draftPayload: Record<string, unknown>) {
		const artifact = this.#chat.view.artifacts.find((item) => item.id === id);
		if (!artifact) return;
		return this.#run(
			id,
			() =>
				apiRequest<Artifact>(`/api/chat/artifacts/${id}`, {
					method: 'PATCH',
					body: { expected_revision: artifact.revision, draft_payload: draftPayload },
					invalidate: ['/api/chat/thread']
				}),
			async (error) => {
				if (error instanceof ApiError && error.status === 409) {
					await this.#refresh();
					this.error =
						'This artifact changed while you were editing it. Review the latest version.';
				} else this.error = errorMessage(error);
			}
		);
	}

	/** Accepting shows the artifact applying at once, before the server answers. */
	accept(id: string) {
		if (this.pendingId || this.#chat.busy) return;
		this.#chat.view = {
			...this.#chat.view,
			artifacts: this.#chat.view.artifacts.map((artifact) =>
				artifact.id === id ? { ...artifact, status: 'executing' } : artifact
			)
		};
		return this.#execute(id, 'accept');
	}

	retry(id: string) {
		return this.#execute(id, 'retry');
	}

	/**
	 * Applying one card can clear the way for another that was blocked, which the
	 * server brings back as a fresh card; the session is reloaded to show it.
	 */
	#execute(id: string, action: 'accept' | 'retry') {
		const acted = this.#chat.view.artifacts.find((artifact) => artifact.id === id);
		return this.#run(
			id,
			async () => {
				const done = await apiRequest<Artifact>(`/api/chat/artifacts/${id}/${action}`, {
					method: 'POST',
					invalidate: ['/api/chat/thread', ...(acted ? artifactData(acted) : [])]
				});
				if (done.status === 'completed') {
					if (acted) this.#options.onChanged?.(artifactData(acted));
					await this.#refresh().catch(() => undefined);
					if (acted && artifactView(acted)?.changesInterfaces)
						await this.#reloadInterfaces().catch(() => undefined);
				}
				return this.#chat.view.artifacts.find((artifact) => artifact.id === id) ?? done;
			},
			async (error) => {
				await this.#refresh().catch(() => undefined);
				this.error = errorMessage(error);
			}
		);
	}

	async #reloadInterfaces() {
		const payload = await apiRequest<InterfacesPayload>('/api/interfaces');
		hydrateCache({ '/api/interfaces': payload });
		this.#options.onInterfaces?.(payload);
	}

	deny(id: string, reason: string | null) {
		return this.#run(id, () =>
			apiRequest<Artifact>(`/api/chat/artifacts/${id}/deny`, {
				method: 'POST',
				body: { reason },
				invalidate: ['/api/chat/thread']
			})
		);
	}
}
