<script lang="ts">
	import type { Artifact } from '#lib/chat/types.ts';
	import {
		eventPayload,
		eventSummary,
		occurrencePayload,
		occurrenceSummary,
		removalPayload
	} from '#lib/calendar/calendar.ts';

	// How an event, one-day change or removal reads once decided, or in an earlier
	// message: a line or two, not the editor.
	let { artifact }: { artifact: Artifact } = $props();

	const lines = $derived.by(() => {
		if (artifact.type === 'event') {
			const payload = eventPayload(artifact);
			if (!payload) return [];
			const changes = payload.routine_changes.length;
			return [
				payload.location ? `${eventSummary(payload)} · ${payload.location}` : eventSummary(payload),
				...(changes
					? [`Changes ${changes} routine ${changes === 1 ? 'occurrence' : 'occurrences'} around it`]
					: [])
			];
		}
		if (artifact.type === 'occurrence_change') {
			const payload = occurrencePayload(artifact);
			return payload ? [occurrenceSummary(payload)] : [];
		}
		const payload = removalPayload(artifact);
		return payload ? [...payload.summary, ...payload.consequences] : [];
	});
</script>

<p class="text-sm font-medium">{artifact.title}</p>
{#each lines as line, index (index)}<p class="text-sm text-muted-foreground">{line}</p>{/each}
