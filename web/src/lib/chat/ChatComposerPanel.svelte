<script lang="ts">
	import { ArrowUp, Clock, Send, Square, X } from '@lucide/svelte';
	import { apiRequest } from '#lib/core/api.ts';
	import { captureFlightOrigin, type FlightOrigin } from '#styles/motion.ts';

	let {
		onSend,
		busy = false,
		stopping = false,
		queued = [],
		queuePaused = false,
		onResumeQueue = () => undefined,
		onCancelQueued = () => undefined,
		onStop = undefined
	}: {
		onSend: (text: string, origin?: FlightOrigin) => void;
		busy?: boolean;
		/** A stop was requested and the server has not confirmed it yet. */
		stopping?: boolean;
		queued?: { id: string; content: string }[];
		/** The last reply ended early, so queued messages wait for the user. */
		queuePaused?: boolean;
		onResumeQueue?: () => void;
		onCancelQueued?: (id: string) => void;
		/** Explicitly cancels the running response; leaving the page only detaches. */
		onStop?: () => void;
	} = $props();

	let text = $state('');
	let textarea = $state<HTMLTextAreaElement>();

	// How much of the day's allowance is left, as a share. Read when the composer
	// appears and again as each reply ends, since a reply is what spends it.
	let left = $state<number | null>(null);
	const leftPercent = $derived(left === null ? null : Math.round(left * 100));
	// The gauge's note, opened by clicking it.
	let usageOpen = $state(false);

	$effect(() => {
		if (busy) return;
		apiRequest<{ used: number }>('/api/chat/allowance')
			.then(({ used }) => (left = Math.max(0, 1 - used)))
			.catch(() => undefined); // Only this line depends on it.
	});

	function handleSubmit() {
		if (!text.trim()) return;
		onSend(text.trim(), captureFlightOrigin(textarea));
		text = '';
	}

	function handleKeyDown(event: KeyboardEvent) {
		if (event.key === 'Enter' && !event.shiftKey) {
			event.preventDefault();
			handleSubmit();
		}
	}
</script>

<div class="pt-6" data-chat-composer>
	{#if queued.length}
		{#if queuePaused}
			<div
				class="mb-1.5 flex items-center justify-between gap-2 px-1 text-sm text-muted-foreground"
			>
				<span>Unsent — the last response did not finish.</span>
				{#if !busy}
					<button
						type="button"
						onclick={onResumeQueue}
						class="inline-flex items-center gap-1.5 font-medium text-foreground underline-offset-4 hover:underline"
					>
						<Send class="size-3.5" aria-hidden="true" />
						Send {queued.length === 1 ? 'it' : 'them'}
					</button>
				{/if}
			</div>
		{/if}
		<ul class="mb-2 space-y-1.5" aria-label={queuePaused ? 'Unsent messages' : 'Queued messages'}>
			{#each queued as item (item.id)}
				<li
					class="flex items-start gap-2.5 rounded-2xl border border-dashed border-border/80 bg-muted/35 px-4 py-2 text-sm text-muted-foreground"
				>
					<Clock class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
					<span data-queued-message={item.id} class="min-w-0 flex-1 break-words whitespace-pre-wrap"
						>{item.content}</span
					>
					<button
						type="button"
						onclick={() => onCancelQueued(item.id)}
						class="-mr-1 shrink-0 rounded p-1 transition hover:bg-muted hover:text-foreground"
						aria-label="Remove queued message"
					>
						<X class="size-3.5" />
					</button>
				</li>
			{/each}
		</ul>
	{/if}
	<form
		onsubmit={(event) => {
			event.preventDefault();
			handleSubmit();
		}}
		class="rounded-[1.75rem] border border-border/90 bg-card shadow-sm transition focus-within:border-primary/45 focus-within:ring-1 focus-within:ring-primary/30"
	>
		<textarea
			bind:this={textarea}
			bind:value={text}
			onkeydown={handleKeyDown}
			placeholder={busy ? 'Reply — sends when the response finishes' : 'Message the Assistant'}
			class="block max-h-[15rem] min-h-[4rem] w-full resize-none bg-transparent px-5 pt-4 pb-2 text-base leading-6 text-foreground outline-none placeholder:text-muted-foreground"
			rows="1"
		></textarea>
		<div class="flex h-12 items-center justify-end gap-2 px-3 pb-2">
			{#if leftPercent !== null}
				{@const low = leftPercent <= 25}
				{@const critical = leftPercent <= 10}
				{@const remaining =
					leftPercent > 0
						? `${leftPercent}% of today's allowance left`
						: "Today's allowance is used up"}
				<!-- A quiet gauge until it runs low: the number is a click away, then said outright. -->
				<div
					class={[
						'relative mr-auto flex items-center gap-2 pl-1 text-xs',
						critical ? 'text-destructive' : 'text-muted-foreground'
					]}
				>
					<button
						type="button"
						class="inline-flex size-7 cursor-pointer items-center justify-center rounded-full hover:bg-muted"
						aria-label="Today's allowance"
						aria-expanded={usageOpen}
						onclick={() => (usageOpen = !usageOpen)}
						onblur={() => (usageOpen = false)}
						onkeydown={(event) => {
							if (event.key === 'Escape') usageOpen = false;
						}}
					>
						<svg
							class="size-4 -rotate-90"
							viewBox="0 0 16 16"
							role="meter"
							aria-label="Today's allowance left"
							aria-valuemin="0"
							aria-valuemax="100"
							aria-valuenow={leftPercent}
						>
							<circle cx="8" cy="8" r="6" fill="none" stroke-width="2.5" class="stroke-muted" />
							<circle
								cx="8"
								cy="8"
								r="6"
								fill="none"
								stroke-width="2.5"
								stroke-linecap="round"
								pathLength="100"
								stroke-dasharray={`${leftPercent} 100`}
								class={critical
									? 'stroke-destructive'
									: low
										? 'stroke-primary'
										: 'stroke-primary/50'}
							/>
						</svg>
					</button>
					{#if low}<span>{remaining}</span>{/if}
					{#if usageOpen}
						<div
							class="absolute bottom-full left-0 z-20 mb-2 rounded-lg border border-border bg-card px-3 py-2 text-left text-xs whitespace-nowrap text-muted-foreground shadow-lg"
							role="tooltip"
						>
							<p class={['font-medium', critical ? 'text-destructive' : 'text-foreground']}>
								{remaining}
							</p>
						</div>
					{/if}
				</div>
			{/if}
			{#if busy && onStop}
				<button
					type="button"
					onclick={onStop}
					disabled={stopping}
					class="inline-flex size-9 items-center justify-center rounded-full border border-border bg-background text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:cursor-wait disabled:opacity-50"
					title={stopping ? 'Stopping…' : 'Stop the response'}
					aria-label={stopping ? 'Stopping the response' : 'Stop the response'}
				>
					<Square class="size-3.5" fill="currentColor" strokeWidth={0} />
				</button>
			{/if}
			<button
				type="submit"
				disabled={!text.trim()}
				class="inline-flex size-9 items-center justify-center rounded-full bg-primary text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground"
				title={busy ? 'Queue this message for when the response finishes' : 'Send message'}
				aria-label={busy ? 'Queue message' : 'Send message'}
			>
				{#if busy}
					<Clock class="size-4.5" strokeWidth={2.5} />
				{:else}
					<ArrowUp class="size-4.5" strokeWidth={2.5} />
				{/if}
			</button>
		</div>
	</form>
</div>
