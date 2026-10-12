<script lang="ts">
	import MemoryBeliefs from '#lib/memory/MemoryBeliefs.svelte';
	import MemoryActivity from '#lib/memory/MemoryActivity.svelte';
	import { apiRequest } from '#lib/core/api.ts';
	import type { MemoryPanelData } from '#lib/memory/types.ts';
	import { Sparkles, X } from '@lucide/svelte';

	let {
		panel,
		onPanel,
		onRefresh,
		onSelectSource,
		onClose
	}: {
		panel: MemoryPanelData | null;
		onPanel: (next: MemoryPanelData) => void;
		onRefresh: () => Promise<void>;
		onSelectSource?: (threadId: string, messageId: string | null) => void;
		onClose?: () => void;
	} = $props();

	const id = $props.id();
	let activityOpen = $state(false);
	let openBeliefId = $state<string | null>(null);
	let beliefsTab: HTMLButtonElement;
	let activityTab: HTMLButtonElement;

	function navigateTabs(event: KeyboardEvent) {
		if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
		event.preventDefault();
		activityOpen = event.key === 'End' || (event.key !== 'Home' && !activityOpen);
		(activityOpen ? activityTab : beliefsTab)?.focus();
	}

	let tidying = $state(false);
	let tidyError = $state('');
	const tidiedOut = $derived(!!panel && !panel.reviewing && panel.reviews_left === 0);

	/** Asks Attune to look over what it knows and arrange it better, now. */
	async function tidy() {
		if (tidying) return;
		tidying = true;
		tidyError = '';
		try {
			onPanel(await apiRequest<MemoryPanelData>('/api/memory/review', { method: 'POST' }));
		} catch (error) {
			tidyError = error instanceof Error ? error.message : 'Unable to tidy up.';
		} finally {
			tidying = false;
		}
	}

	function showBelief(beliefId: string) {
		openBeliefId = beliefId;
		activityOpen = false;
		beliefsTab?.focus();
	}
</script>

<div class="flex h-full min-h-0 flex-col">
	<header class="flex flex-col gap-3 px-5 pt-5">
		<div class="flex items-start justify-between gap-3">
			<div class="flex min-w-0 flex-col gap-1">
				<p class="text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase">
					Memory
				</p>
				<h1 class="text-lg font-semibold text-foreground">About you</h1>
				<p class="text-body leading-snug text-muted-foreground">
					{activityOpen
						? 'What Attune has been learning from, and how it went.'
						: 'What Attune knows about you.'}
				</p>
				{#if tidyError}
					<p class="text-body text-destructive" role="alert">{tidyError}</p>
				{/if}
			</div>
			<div class="flex shrink-0 items-center gap-1">
				{#if panel?.top.length}
					<button
						type="button"
						onclick={tidy}
						disabled={tidying || panel.reviewing || tidiedOut}
						title={tidiedOut
							? 'Tidied as often as it can today.'
							: 'Attune looks over what it knows and arranges it better. Nothing is added or removed.'}
						class="flex h-9 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 text-note text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-default disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
					>
						<Sparkles class="size-3.5" aria-hidden="true" />{panel.reviewing
							? 'Tidying…'
							: 'Tidy up'}
					</button>
				{/if}
				{#if onClose}
					<button
						type="button"
						onclick={onClose}
						aria-label="Close memory"
						title="Close memory"
						class="inline-flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
					>
						<X class="size-4" />
					</button>
				{/if}
			</div>
		</div>
		<div role="tablist" aria-label="Memory views" class="grid grid-cols-2 border-b border-border">
			<button
				bind:this={beliefsTab}
				id={`${id}-beliefs-tab`}
				type="button"
				role="tab"
				aria-selected={!activityOpen}
				aria-controls={`${id}-beliefs-panel`}
				tabindex={activityOpen ? -1 : 0}
				onclick={() => (activityOpen = false)}
				onkeydown={navigateTabs}
				class="min-h-12 cursor-pointer border-b-2 px-4 text-body transition-colors {activityOpen
					? 'border-transparent text-muted-foreground hover:text-foreground'
					: 'border-foreground font-medium text-foreground'}"
			>
				Beliefs
			</button>
			<button
				bind:this={activityTab}
				id={`${id}-activity-tab`}
				type="button"
				role="tab"
				aria-selected={activityOpen}
				aria-controls={`${id}-activity-panel`}
				tabindex={activityOpen ? 0 : -1}
				onclick={() => (activityOpen = true)}
				onkeydown={navigateTabs}
				class="flex min-h-12 cursor-pointer items-center justify-center gap-2 border-b-2 px-4 text-body transition-colors {activityOpen
					? 'border-foreground font-medium text-foreground'
					: 'border-transparent text-muted-foreground hover:text-foreground'}"
			>
				Activity
				{#if (panel?.failed ?? 0) > 0}
					<span class="rounded-full bg-destructive/10 px-1.5 text-destructive"
						>{panel?.failed} failed</span
					>
				{:else if panel?.working}<span class="rounded-full bg-muted px-1.5">Learning</span>{/if}
			</button>
		</div>
	</header>
	<div class="min-h-0 flex-1 overflow-y-auto px-5 pt-4 pb-8">
		<div
			id={`${id}-beliefs-panel`}
			role="tabpanel"
			aria-labelledby={`${id}-beliefs-tab`}
			tabindex="0"
			hidden={activityOpen}
		>
			{#if panel}
				<MemoryBeliefs {panel} bind:openBeliefId {onPanel} {onSelectSource} />
			{:else}
				<p class="text-body text-muted-foreground">Loading…</p>
			{/if}
		</div>
		<div
			id={`${id}-activity-panel`}
			role="tabpanel"
			aria-labelledby={`${id}-activity-tab`}
			tabindex="0"
			hidden={!activityOpen}
		>
			{#if panel && activityOpen}
				<MemoryActivity {panel} {onRefresh} onShowBelief={showBelief} {onSelectSource} />
			{:else if !panel}
				<p class="text-body text-muted-foreground">Loading…</p>
			{/if}
		</div>
	</div>
</div>
