<script lang="ts">
	import { onMount, tick, type Snippet } from 'svelte';
	import { RotateCcw } from '@lucide/svelte';
	import type { ChatThreadMessage } from '#lib/chat/types.ts';
	import { failureText } from '#lib/chat/reducer.ts';
	import ActivityIndicator from '#lib/chat/ActivityIndicator.svelte';
	import ReplyBody from '#lib/chat/ReplyBody.svelte';
	import EarlierArtifact from '#lib/chat/cards/EarlierArtifact.svelte';
	import { flight, type FlightOrigin, reveal } from '#styles/motion.ts';

	let {
		messages,
		artifactIds = [],
		children,
		outgoingMessage,
		anchorMessageId = null,
		anchorKey = 0,
		activity = null,
		activeMessageId = null,
		retryableRunId = null,
		onRetry,
		onResend,
		onDiscard
	}: {
		children?: Snippet<[string[]]>;
		outgoingMessage?: { id: string; origin: FlightOrigin };
		artifactIds?: string[];
		messages: ChatThreadMessage[];
		/** A message to open on instead of the latest, linked to from memory. */
		anchorMessageId?: string | null;
		/** Changes each time a link is followed, so the same message is landed on again. */
		anchorKey?: number;
		/** What the running reply is doing, as the server last reported it. */
		activity?: string | null;
		/** The reply currently being written. */
		activeMessageId?: string | null;
		/** The run a Retry button may name; only the latest reply that ended early. */
		retryableRunId?: string | null;
		onRetry?: (runId: string) => void;
		onResend?: (messageId: string) => void;
		onDiscard?: (messageId: string) => void;
	} = $props();

	let threadViewport: HTMLDivElement;
	let threadContent: HTMLDivElement;
	let followLatest = true;
	let lastUserMessageId: string | undefined;
	/** The message the anchor landed on, held only long enough to mark it. */
	let highlighted = $state<string | null>(null);
	let anchored: string | null = null;
	let anchoredKey = 0;

	function scrollToLatest() {
		if (followLatest && threadViewport) {
			// Follow the animated layout directly, rather than restarting smooth scroll each frame.
			threadViewport.scrollTo({ top: threadViewport.scrollHeight, behavior: 'instant' });
		}
	}

	onMount(() => {
		const observer = new ResizeObserver(scrollToLatest);
		observer.observe(threadContent);
		observer.observe(threadViewport);
		return () => observer.disconnect();
	});

	// Long enough to find on arrival, brief enough not to become part of the thread.
	$effect(() => {
		if (!highlighted) return;
		const timer = setTimeout(() => (highlighted = null), 2400);
		return () => clearTimeout(timer);
	});

	const latestMessageByArtifact = $derived.by(() => {
		const latestMessageByArtifact = new Map<string, string>();
		for (const message of messages) {
			for (const event of message.artifact_events ?? []) {
				latestMessageByArtifact.set(event.artifact_id, message.id);
			}
		}
		return latestMessageByArtifact;
	});

	const supersededArtifactMessageIds = $derived.by(() => {
		return new Set(
			messages
				.filter(
					(message) =>
						message.artifact_events?.length &&
						message.artifact_events.every(
							(event) => latestMessageByArtifact.get(event.artifact_id) !== message.id
						)
				)
				.map((message) => message.id)
		);
	});

	/**
	 * The cards a reply shows, those whose latest change it made, each once: where
	 * in its text it first appeared. A card superseded later in the same reply
	 * stays where it was, and its replacement shows where it was made.
	 */
	function shownHere(message: ChatThreadMessage) {
		const shown: { id: string; offset: number | null | undefined }[] = [];
		for (const event of message.artifact_events ?? []) {
			if (
				artifactIds.includes(event.artifact_id) &&
				latestMessageByArtifact.get(event.artifact_id) === message.id &&
				!shown.some((card) => card.id === event.artifact_id)
			)
				shown.push({ id: event.artifact_id, offset: event.text_offset });
		}
		return shown;
	}

	$effect(() => {
		const latestUserId = messages.findLast((message) => message.author === 'user')?.id;
		if (latestUserId !== lastUserMessageId) {
			lastUserMessageId = latestUserId;
			followLatest = true;
			// Room made to centre a linked message is not part of the conversation.
			room = 0;
		}
		void tick().then(scrollToLatest);
	});

	/**
	 * Land on a linked message rather than the newest one. Following the latest is
	 * the default and a ResizeObserver keeps enforcing it, so this has to switch it
	 * off before scrolling — and only once for each time the link is followed, or
	 * reading further up would be undone every time the thread grows. It comes
	 * after the effect above, which turns following back on when the session shown
	 * changes: a link into another session would otherwise land on its newest.
	 */
	$effect(() => {
		const target = anchorMessageId;
		const key = anchorKey;
		if (!target || (target === anchored && key === anchoredKey)) return;
		if (!messages.some((message) => message.id === target)) return;
		anchored = target;
		anchoredKey = key;
		followLatest = false;
		void tick().then(() => {
			const element = document.getElementById(`message-${target}`);
			if (!element) {
				// The message is in the thread but not yet laid out; the newest is a
				// better resting place than wherever the viewport happens to sit.
				followLatest = true;
				return;
			}
			highlighted = target;
			void centre(element);
		});
	});

	// Space above the thread, so that its first messages can sit in the middle too:
	// there is nothing above them to scroll past otherwise. None is made below. A
	// conversation ends where it ends, and its last messages rest at the bottom.
	let room = $state(0);

	/** Put a message in the middle of the view, or its top in view when it is taller. */
	async function centre(element: HTMLElement) {
		const view = threadViewport.clientHeight;
		const box = element.getBoundingClientRect();
		// Where it is in the thread itself, without any room already made.
		const top =
			box.top - threadViewport.getBoundingClientRect().top + threadViewport.scrollTop - room;
		const wanted = top - Math.max(16, (view - box.height) / 2);
		room = Math.max(0, -wanted);
		await tick();
		threadViewport.scrollTo({ top: wanted + room, behavior: 'instant' });
	}
