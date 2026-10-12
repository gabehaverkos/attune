<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';

	import { untrack } from 'svelte';
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';
	import type { Artifact } from '#lib/chat/types.ts';
	import ArtifactDrawer from '#lib/chat/cards/ArtifactDrawer.svelte';

	// Several proposals the assistant made at once, such as a set of events: one
	// card at a time with a pager, each approved on its own or all together.
	// Working on any card in the set keeps every one of them from expiring.
	let {
		artifacts,
		pendingId,
		blocked = false,
		errorMessage,
		onUpdate,
		onAccept,
		onDeny,
		onRetry
	}: {
		artifacts: Artifact[];
		pendingId: string | null;
		blocked?: boolean;
		errorMessage: string;
		onUpdate: (id: string, payload: Record<string, unknown>) => void;
		onAccept: (id: string) => Promise<unknown> | void;
		onDeny: (id: string, reason: string | null) => void;
		onRetry: (id: string) => void;
	} = $props();

	let index = $state(
		untrack(() =>
			Math.max(
				0,
				artifacts.findIndex(
					(artifact) => artifact.status === 'draft' || artifact.status === 'ready'
				)
			)
		)
	);
	let interactionAt = $state(0);
	let approvingAll = $state(false);
	const current = $derived(artifacts[Math.min(index, artifacts.length - 1)]);
	const ready = $derived(artifacts.filter((artifact) => artifact.status === 'ready'));

	/** Approve each ready one in order, stopping at the first that fails; those done stay done. */
	async function approveAll() {
		approvingAll = true;
		try {
			for (const artifact of ready) {
				index = artifacts.findIndex((candidate) => candidate.id === artifact.id);
				await onAccept(artifact.id);
				if (artifacts.find((candidate) => candidate.id === artifact.id)?.status === 'failed') break;
			}
		} finally {
			approvingAll = false;
		}
	}
</script>

<div
	class="space-y-2"
	role="group"
	aria-label="Proposals made together"
	onpointerdowncapture={() => (interactionAt = Date.now())}
	onkeydowncapture={() => (interactionAt = Date.now())}
>
	<div class="flex items-center justify-between gap-2 px-1">
		<div class="flex items-center gap-1 text-sm text-muted-foreground">
			<button
				type="button"
				aria-label="Previous"
				class="inline-flex size-8 items-center justify-center rounded-full hover:bg-muted disabled:opacity-40"
				disabled={index === 0}
				onclick={() => (index -= 1)}><ChevronLeft class="size-4" /></button
			>
			<span class="min-w-14 text-center tabular-nums">{index + 1} of {artifacts.length}</span>
			<button
				type="button"
				aria-label="Next"
				class="inline-flex size-8 items-center justify-center rounded-full hover:bg-muted disabled:opacity-40"
				disabled={index >= artifacts.length - 1}
				onclick={() => (index += 1)}><ChevronRight class="size-4" /></button
			>
		</div>
		{#if ready.length > 1}
			<Button
				type="button"
				size="sm"
				variant="secondary"
				disabled={blocked || approvingAll || pendingId !== null}
				onclick={approveAll}
			>
				{approvingAll ? 'Approving…' : `Approve all ${ready.length}`}
			</Button>
		{/if}
	</div>
	{#each artifacts as artifact (artifact.id)}
		<div hidden={artifact.id !== current.id}>
			<ArtifactDrawer
				{artifact}
				pending={pendingId === artifact.id}
				{blocked}
				{errorMessage}
				siblings={artifacts}
				{interactionAt}
				onUpdate={(payload) => onUpdate(artifact.id, payload)}
				onAccept={() => onAccept(artifact.id)}
				onDeny={(reason) => onDeny(artifact.id, reason)}
				onRetry={() => onRetry(artifact.id)}
			/>
		</div>
	{/each}
</div>
