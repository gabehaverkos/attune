<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';

	import { untrack } from 'svelte';
	import WeeklyRoutineEditor from '#lib/calendar/WeeklyRoutineEditor.svelte';
	import {
		normalizeRoutineEditorValue,
		otherRoutinePayload,
		routineEditorErrors,
		type RoutineEditorValue,
		toRoutineEditorValue,
		workRoutinePayload
	} from '#lib/calendar/calendar.ts';
	import type { ArtifactViewProps } from '#lib/chat/cards/registry.ts';
	import ArtifactActions from '#lib/chat/cards/ArtifactActions.svelte';

	// Work and other routines: one card, since they differ only in the calendar's
	// rules, and the editor already knows those from the routine's kind.
	let { artifact, pending, errorMessage, onUpdate, onAccept, onDeny, onRetry }: ArtifactViewProps =
		$props();

	const label = $derived(artifact.type === 'work_routine' ? 'Work routine' : 'Other routine');

	function payload(value: Record<string, unknown> | null) {
		return artifact.type === 'work_routine'
			? workRoutinePayload(value)
			: otherRoutinePayload(value);
	}

	function comparable(value: RoutineEditorValue) {
		return JSON.stringify(normalizeRoutineEditorValue(value));
	}

	const initialPayload = untrack(() => payload(artifact.draft_payload));
	const persisted = $derived(payload(artifact.draft_payload));
	const displayed = $derived(payload(artifact.approved_payload ?? artifact.draft_payload));
	const editable = $derived(artifact.status === 'draft' || artifact.status === 'ready');
	let editorValue = $state<RoutineEditorValue | null>(
		initialPayload ? toRoutineEditorValue(initialPayload) : null
	);
	let loadedRevision = untrack(() => artifact.revision);
	let editorKey = $state(0);
	let conflicted = $state(false);

	$effect(() => {
		if (!persisted || loadedRevision === artifact.revision) return;
		editorValue = toRoutineEditorValue(persisted);
		loadedRevision = artifact.revision;
		editorKey += 1;
	});

	// The editor is always live; the actions follow what it holds. Unsaved
	// changes offer Save and Cancel, and a saved proposal offers Approve.
	const dirty = $derived(
		!!editorValue &&
			!!persisted &&
			comparable(editorValue) !== comparable(toRoutineEditorValue(persisted))
	);
	const invalid = $derived(
		!editorValue ||
			conflicted ||
			routineEditorErrors(normalizeRoutineEditorValue(editorValue)).length > 0
	);

	function save() {
		if (!editorValue || invalid) return;
		onUpdate(normalizeRoutineEditorValue(editorValue) as unknown as Record<string, unknown>);
	}

	function cancel() {
		if (persisted) editorValue = toRoutineEditorValue(persisted);
		editorKey += 1;
	}
</script>

<div class="space-y-5" aria-busy={pending || artifact.status === 'executing'}>
	{#if !persisted || !displayed}
		<Notice tone="error"
			>This {label.toLowerCase()} proposal contains data that cannot be displayed safely.</Notice
		>
	{:else if editable && editorValue}
		{#if artifact.missing_fields.length && !dirty}<Notice tone="info"
				>Complete the highlighted information before approving this routine.</Notice
			>{/if}
		{#key editorKey}
			<WeeklyRoutineEditor
				bind:value={editorValue}
				bind:conflicted
				missingFields={dirty ? [] : artifact.missing_fields}
				disabled={pending}
			/>
		{/key}
		{#if errorMessage}<p class="text-sm text-destructive" role="alert">{errorMessage}</p>{/if}
		<div class="flex flex-wrap items-center justify-center gap-2">
			{#if dirty}
				<button
					type="button"
					class="h-10 rounded-md px-3 text-sm font-medium text-muted-foreground"
					onclick={cancel}
					disabled={pending}>Cancel</button
				>
				<Button type="button" onclick={save} disabled={pending || invalid}
					>{pending ? 'Saving…' : 'Save changes'}</Button
				>
			{:else}
				{#if artifact.status === 'ready'}<Button
						type="button"
						onclick={onAccept}
						disabled={pending || conflicted}>{pending ? 'Approving…' : 'Approve'}</Button
					>{/if}
				<ArtifactActions showDeny {pending} {onDeny} {onRetry} />
			{/if}
		</div>
	{:else}
		<WeeklyRoutineEditor value={toRoutineEditorValue(displayed)} disabled />
		{#if artifact.status === 'failed'}
			<Notice tone="error">{artifact.error?.message ?? 'This routine could not be created.'}</Notice
			>
		{:else if artifact.status === 'denied'}
			<p class="text-sm text-muted-foreground">
				This proposal was denied.{#if typeof artifact.result_payload?.denial_reason === 'string'}
					{artifact.result_payload.denial_reason}{/if}
			</p>
		{:else if artifact.status === 'superseded'}
			<p class="text-sm text-muted-foreground">This proposal was replaced by a newer artifact.</p>
		{/if}
		{#if errorMessage}<p class="text-sm text-destructive" role="alert">{errorMessage}</p>{/if}
		{#if artifact.status === 'failed'}<ArtifactActions
				showRetry
				{pending}
				{onDeny}
				{onRetry}
			/>{/if}
	{/if}
</div>
