<script lang="ts">
	import { apiRequest } from '#lib/core/api.ts';
	import {
		formatMoment,
		nodesById,
		sourceTitle,
		type MemoryPanelData,
		type MemorySource
	} from '#lib/memory/types.ts';
	import {
		ArrowUpRight,
		ChevronRight,
		Loader,
		MessageCircle,
		PenLine,
		RotateCw,
		Sparkles,
		Trash2
	} from '@lucide/svelte';

	let {
		panel,
		onRefresh,
		onShowBelief,
		onSelectSource
	}: {
		panel: MemoryPanelData;
		onRefresh: () => Promise<void>;
		/** Returns to the main view with that belief open. */
		onShowBelief: (beliefId: string) => void;
		onSelectSource?: (threadId: string, messageId: string | null) => void;
	} = $props();

	let openId = $state<string | null>(null);
	let retrying = $state(false);
	let errorMessage = $state('');

	const nodes = $derived(nodesById(panel));

	const STATUS = {
		learning: { label: 'Learning', tone: 'bg-muted text-muted-foreground' },
		waiting: { label: 'Not learned', tone: 'bg-muted text-muted-foreground' },
		learned: { label: 'Learned', tone: 'bg-accent text-accent-foreground' },
		failed: { label: "Couldn't learn", tone: 'bg-destructive/10 text-destructive' },
		empty: { label: 'Nothing to learn', tone: 'bg-muted text-muted-foreground' }
	} as const;

	function summary(source: MemorySource): string {
		if (source.status === 'waiting') {
			return `${source.waiting} ${source.waiting === 1 ? 'message' : 'messages'} not learned yet`;
		}
		if (source.kind === 'review') {
			const count = source.changes.length;
			return `${count} ${count === 1 ? 'belief' : 'beliefs'} rearranged, nothing added`;
		}
		if (source.kind === 'removal') return 'no longer known';
		if (source.status === 'learned') {
			const count = source.changes.length;
			return count
				? `${count} ${count === 1 ? 'change' : 'changes'} to memory`
				: 'nothing it taught is current';
		}
		if (source.status === 'learning') return 'learning now';
		if (source.status === 'empty') return 'no personal facts in it';
		return 'not learned yet';
	}

	async function retry() {
		if (retrying) return;
		retrying = true;
		errorMessage = '';
		try {
			await apiRequest('/api/memory/retry', { method: 'POST' });
			await onRefresh();
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Unable to try again.';
		} finally {
			retrying = false;
		}
	}
</script>