</script>

<!-- Focusable scroll region: keyboard users need to scroll and pause auto-follow. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div
	bind:this={threadViewport}
	role="region"
	aria-label="Conversation"
	data-flight-viewport
	tabindex="0"
	class="thread-viewport min-h-0 flex-1 overflow-y-auto pr-1 scroll-smooth"
	onwheel={(event) => {
		if (event.deltaY < 0) followLatest = false;
	}}
	ontouchstart={() => (followLatest = false)}
	onkeydown={(event) => {
		if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) followLatest = false;
	}}
	onscroll={() => {
		if (threadViewport.scrollHeight - threadViewport.scrollTop - threadViewport.clientHeight < 4) {
			followLatest = true;
		}
	}}
>
	<div aria-hidden="true" class="shrink-0" style:height={`${room}px`}></div>
	<div bind:this={threadContent} class="flex min-h-full flex-col justify-end space-y-10">
		{#each messages as msg (msg.client_key ?? msg.id)}
			<div
				id="message-{msg.id}"
				class={[
					'flex w-full flex-col items-start scroll-mt-8 rounded-2xl transition-colors duration-(--motion-slow)',
					highlighted === msg.id && 'bg-accent/40'
				]}
			>
				{#if msg.author === 'user'}
					<!-- User message: Extra large, extremely spacious speech bubble -->
					<div
						use:flight={outgoingMessage?.id === (msg.client_key ?? msg.id)
							? outgoingMessage.origin
							: undefined}
						class={[
							'max-w-[85%] rounded-[2rem] rounded-tl-none px-8 py-5 bg-primary text-primary-foreground shadow-sm text-xl leading-relaxed',
							msg.send_failed && 'opacity-60'
						]}
					>
						<p class="whitespace-pre-wrap">{msg.content}</p>
					</div>
					{#if msg.send_failed}
						<div class="mt-2 flex flex-wrap items-center gap-3 px-2 text-sm text-muted-foreground">
							<span>Not sent. {msg.send_error ?? ''}</span>
							{#if onResend}
								<button
									type="button"
									class="font-medium text-foreground underline-offset-4 hover:underline"
									onclick={() => onResend(msg.id)}>Send again</button
								>
							{/if}
							{#if onDiscard}
								<button
									type="button"
									class="hover:text-foreground hover:underline underline-offset-4"
									onclick={() => onDiscard(msg.id)}>Remove</button
								>
							{/if}
						</div>
					{/if}
				{:else}
					<!-- Chat/Agent message: Premium extra large printed text block -->
					<ReplyBody
						content={msg.content}
						streaming={msg.status === 'streaming'}
						placements={supersededArtifactMessageIds.has(msg.id) ? [] : shownHere(msg)}
						cards={children}
					/>
					<div class="max-w-full text-xl leading-relaxed transition-all w-full">
						{#if msg.status === 'streaming'}
							{#if msg.id === activeMessageId}
								<ActivityIndicator label={activity} />
							{/if}
						{:else if msg.status === 'failed' || msg.status === 'interrupted'}
							<div
								class="mt-2 flex flex-wrap items-center gap-3 text-sm text-muted-foreground"
								data-reply-ended
							>
								<span>{failureText(msg)}</span>
								{#if onRetry && msg.run_id && msg.run_id === retryableRunId}
									<button
										type="button"
										onclick={() => onRetry(msg.run_id as string)}
										class="inline-flex items-center gap-1.5 font-medium text-foreground underline-offset-4 hover:underline"
									>
										<RotateCcw class="size-3.5" aria-hidden="true" />
										Retry
									</button>
								{/if}
							</div>
						{/if}
					</div>
					{#if supersededArtifactMessageIds.has(msg.id)}
						<div class="mt-4 w-full" transition:reveal>
							<EarlierArtifact events={msg.artifact_events ?? []} />
						</div>
					{/if}
				{/if}
			</div>
		{/each}
	</div>
</div>

<style>
	.thread-viewport {
		scrollbar-width: none;
	}

	.thread-viewport::-webkit-scrollbar {
		display: none;
	}
</style>
