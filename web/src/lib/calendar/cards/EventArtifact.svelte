<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';

	import { untrack } from 'svelte';
	import EventEditor from '#lib/calendar/EventEditor.svelte';
	import {
		type EventEditorValue,
		eventInputKey,
		eventPayload,
		fromEventValue,
		toEventValue
	} from '#lib/calendar/calendar.ts';
	import type { ArtifactViewProps } from '#lib/chat/cards/registry.ts';
	import ArtifactActions from '#lib/chat/cards/ArtifactActions.svelte';

	// Adding an event, or changing one already on the calendar: the calendar's own
	// event editor, on the real day, with the routines it overlaps and what the
	// assistant thinks happens to them. The editor is always live; unsaved
	// changes offer Save and Cancel, and a saved proposal offers Approve.
	let {
		artifact,
		pending,
		errorMessage,
		siblings = [],
		onUpdate,
		onAccept,
		onDeny,
		onRetry
	}: ArtifactViewProps = $props();

	const persisted = $derived(eventPayload(artifact));
	const editable = $derived(artifact.status === 'draft' || artifact.status === 'ready');
	let value = $state<EventEditorValue | null>(
		untrack(() => (persisted ? toEventValue(persisted) : null))
	);
	let loadedRevision = untrack(() => artifact.revision);
	let editorKey = $state(0);

	$effect(() => {
		if (!persisted || loadedRevision === artifact.revision) return;
		value = toEventValue(persisted);
		loadedRevision = artifact.revision;
		editorKey += 1;
	});

	const dirty = $derived(
		!!value && !!persisted && eventInputKey(value) !== eventInputKey(toEventValue(persisted))
	);
	const titleMissing = $derived(!value?.title.trim());

	// The rest of the set, still proposals: shown dashed on this one's track.
	const pending_ = $derived(
		siblings.flatMap((sibling) => {
			const payload = eventPayload(sibling);
			if (!payload || sibling.id === artifact.id || !['draft', 'ready'].includes(sibling.status))
				return [];
			const sibling_value = toEventValue(payload);
			return [
				{
					key: sibling.id,
					title: payload.title || 'Event',
					date: sibling_value.date,
					start: sibling_value.start,
					end: sibling_value.end
				}
			];
		})
	);

	function save() {
		if (!value || !persisted || titleMissing) return;
		onUpdate(fromEventValue(persisted, value) as unknown as Record<string, unknown>);
	}

	function cancel() {
		if (persisted) value = toEventValue(persisted);
		editorKey += 1;
	}
</script>

<div class="space-y-5" aria-busy={pending || artifact.status === 'executing'}>
	{#if !persisted || !value}
		<Notice tone="error">This event proposal contains data that cannot be displayed safely.</Notice>
	{:else}
		{#if persisted.event_id}<p
				class="-mb-2 text-caption font-medium uppercase text-muted-foreground"
			>
				Changing an event
			</p>{/if}
		{#if editable && artifact.missing_fields.length && !dirty}<Notice tone="info"
				>Add what's missing before approving this event.</Notice
			>{/if}
		{#key editorKey}
			<EventEditor
				bind:value
				eventId={persisted.event_id}
				disabled={!editable || pending}
				pending={pending_}
				titleMissing={editable && titleMissing}
			/>
		{/key}
		{#if artifact.status === 'failed'}
			<Notice tone="error">{artifact.error?.message ?? 'This event could not be saved.'}</Notice>
		{:else if artifact.status === 'denied'}
			<p class="text-sm text-muted-foreground">This proposal was denied.</p>
		{:else if artifact.status === 'superseded'}
			<p class="text-sm text-muted-foreground">This proposal was replaced by a newer one.</p>
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
					<Button type="button" onclick={save} disabled={pending || titleMissing}
						>{pending ? 'Saving…' : 'Save changes'}</Button
					>
				{:else}
					{#if artifact.status === 'ready'}<Button
							type="button"
							onclick={onAccept}
							disabled={pending}
							>{pending
								? 'Approving…'
								: persisted.event_id
									? 'Save to calendar'
									: 'Add to calendar'}</Button
						>{/if}
					<ArtifactActions showDeny {pending} {onDeny} {onRetry} />
				{/if}
			</div>
		{:else if artifact.status === 'failed' && artifact.error?.retryable}
			<ArtifactActions showRetry {pending} {onDeny} {onRetry} />
		{/if}
	{/if}
</div>
