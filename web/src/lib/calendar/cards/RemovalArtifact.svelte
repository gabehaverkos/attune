<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';

	import { Trash2 } from '@lucide/svelte';
	import type { ArtifactViewProps } from '#lib/chat/cards/registry.ts';
	import { removalPayload } from '#lib/calendar/calendar.ts';

	// Removing an event or a whole routine: nothing to edit, so a compact card
	// saying what goes and what comes back, with one button to do it.
	let { artifact, pending, errorMessage, onAccept, onDeny }: ArtifactViewProps = $props();

	const payload = $derived(removalPayload(artifact));
	const editable = $derived(artifact.status === 'draft' || artifact.status === 'ready');
	const noun = $derived(payload?.target_type === 'event' ? 'event' : 'routine');
</script>

<div class="space-y-4" aria-busy={pending || artifact.status === 'executing'}>
	{#if !payload}
		<Notice tone="error"
			>This removal proposal contains data that cannot be displayed safely.</Notice
		>
	{:else}
		<div class="rounded-xl border border-border bg-muted/35 p-4">
			<p class="flex items-center gap-2 font-semibold">
				<Trash2 class="size-4 text-destructive" />
				{payload.title ?? `This ${noun}`}
			</p>
			{#each payload.summary as line, index (index)}<p class="mt-1 text-sm text-muted-foreground">
					{line}
				</p>{/each}
			{#if payload.consequences.length}
				<ul class="mt-3 space-y-1 border-t border-border pt-3 text-sm">
					{#each payload.consequences as line, index (index)}<li>{line}</li>{/each}
				</ul>
			{/if}
		</div>
		{#if artifact.status === 'failed'}
			<Notice tone="error">{artifact.error?.message ?? `This ${noun} could not be removed.`}</Notice
			>
		{/if}
		{#if errorMessage}<p class="text-sm text-destructive" role="alert">{errorMessage}</p>{/if}
		{#if editable}
			<div class="flex flex-wrap items-center justify-center gap-2">
				<button
					type="button"
					class="h-10 rounded-md px-3 text-sm font-medium text-muted-foreground hover:bg-muted"
					onclick={() => onDeny(null)}
					disabled={pending}>Keep it</button
				>
				{#if artifact.status === 'ready'}
					<Button type="button" variant="destructive" onclick={onAccept} disabled={pending}
						>{pending ? 'Removing…' : `Delete ${noun}`}</Button
					>
				{/if}
			</div>
		{/if}
	{/if}
</div>