<div class="flex flex-col gap-3">
	{#if panel.failed > 0}
		<div
			class="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted px-3 py-2 text-sm"
		>
			<p>{panel.failed} {panel.failed === 1 ? 'input needs' : 'inputs need'} another attempt.</p>
			<button
				type="button"
				disabled={retrying}
				onclick={retry}
				class="flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg px-2 underline underline-offset-2 disabled:opacity-50"
			>
				<RotateCw class={['size-3.5', retrying && 'animate-spin']} aria-hidden="true" />
				{retrying ? 'Trying again…' : 'Retry failed inputs'}
			</button>
		</div>
	{/if}

	{#each panel.sources as source (source.id)}
		{@const open = openId === source.id}
		{@const status = STATUS[source.status]}
		<div class="overflow-hidden rounded-xl border border-border">
			<button
				type="button"
				aria-expanded={open}
				onclick={() => (openId = open ? null : source.id)}
				class={[
					'flex min-h-14 w-full cursor-pointer items-center gap-3 px-3.5 py-3 text-left hover:bg-muted',
					open && 'bg-muted/60'
				]}
			>
				<span
					class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"
				>
					{#if source.kind === 'conversation'}
						<MessageCircle class="size-4" aria-hidden="true" />
					{:else if source.kind === 'review'}
						<Sparkles class="size-4" aria-hidden="true" />
					{:else if source.kind === 'removal'}
						<Trash2 class="size-4" aria-hidden="true" />
					{:else}
						<PenLine class="size-4" aria-hidden="true" />
					{/if}
				</span>
				<span class="flex min-w-0 flex-1 flex-col gap-0.5">
					<span class="text-body font-medium">{sourceTitle(source)}</span>
					<span class="text-note text-muted-foreground"
						>{formatMoment(source.created_at)} · {summary(source)}</span
					>
				</span>
				<span
					class={[
						'flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-note font-medium',
						status.tone
					]}
				>
					{#if source.status === 'learning'}<Loader
							class="size-3 animate-spin"
							aria-hidden="true"
						/>{/if}
					{source.kind === 'review'
						? 'Tidied'
						: source.kind === 'removal'
							? 'Forgotten'
							: status.label}
				</span>
				<ChevronRight
					class={[
						'size-3.5 shrink-0 text-muted-foreground transition-transform',
						open && 'rotate-90'
					]}
					aria-hidden="true"
				/>
			</button>

			{#if open}
				<div class="flex flex-col gap-3 px-3.5 pt-1 pb-3.5 pl-[3.625rem]">
					{#if source.statement}
						<blockquote class="rounded-lg bg-muted/70 px-3.5 py-3 text-body leading-relaxed">
							{source.statement}
						</blockquote>
					{/if}
					{#if source.status === 'learning'}
						<p class="text-sm text-foreground/80">
							Attune is learning from this now. Until it finishes, it can still use what you said in
							chat.
						</p>
					{:else if source.status === 'waiting'}
						<p class="text-sm text-foreground/80">
							Attune is about to learn this. It can already use what you said in chat.
						</p>
					{:else if source.status === 'failed'}
						<p class="text-sm text-foreground/80">
							Attune couldn't learn this yet. Nothing was lost; it is still available to Attune in
							chat.
						</p>
						<button
							type="button"
							disabled={retrying}
							onclick={retry}
							class="flex h-9 cursor-pointer items-center gap-1.5 self-start rounded-lg border border-border px-3 text-sm hover:bg-muted disabled:opacity-50"
						>
							<RotateCw class={['size-3.5', retrying && 'animate-spin']} aria-hidden="true" />
							{retrying ? 'Trying again…' : 'Retry failed inputs'}
						</button>
					{/if}
					{#if source.changes.length}
						<div class="flex flex-col">
							<p
								class="mb-1 text-note font-semibold tracking-wider text-muted-foreground uppercase"
							>
								What changed in memory
							</p>
							{#each source.changes as change, index (index)}
								{@const nodeId = change.node_id}
								{#if nodeId && nodes.get(nodeId)}
									<button
										type="button"
										onclick={() => onShowBelief(nodeId)}
										title="Show this belief"
										class="-ml-2 flex min-h-9 cursor-pointer items-baseline gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-muted"
									>
										<span class="w-[6.75rem] shrink-0 text-note text-muted-foreground"
											>{change.label}</span
										>
										<span class="text-body">{change.text}</span>
									</button>
								{:else}
									<p class="-ml-2 flex min-h-9 items-baseline gap-2 px-2 py-1.5">
										<span class="w-[6.75rem] shrink-0 text-note text-muted-foreground"
											>{change.label}</span
										>
										<span class="text-body text-muted-foreground">{change.text}</span>
									</p>
								{/if}
							{/each}
						</div>
					{/if}
					{#if source.thread_id && onSelectSource}
						<button
							type="button"
							onclick={() => onSelectSource(source.thread_id as string, null)}
							class="flex cursor-pointer items-center gap-1 self-start text-sm font-medium underline underline-offset-2 hover:text-foreground"
						>
							Open conversation<ArrowUpRight class="size-3.5" aria-hidden="true" />
						</button>
					{/if}
				</div>
			{/if}
		</div>
	{:else}
		<p class="rounded-lg border border-dashed border-border p-4 text-body text-muted-foreground">
			Nothing yet. Attune learns from each message as its reply ends, and from anything you tell it
			here.
		</p>
	{/each}

	{#if errorMessage}
		<p class="text-body text-destructive" role="alert">{errorMessage}</p>
	{/if}
</div>
