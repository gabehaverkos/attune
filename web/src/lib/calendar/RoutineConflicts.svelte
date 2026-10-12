<script lang="ts" module>
	import type { CalendarRoutine } from '#lib/calendar/calendar.ts';

	/** What a link to a routine needs: which one, and its kind (which editor opens it). */
	export type RoutineRef = Pick<CalendarRoutine, 'id' | 'kind'>;

	/** Something a routine or event overlaps: a routine, or one of its own patterns. */
	export type RoutineConflict = { label: string; routine: RoutineRef | null };

	export function routineLink(routine: RoutineRef) {
		return `/?calendar=open&routine=${routine.kind}&routine_id=${routine.id}`;
	}
</script>

<script lang="ts">
	// An editor's one status line: what the schedule overlaps, each clash a way to
	// what it clashes with, or the editor's ordinary summary when nothing does. It
	// holds a line that is always there, so clashes never shift the card. Red when
	// the overlap stops saving (routines), amber when it's only a warning (events).
	let {
		conflicts,
		fallback,
		onOpenRoutine,
		tone = 'error',
		class: className = ''
	}: {
		conflicts: RoutineConflict[];
		fallback: string;
		/** Inside the calendar, opening a routine swaps its editor in; elsewhere it's a link to it. */
		onOpenRoutine?: (routine: RoutineRef) => void;
		tone?: 'error' | 'warning';
		class?: string;
	} = $props();
</script>

<!-- eslint-disable svelte/no-navigation-without-resolve -- routineLink builds a query on the root page -->
<!-- The summary always holds the line's place; the overlaps lie over it and wrap
     downward into the space below, so however many there are, nothing moves. With
     no summary there is no line to hold, and the overlaps take their own room. -->
<div class={['relative text-body', className]} aria-live="polite">
	{#if fallback}
		<p class={['truncate text-muted-foreground', conflicts.length && 'invisible']}>{fallback}</p>
	{/if}
	{#if conflicts.length}
		<p
			class={[
				fallback && 'absolute inset-x-0 top-0',
				'text-balance',
				tone === 'warning' ? 'text-warning' : 'text-destructive'
			]}
		>
			Overlaps {#each conflicts as conflict, index (index)}{index
					? ', '
					: ''}{#if conflict.routine && onOpenRoutine}{@const routine = conflict.routine}<button
						type="button"
						class="underline underline-offset-2 hover:opacity-80"
						onclick={() => onOpenRoutine(routine)}>{conflict.label}</button
					>{:else if conflict.routine}<a
						class="underline underline-offset-2 hover:opacity-80"
						href={routineLink(conflict.routine)}>{conflict.label}</a
					>{:else}{conflict.label}{/if}{/each}
		</p>
	{/if}
</div>
