<script lang="ts">
	import { onDestroy, tick, untrack } from 'svelte';
	import { ArtifactActions } from '#lib/chat/cards/actions.svelte.ts';
	import ArtifactDrawer from '#lib/chat/cards/ArtifactDrawer.svelte';
	import ArtifactSet from '#lib/chat/cards/ArtifactSet.svelte';
	import { type FlightOrigin, setSkipTransitions } from '#styles/motion.ts';
	import ChatComposerPanel from '#lib/chat/ChatComposerPanel.svelte';
	import { ChatController, MessageQueue } from '#lib/chat/controller.svelte.ts';
	import { retryableRunId } from '#lib/chat/reducer.ts';
	import SessionList from '#lib/chat/SessionList.svelte';
	import { Sessions, sourceLinks } from '#lib/chat/sessions.svelte.ts';
	import ChatThreadPanel from '#lib/chat/ChatThreadPanel.svelte';
	import type { Artifact, ChatThreadPage } from '#lib/chat/types.ts';
	import { apiRequest, hydrateCache, invalidateCache } from '#lib/core/api.ts';
	import { useInterfacesShell } from '#lib/shell/interfaces.ts';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const interfacesShell = useInterfacesShell();
	$effect(() => {
		hydrateCache({
			[data.cacheUrl]: {
				threads: data.threads,
				active_thread_id: data.activeThreadId,
				thread: data.thread,
				artifacts: data.artifacts,
				active_run: data.activeRun
			}
		});
	});

	// Always read a session fresh: its response may have progressed in the background.
	async function loadThreadPage(threadId: string): Promise<ChatThreadPage> {
		const url = `/api/chat/thread?thread_id=${encodeURIComponent(threadId)}`;
		const page = await apiRequest<ChatThreadPage>(url);
		hydrateCache({ [url]: page });
		return page;
	}

	const queue = new MessageQueue();

	// The server lifecycle of the visible session lives in the controller; this page
	// only arranges sessions, the composer, and proposal actions around it.
	const chat = new ChatController({
		loadThread: loadThreadPage,
		onThreadAccepted(previousThreadId, threadId) {
			queue.move(previousThreadId, threadId);
			sessions.accepted(threadId);
		},
		onRunSettled(outcome, threadId) {
			invalidateCache('/api/chat');
			void sessions.refresh();
			// Queued follow-ups go out only after a reply that finished. After a stop or
			// failure they wait for the user to decide.
			if (outcome !== 'completed') queue.pause(threadId);
		}
	});
	onDestroy(() => chat.detach());

	const actions = new ArtifactActions({
		chat,
		loadThread: loadThreadPage,
		onInterfaces: (payload) => interfacesShell.update(payload),
		onChanged: (urls) => interfacesShell.changed(urls)
	});
	const sessions = new Sessions({
		chat,
		loadThread: loadThreadPage,
		show(page) {
			// An anchor belongs to the session it was linked into, not to the next one.
			anchorMessageId = null;
			actions.clearError();
			chat.adopt(page);
			void remountChatView();
		},
		forget: (threadId) => queue.set(threadId, [])
	});

	const activeThreadId = $derived(chat.threadId);
	const messages = $derived(chat.view.messages);
	const artifacts = $derived(chat.view.artifacts);
	const isTurnBlocked = $derived(chat.busy);

	/** A message's proposals, with its events kept together as one set where it made several. */
	function artifactGroups(items: Artifact[]): Artifact[][] {
		const events = items.filter((artifact) => artifact.type === 'event');
		return items.flatMap((artifact) => {
			if (artifact.type !== 'event' || events.length < 2) return [[artifact]];
			return artifact.id === events[0].id ? [events] : [];
		});
	}
	const queuedMessages = $derived(queue.items(activeThreadId));

	let outgoingMessage = $state<{ id: string; origin: FlightOrigin }>();
	// The message a memory source linked to, if this view was opened by one.
	let anchorMessageId = $state<string | null>(null);
	// Bumped on every explicit thread switch (select/new/delete) to force the message panel
	// to fully remount rather than reconcile in place. This sidesteps stuck artifact-drawer
	// out-transitions from a previous thread bleeding into the newly selected one.
	let chatViewKey = $state(0);
	// Whether the loader's data has been shown once; the first showing is no switch.
	let loaded = false;

	// Remounts the message panel without letting any artifact transitions animate,
	// so switching threads feels instant rather than showing the old thread fade out.
	async function remountChatView() {
		outgoingMessage = undefined;
		setSkipTransitions(true);
		chatViewKey += 1;
		await tick();
		setSkipTransitions(false);
	}

	// Release the backlog the moment the viewed session accepts a turn again, unless
	// the last reply ended early. The drain writes to the queue it is reading from.
	$effect(() => {
		// A reply waiting on a card waits for the card, not for what was queued before it.
		if (isTurnBlocked || chat.held || queue.paused(activeThreadId)) return;
		const threadId = activeThreadId;
		untrack(() => {
			if (queue.items(threadId).length > 0) void flushQueue(threadId);
		});
	});

	// Only loader-data changes should reset the view. Reading local state reactively
	// here would undo a session selection immediately after it was clicked.
	$effect(() => {
		const page: ChatThreadPage = {
			threads: data.threads || [],
			active_thread_id: data.activeThreadId || 'draft',
			thread: data.thread || [],
			artifacts: data.artifacts || [],
			active_run: data.activeRun ?? null
		};
		const nextAnchor = data.anchorMessageId || null;
		untrack(() => {
			// A link from memory can bring another session's data. That is a switch like
			// any other, so the panel is built again: reconciled in place, the session
			// left behind stays on screen in pieces among the one arrived at.
			const switched = loaded && chat.threadId !== page.active_thread_id;
			loaded = true;
			sessions.threads = page.threads;
			chat.adopt(page);
			anchorMessageId = nextAnchor;
			actions.clearError();
			if (switched) void remountChatView();
		});
	});

	function handleSend(content: string, origin?: FlightOrigin) {
		if (isTurnBlocked || queuedMessages.length) {
			queue.add(activeThreadId, content);
			return;
		}
		void sendNow(content, origin);
	}

	async function sendNow(content: string, origin?: FlightOrigin): Promise<boolean> {
		const sending = chat.send(content);
		// The optimistic message is in the view synchronously; animate it from the composer.
		const local = chat.view.messages.at(-1);
		outgoingMessage = origin && local?.client_key ? { id: local.client_key, origin } : undefined;
		return sending;
	}

	function flushQueue(threadId: string) {
		return queue.flush(
			threadId,
			() => !isTurnBlocked,
			async (content, origin) => {
				if (!(await sendNow(content, origin))) queue.pause(chat.threadId);
			}
		);
	}
