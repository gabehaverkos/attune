<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';
	import TextLink from '#lib/core/ui/TextLink.svelte';

	import { untrack } from 'svelte';
	import { useInterfaceEnabled } from '#lib/shell/interfaces.ts';
	import { KEY } from '#lib/calendar/interface.ts';
	import { CheckCircle2, LoaderCircle, Pencil } from '@lucide/svelte';
	import WeeklySleepEditor, { type SleepWindow } from '#lib/calendar/WeeklySleepEditor.svelte';
	import type { Artifact } from '#lib/chat/types.ts';
	import ArtifactActions from '#lib/chat/cards/ArtifactActions.svelte';
	import {
		formatLocalTime,
		formatRoutineDays,
		type SleepRoutineArtifactPayload
	} from '#lib/calendar/calendar.ts';

	// The calendar link is dead weight once the interface is off.
	const calendarEnabled = useInterfaceEnabled(KEY);

	let {
		artifact,
		pending,
		errorMessage,
		onUpdate,
		onAccept,
		onDeny,
		onRetry
	}: {
		artifact: Artifact;
		pending: boolean;
		errorMessage: string;
		onUpdate: (payload: Record<string, unknown>) => void;
		onAccept: () => void;
		onDeny: (reason: string | null) => void;
		onRetry: () => void;
	} = $props();

	function payload(value: Record<string, unknown> | null): SleepRoutineArtifactPayload | null {
		if (!value || value.kind !== 'sleep' || !Array.isArray(value.windows)) return null;
		return value as unknown as SleepRoutineArtifactPayload;
	}

	function sleepWindows(value: SleepRoutineArtifactPayload | null): SleepWindow[] {
		if (!value) return [];
		return value.windows
			.filter(
				(window) =>
					window.start_local_time && window.end_local_time && window.end_day_offset !== null
			)
			.map((window) => ({
				id: window.id,
				weekdays: [...window.weekdays],
				start_local_time: window.start_local_time!,
				end_local_time: window.end_local_time!,
				end_day_offset: window.end_day_offset!
			}));
	}

	const initialArtifact = untrack(() => artifact);
	const initialPayload = payload(initialArtifact.draft_payload);
	const persisted = $derived(payload(artifact.draft_payload));
	const displayed = $derived(payload(artifact.approved_payload ?? artifact.draft_payload));
	let windows = $state<SleepWindow[]>(sleepWindows(initialPayload));
	let complete = $state(false);
	let editing = $state(initialArtifact.status === 'draft');
	let loadedRevision = $state(initialArtifact.revision);
	let editorKey = $state(0);

	$effect(() => {
		if (!persisted || loadedRevision === artifact.revision) return;
		windows = sleepWindows(persisted);
		loadedRevision = artifact.revision;
		editorKey += 1;
		editing = artifact.status === 'draft';
	});

	function save() {
		if (!persisted || !complete) return;
		onUpdate({ ...persisted, windows });
	}

	function cancelEdit() {
		windows = sleepWindows(persisted);
		editorKey += 1;
		editing = false;
	}
</script>

<div class="space-y-5" aria-busy={pending || artifact.status === 'executing'}>
	{#if !persisted || !displayed}
		<Notice tone="error"
			>This sleep-routine proposal contains data that cannot be displayed safely.</Notice
		>
	{:else if artifact.status === 'draft' || artifact.status === 'ready'}
		{#if artifact.missing_fields.length}<Notice tone="info"
				>Complete the sleep schedule before approving it.</Notice
			>{/if}
		<div class:pointer-events-none={!editing} class:opacity-80={!editing}>
			{#key editorKey}
				<WeeklySleepEditor bind:windows bind:complete initialWindows={windows} externalSave />
			{/key}
		</div>
		{#if errorMessage}<p class="text-sm text-destructive" role="alert">{errorMessage}</p>{/if}
		<div class="flex flex-wrap items-center justify-center gap-2">
			{#if editing}
				<Button type="button" onclick={save} disabled={pending || !complete}
					>{pending ? 'Saving…' : 'Save changes'}</Button
				>
				<button
					type="button"
					class="h-10 rounded-md px-3 text-sm font-medium text-muted-foreground"
					onclick={cancelEdit}
					disabled={pending}>Cancel</button
				>
			{:else}
				{#if artifact.status === 'ready'}<Button
						type="button"
						onclick={onAccept}
						disabled={pending || !complete}>{pending ? 'Approving…' : 'Approve'}</Button
					>{/if}
				<button
					type="button"
					class="inline-flex h-10 items-center gap-2 rounded-md border border-border px-3 text-sm"
					onclick={() => (editing = true)}
					disabled={pending}><Pencil class="size-4" /> Edit</button
				>
				<ArtifactActions showDeny {pending} {onDeny} {onRetry} />
			{/if}
		</div>
	{:else}
		<div class="rounded-lg border border-border bg-background/55 p-4">
			<p class="font-semibold">{displayed.name || 'Sleep schedule'}</p>
			{#each displayed.windows as window, index (index)}<p
					class="mt-2 text-sm text-muted-foreground"
				>
					{formatRoutineDays(window.weekdays)} · {formatLocalTime(
						window.start_local_time
					)}–{formatLocalTime(window.end_local_time)}
				</p>{/each}
		</div>
		{#if artifact.status === 'executing'}
			<p class="flex items-center gap-2 text-sm text-muted-foreground">
				<LoaderCircle class="size-4 animate-spin" /> Updating your sleep schedule…
			</p>
		{:else if artifact.status === 'completed'}
			<div
				class="flex items-center justify-between gap-3 rounded-lg border border-primary/20 bg-accent/40 p-3"
			>
				<p class="flex items-center gap-2 text-sm font-medium">
					<CheckCircle2 class="size-4" /> Sleep schedule updated
				</p>
				{#if calendarEnabled() && typeof artifact.result_payload?.href === 'string'}<TextLink
						href={artifact.result_payload.href === '/calendar'
							? '/?calendar=open'
							: artifact.result_payload.href}>View in Calendar</TextLink
					>{/if}
			</div>
		{:else if artifact.status === 'failed'}
			<Notice tone="error"
				>{artifact.error?.message ?? 'This sleep schedule could not be updated.'}</Notice
			>
		{/if}
		{#if artifact.status === 'failed'}<ArtifactActions
				showRetry
				{pending}
				{onDeny}
				{onRetry}
			/>{/if}
	{/if}
</div>
