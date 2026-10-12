<script lang="ts">
	import WeeklySleepEditor, { type SleepWindow } from '#lib/calendar/WeeklySleepEditor.svelte';
	import WeeklyRoutineEditor from '#lib/calendar/WeeklyRoutineEditor.svelte';
	import type { Artifact } from '#lib/chat/types.ts';
	import {
		artifactDisplayTitle,
		artifactSummary,
		otherRoutinePayload,
		toRoutineEditorValue,
		workRoutinePayload
	} from '#lib/calendar/calendar.ts';

	let { artifact }: { artifact: Artifact } = $props();
	const payload = $derived(artifact.approved_payload ?? artifact.draft_payload);
	const routine = $derived(workRoutinePayload(payload) ?? otherRoutinePayload(payload));
	const windows = $derived(
		artifact.type === 'sleep_routine' && Array.isArray(payload.windows)
			? (payload.windows as SleepWindow[]).filter(
					(window) =>
						window.start_local_time && window.end_local_time && window.end_day_offset !== null
				)
			: []
	);
</script>

{#if windows.length}
	{#key `${artifact.id}:${artifact.revision}`}
		<WeeklySleepEditor initialWindows={windows} {windows} complete={true} externalSave readOnly />
	{/key}
{:else if routine}
	<WeeklyRoutineEditor value={toRoutineEditorValue(routine)} disabled />
{:else}
	<p class="text-sm font-medium">{artifactDisplayTitle(artifact)}</p>
	<p class="text-sm text-muted-foreground">{artifactSummary(artifact)}</p>
{/if}