</script>

<svelte:head>
	<title>Attune</title>
</svelte:head>

<main
	class="chat-layout app-page relative flex h-full w-full min-h-0 overflow-hidden bg-background"
>
	<SessionList {sessions} {activeThreadId} empty={messages.length === 0} />

	<!-- Chat column -->
	<div class="chat-column flex min-h-0 h-full flex-col overflow-hidden bg-background py-8">
		<!-- Message Viewport -->
		{#key chatViewKey}
			<ChatThreadPanel
				{messages}
				{outgoingMessage}
				{anchorMessageId}
				anchorKey={sourceLinks.followed}
				activity={chat.view.run?.activity ?? null}
				activeMessageId={chat.view.run?.assistantMessageId ?? null}
				retryableRunId={retryableRunId(chat.view)}
				onRetry={(runId) => void chat.retry(runId)}
				onResend={(messageId) => void chat.resend(messageId)}
				onDiscard={(messageId) => chat.discard(messageId)}
				artifactIds={artifacts.map((artifact) => artifact.id)}
			>
				{#snippet children(ids: string[])}
					<div class="w-full space-y-3" aria-label="Artifacts">
						{#each artifactGroups(artifacts.filter( (artifact) => ids.includes(artifact.id) )) as group (group[0].id)}
							{#if group.length > 1}
								<ArtifactSet
									artifacts={group}
									pendingId={actions.pendingId}
									blocked={isTurnBlocked}
									errorMessage={actions.error}
									onUpdate={(id, payload) => actions.update(id, payload)}
									onAccept={(id) => actions.accept(id)}
									onDeny={(id, reason) => actions.deny(id, reason)}
									onRetry={(id) => actions.retry(id)}
								/>
							{:else}
								{@const artifact = group[0]}
								<ArtifactDrawer
									{artifact}
									pending={actions.pendingId === artifact.id}
									blocked={isTurnBlocked}
									errorMessage={actions.error}
									onUpdate={(payload) => actions.update(artifact.id, payload)}
									onAccept={() => actions.accept(artifact.id)}
									onDeny={(reason) => actions.deny(artifact.id, reason)}
									onRetry={() => actions.retry(artifact.id)}
								/>
							{/if}
						{/each}
					</div>
				{/snippet}
			</ChatThreadPanel>
		{/key}

		<div class="shrink-0 mt-4">
			<ChatComposerPanel
				onSend={handleSend}
				busy={isTurnBlocked}
				stopping={chat.view.run?.cancelRequested ?? false}
				queued={queuedMessages}
				queuePaused={queue.paused(activeThreadId)}
				onResumeQueue={() => queue.resume(activeThreadId)}
				onCancelQueued={(id) => queue.cancel(activeThreadId, id)}
				onStop={chat.view.run ? () => void chat.cancel() : undefined}
			/>
		</div>
	</div>
</main>
