<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';

	import { Trash2 } from '@lucide/svelte';
	import type { ArtifactViewProps } from '#lib/chat/cards/registry.ts';

	// Forgetting everything Attune knows: nothing to edit, so a compact card saying
	// how much goes and that it cannot be undone, with one button to do it.
	let { artifact, pending, errorMessage, onAccept, onDeny }: ArtifactViewProps = $props();

	const held = $derived(
		Number((artifact.approved_payload ?? artifact.draft_payload)?.held ?? 0) || 0
	);
	const editable = $derived(artifact.status === 'draft' || artifact.status === 'ready');
</script>

<div class="space-y-4" aria-busy={pending || artifact.status === 'executing'}>
	<div class="rounded-xl border border-border bg-muted/35 p-4">
		<p class="flex items-start gap-2 font-semibold">
			<Trash2 class="mt-1 size-4 shrink-0 text-destructive" />
			{held === 1
				? 'The one thing Attune knows about you'
				: held
					? `All ${held} things Attune knows about you`
					: 'Everything Attune knows about you'}
		</p>
		<ul class="mt-3 space-y-1 border-t border-border pt-3 text-sm">
			<li>Attune will stop knowing all of it. This can't be undone.</li>
			<li>Your conversations and your calendar stay as they are.</li>
		</ul>
	</div>
	{#if artifact.status === 'failed'}
		<Notice tone="error">{artifact.error?.message ?? 'Memory could not be cleared.'}</Notice>
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
					>{pending ? 'Forgetting…' : 'Forget everything'}</Button
				>
			{/if}
		</div>
	{/if}
</div>
