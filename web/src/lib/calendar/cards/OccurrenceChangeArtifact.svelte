<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';

	import { untrack } from 'svelte';
	import OccurrenceEditor, { type OccurrenceValue } from '#lib/calendar/OccurrenceEditor.svelte';
	import { clock, occurrencePayload, toOccurrenceValue } from '#lib/calendar/calendar.ts';
	import type { ArtifactViewProps } from '#lib/chat/cards/registry.ts';
	import ArtifactActions from '#lib/chat/cards/ArtifactActions.svelte';

	// One day of a routine changed on its own: the calendar's one-day editor,
	// skipped or retimed, with the rest of that day around it.
	let { artifact, pending, errorMessage, onUpdate, onAccept, onDeny, onRetry }: ArtifactViewProps =
		$props();

	const persisted = $derived(occurrencePayload(artifact));
	const editable = $derived(artifact.status === 'draft' || artifact.status === 'ready');
	let value = $state<OccurrenceValue | null>(
		untrack(() => (persisted ? toOccurrenceValue(persisted) : null))
	);
	let loadedRevision = untrack(() => artifact.revision);
	let editorKey = $state(0);

	$effect(() => {
		if (!persisted || loadedRevision === artifact.revision) return;
		value = toOccurrenceValue(persisted);
		loadedRevision = artifact.revision;
		editorKey += 1;
	});

	const dirty = $derived(
		!!value && !!persisted && JSON.stringify(value) !== JSON.stringify(toOccurrenceValue(persisted))
	);

	function save() {
		if (!value || !persisted) return;
		onUpdate({
			...persisted,
			operation: value.skip ? 'skip' : 'custom',
			start: value.skip ? persisted.start : clock(value.start),
			end: value.skip ? persisted.end : clock(value.end)
		});
	}

	function cancel() {
		if (persisted) value = toOccurrenceValue(persisted);
		editorKey += 1;
	}
</script>

<div class="space-y-5" aria-busy={pending || artifact.status === 'executing'}>
	{#if !persisted || !value || !persisted.window_id || !persisted.occurrence_date}
		<Notice tone="error">This change doesn't name a day of a routine the calendar has.</Notice>
	{:else}
		{#key editorKey}
			<OccurrenceEditor
				bind:value
				date={persisted.occurrence_date}
				windowId={persisted.window_id}
				title={persisted.routine_title ?? 'Routine'}
				disabled={!editable || pending}
			/>
		{/key}
		{#if artifact.status === 'failed'}
			<Notice tone="error">{artifact.error?.message ?? 'This day could not be changed.'}</Notice>
		{/if}
		{#if errorMessage}<p class="text-sm text-destructive" role="alert">{errorMessage}</p>{/if}
		{#if editable}
			<div class="flex flex-wrap items-center justify-center gap-2">
				{#if dirty}
					<button
						type="button"
						class="h-10 rounded-md px-3 text-sm font-medium text-muted-foreground"
						onclick={cancel}
						disabled={pending}>Cancel</button
					>
					<Button type="button" onclick={save} disabled={pending}
						>{pending ? 'Saving…' : 'Save changes'}</Button
					>
				{:else}
					{#if artifact.status === 'ready'}<Button
							type="button"
							onclick={onAccept}
							disabled={pending}>{pending ? 'Approving…' : 'Change this day'}</Button
						>{/if}
					<ArtifactActions showDeny {pending} {onDeny} {onRetry} />
				{/if}
			</div>
		{/if}
	{/if}
</div>
