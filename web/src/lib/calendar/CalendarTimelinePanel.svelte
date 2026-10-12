<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';

	import { untrack } from 'svelte';
	import { PenLine, Settings2, Trash2 } from '@lucide/svelte';
	import { apiRequest } from '#lib/core/api.ts';
	import {
		addDays,
		blockEdgeColor,
		type CalendarDay,
		type CalendarEvent,
		type CalendarScheduleBlock,
		type EventEditorValue,
		eventInputKey,
		fromEvent,
		minutesOnDay,
		newEventValue,
		toEventInput
	} from '#lib/calendar/calendar.ts';
	import type { RoutineRef } from '#lib/calendar/RoutineConflicts.svelte';
	import { formatClock } from '#lib/calendar/TimeTrack.svelte';
	import EventEditor from '#lib/calendar/EventEditor.svelte';
	import OccurrenceEditor, { type OccurrenceValue } from '#lib/calendar/OccurrenceEditor.svelte';

	// The open day, beside the grid it is drawn on: what is on it, and whichever
	// of those is selected, an event with its details or one day of a routine.
	let {
		days,
		selectedDate,
		onChanged,
		onManageRoutine,
		onOpenDay,
		initialBlockId = '',
		initialSpan = null,
		newEvent = $bindable(null),
		preview = $bindable(null)
	}: {
		days: CalendarDay[];
		selectedDate: string;
		/** After a save: reload these dates' months so the timeline shows it. */
		onChanged: (dates: string[]) => Promise<void>;
		onManageRoutine: (routine: RoutineRef) => void;
		/** Open another day with one of its blocks selected, e.g. one clicked past midnight. */
		onOpenDay: (date: string, blockId: string) => void;
		initialBlockId?: string;
		/** A span to start a new event on, e.g. one drawn on the week. */
		initialSpan?: { start: number; end: number } | null;
		/**
		 * The day's list and its timeline; the week leaves both out, showing the day
		 * itself, with the event being added or changed drawn on its own grid.
		 */
		/** The event being added, for the week to draw and to drag. */
		newEvent?: EventEditorValue | null;
		/** The event being edited here, as it stands unsaved, for the week to draw and to drag. */
		preview?: {
			eventId: string;
			title: string;
			date: string;
			start: number;
			end: number;
		} | null;
	} = $props();

	let selectedBlockId = $state(untrack(() => initialBlockId));
	untrack(() => {
		newEvent = initialSpan ? newEventValue(selectedDate, initialSpan.start, initialSpan.end) : null;
	});
	let editing = $state<{
		id: string;
		value: EventEditorValue;
		original: string;
		updatedAt: string;
	} | null>(null);
	let occurrence = $state<{
		windowId: string;
		date: string;
		title: string;
		routine: RoutineRef | null;
		value: OccurrenceValue;
		original: string;
		note: string;
		originalNote: string;
		changedBy: CalendarScheduleBlock['changed_by'];
	} | null>(null);
	// On the week, a selected event is only shown until Edit is chosen.
	let editMode = $state(false);
	const editable = $derived(editMode);
	let saving = $state(false);
	let deleting = $state(false);
	let interactionError = $state('');
	let shownDate = untrack(() => selectedDate);

	const selectedDay = $derived(days.find((day) => day.date === selectedDate));
	const blocks = $derived(
		[...(selectedDay?.blocks ?? [])].sort((a, b) => a.start.localeCompare(b.start))
	);
	const selectedBlock = $derived(
		selectedBlockId ? blocks.find((block) => sameBlock(block.id, selectedBlockId)) : undefined
	);
	const eventDirty = $derived(
		editing !== null && eventInputKey(editing.value) !== editing.original
	);
	const occurrenceTimesDirty = $derived(
		occurrence !== null && JSON.stringify(occurrence.value) !== occurrence.original
	);
	const occurrenceDirty = $derived(
		occurrenceTimesDirty || (occurrence !== null && occurrence.note !== occurrence.originalNote)
	);

	$effect(() => {
		preview =
			editing && editMode
				? {
						eventId: editing.id,
						title: editing.value.title,
						date: editing.value.date,
						start: editing.value.start,
						end: editing.value.end
					}
				: null;
	});

	/** Where the week dragged the event being added or edited. */
	export function setTimes(times: { date: string; start: number; end: number }) {
		if (newEvent) newEvent = { ...newEvent, ...times };
		else if (editing && editable) editing.value = { ...editing.value, ...times };
	}

	// A different day starts with nothing selected.
	$effect(() => {
		const date = selectedDate;
		untrack(() => {
			if (date === shownDate) return;
			shownDate = date;
			select('');
		});
	});

	// The selection decides the editor: an event loads, a routine's day opens as it stands.
	$effect(() => {
		const block = selectedBlock;
		untrack(() => {
			if (block?.event_id) {
				occurrence = null;
				if (editing?.id !== block.event_id) {
					editing = null;
					editMode = false;
					void loadEvent(block.event_id);
				}
			} else if (block?.window_id && block.occurrence_date) {
				editing = null;
				occurrence = occurrenceOf(block);
			} else {
				editing = null;
				occurrence = null;
			}
		});
	});

	/** An overnight block shows on both days under ids that differ only in the day. */
	function sameBlock(a: string, b: string) {
		return a.replace(/:day:[^:]+$/, '') === b.replace(/:day:[^:]+$/, '');
	}

	function select(blockId: string) {
		selectedBlockId = blockId;
		newEvent = null;
		interactionError = '';
	}

	function agendaTime(block: CalendarScheduleBlock) {
		return `${formatClock(minutesOnDay(block.start, selectedDate))} – ${formatClock(minutesOnDay(block.end, selectedDate))}`;
	}

	function errorText(error: unknown, fallback: string) {
		return error instanceof Error ? error.message : fallback;
	}

	async function loadEvent(eventId: string) {
		try {
			const event = await apiRequest<CalendarEvent>(`/api/calendar/events/${eventId}`);
			if (selectedBlock?.event_id !== eventId) return;
			const value = fromEvent(event);
			editing = { id: eventId, value, original: eventInputKey(value), updatedAt: event.updated_at };
		} catch (error) {
			interactionError = errorText(error, 'Unable to load this event.');
		}
	}

	async function createEvent() {
		if (!newEvent) return;
		if (!newEvent.title.trim()) {
			interactionError = 'Give the event a title.';
			return;
		}
		saving = true;
		interactionError = '';
		const date = newEvent.date;
		try {
			const event = await apiRequest<CalendarEvent>('/api/calendar/events', {
				method: 'POST',
				body: toEventInput(newEvent),
				invalidate: ['/api/calendar']
			});
			newEvent = null;
			await onChanged([date, addDays(date, 1)]);
			// On the week the event may be on another day than the panel was: go to it.
			onOpenDay(date, event.id);
		} catch (error) {
			interactionError = errorText(error, 'Unable to add this event.');
		} finally {
			saving = false;
		}
	}

	async function saveEvent() {
		if (!editing) return;
		if (!editing.value.title.trim()) {
			interactionError = 'Give the event a title.';
			return;
		}
		saving = true;
		interactionError = '';
		const { id, value, updatedAt } = editing;
		try {
			await apiRequest<CalendarEvent>(`/api/calendar/events/${id}`, {
				method: 'PUT',
				body: { ...toEventInput(value), expected_updated_at: updatedAt },
				invalidate: ['/api/calendar']
			});
			editing = null;
			editMode = false;
			// The reload hands the timeline a new block, which loads the saved event again.
			await onChanged([selectedDate, addDays(selectedDate, 1), value.date, addDays(value.date, 1)]);
			if (value.date !== selectedDate) onOpenDay(value.date, id);
		} catch (error) {
			interactionError = errorText(error, 'Unable to save this event.');
		} finally {
			saving = false;
		}
	}

	async function deleteEvent() {
		if (!editing) return;
		deleting = true;
		interactionError = '';
		const { id, updatedAt } = editing;
		const date = selectedDate;
		try {
			await apiRequest(`/api/calendar/events/${id}`, {
				method: 'DELETE',
				body: { expected_updated_at: updatedAt },
				invalidate: ['/api/calendar']
			});
			editing = null;
			select('');
			await onChanged([addDays(date, -1), date, addDays(date, 1)]);
		} catch (error) {
			interactionError = errorText(error, 'Unable to delete this event.');
		} finally {
			deleting = false;
		}
	}

	/** One day of a routine as it stands, from every part of it the calendar shows. */
	function occurrenceOf(block: CalendarScheduleBlock) {
		const windowId = block.window_id ?? '';
		const date = block.occurrence_date ?? '';
		const parts = days
			.flatMap((day) => day.blocks)
			.filter(
				(candidate) => candidate.window_id === windowId && candidate.occurrence_date === date
			);
		const start = Math.min(...parts.map((part) => minutesOnDay(part.start, date)));
		const end = Math.max(...parts.map((part) => minutesOnDay(part.end, date)));
		const value = { skip: false, start, end };
		return {
			windowId,
			date,
			title: block.title,
			routine: block.routine_id
				? { id: block.routine_id, kind: block.kind as RoutineRef['kind'] }
				: null,
			value,
			original: JSON.stringify(value),
			note: block.description ?? '',
			originalNote: block.description ?? '',
			changedBy: block.changed_by
		};
	}

	function resetOccurrence() {
		if (!occurrence) return;
		occurrence.value = JSON.parse(occurrence.original);
		occurrence.note = occurrence.originalNote;
	}

	async function saveOccurrence() {
		if (!occurrence) return;
		saving = true;
		interactionError = '';
		const { windowId, date, value, note, originalNote } = occurrence;
		try {
			// The times and the note save apart: a note alone leaves the day unchanged.
			if (occurrenceTimesDirty) {
				await apiRequest(`/api/calendar/occurrences/${windowId}/${date}`, {
					method: 'PUT',
					body: value.skip
						? { operation: 'skip' }
						: {
								operation: 'custom',
								start_offset_minutes: value.start,
								end_offset_minutes: value.end
							},
					invalidate: ['/api/calendar']
				});
			}
			if (note !== originalNote) {
				await apiRequest(`/api/calendar/occurrences/${windowId}/${date}/note`, {
					method: 'PUT',
					body: { description: note },
					invalidate: ['/api/calendar']
				});
			}
			await onChanged([date, addDays(date, 1)]);
		} catch (error) {
			interactionError = errorText(error, 'Unable to change this day.');
		} finally {
			saving = false;
		}
	}

	async function restoreOccurrence() {
		if (!occurrence) return;
		saving = true;
		interactionError = '';
		const { windowId, date } = occurrence;
		try {
			await apiRequest(`/api/calendar/occurrences/${windowId}/${date}`, {
				method: 'DELETE',
				invalidate: ['/api/calendar']
			});
			await onChanged([date, addDays(date, 1)]);
		} catch (error) {
			interactionError = errorText(error, 'Unable to restore this day.');
		} finally {
			saving = false;
		}
	}

	function occurrenceStatus() {
		if (occurrenceTimesDirty) return 'Just this day; the routine itself stays the same.';
		if (occurrence?.changedBy === 'direct') return 'Changed for this day.';
		if (occurrence?.changedBy === 'event') return 'Changed around an event.';
		return 'Change its times to change just this day.';
	}
</script>

<div class="grid h-full min-h-0 grid-cols-1">
	<div class="flex min-h-0 flex-col overflow-y-auto p-4 text-left" aria-label="Day timeline">
		<div class="flex flex-1 flex-col">
			{#if newEvent}
				<EventEditor
					layout="day"
					bind:value={newEvent}
					disabled={saving}
					titleMissing={interactionError !== '' && !newEvent.title.trim()}
					focusTitle
					onsubmit={createEvent}
					onOpenRoutine={onManageRoutine}
				/>
			{:else if editing}
				<EventEditor
					layout="day"
					bind:value={editing.value}
					eventId={editing.id}
					disabled={saving || deleting || !editable}
					titleMissing={!editing.value.title.trim()}
					onsubmit={() => eventDirty && void saveEvent()}
					onOpenRoutine={onManageRoutine}
				/>
			{:else if occurrence}
				<OccurrenceEditor
					layout="day"
					bind:value={occurrence.value}
					bind:note={occurrence.note}
					date={occurrence.date}
					windowId={occurrence.windowId}
					title={occurrence.title}
					status={occurrenceStatus()}
					disabled={saving}
				/>
			{:else}
				{#if !selectedBlock && blocks.length}
					<!-- On the week, the day itself: what is on it, each a way in. -->
					<ul class="mb-4 space-y-1" aria-label="On this day">
						{#each blocks as block (block.id)}
							<li>
								<button
									type="button"
									class="w-full rounded-md border-l-[3px] px-2 py-1.5 text-left transition-colors hover:bg-muted/50"
									style:border-left-color={blockEdgeColor(block.kind)}
									onclick={() => onOpenDay(selectedDate, block.id)}
								>
									<p class="truncate text-body font-medium">{block.title}</p>
									<p class="text-sm tabular-nums text-muted-foreground">
										{agendaTime(block)}{block.changed_by ? ' · changed' : ''}
									</p>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
				<p class="text-body text-muted-foreground">
					{#if selectedBlock?.event_id}
						Loading the event…
					{:else if !blocks.length}
						Nothing on this day. Drag across open time to add an event.
					{:else}
						Select something to see it, or drag across open time to add an event.
					{/if}
				</p>
			{/if}
			{#if interactionError}<p class="mt-3 text-body text-destructive" role="alert">
					{interactionError}
				</p>{/if}
		</div>

		{#if newEvent || editing || occurrence}
			<div class="mt-5 flex flex-wrap items-center gap-3 border-t border-border/70 pt-4">
				{#if newEvent}
					<div class="flex-1"></div>
					<Button variant="secondary" onclick={() => (newEvent = null)} disabled={saving}
						>Cancel</Button
					>
					<Button onclick={createEvent} disabled={saving || !newEvent.title.trim()}
						>{saving ? 'Adding…' : 'Add event'}</Button
					>
				{:else if editing && selectedBlock}
					<div class="flex-1"></div>
					<button
						type="button"
						class="inline-flex h-10 items-center gap-2 rounded-md px-3 text-body font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
						disabled={saving || deleting}
						onclick={deleteEvent}
						><Trash2 class="size-4" /> {deleting ? 'Deleting…' : 'Delete'}</button
					>
					{#if !editable}
						<Button
							variant="secondary"
							class="inline-flex items-center gap-2"
							onclick={() => (editMode = true)}
							disabled={deleting}><PenLine class="size-4" /> Edit</Button
						>
					{:else if eventDirty || editMode}
						<Button
							variant="secondary"
							onclick={() => {
								editMode = false;
								if (editing) void loadEvent(editing.id);
							}}
							disabled={saving}>{editMode ? 'Cancel' : 'Reset'}</Button
						>
						<Button
							onclick={saveEvent}
							disabled={saving || deleting || !eventDirty || !editing.value.title.trim()}
							>{saving ? 'Saving…' : 'Save changes'}</Button
						>
					{/if}
				{:else if occurrence}
					<div class="flex-1">
						{#if occurrence.routine}
							{@const routine = occurrence.routine}
							<Button
								variant="ghost"
								class="inline-flex items-center gap-2"
								onclick={() => onManageRoutine(routine)}
							>
								<Settings2 class="size-4" /> Edit the routine
							</Button>
						{/if}
					</div>
					{#if occurrence.changedBy === 'direct' && !occurrenceDirty}
						<Button variant="ghost" onclick={restoreOccurrence} disabled={saving}
							>Back to normal</Button
						>
					{/if}
					{#if occurrenceDirty}
						<Button variant="secondary" onclick={resetOccurrence} disabled={saving}>Reset</Button>
						<Button onclick={saveOccurrence} disabled={saving}
							>{saving ? 'Saving…' : 'Save this day'}</Button
						>
					{/if}
				{/if}
			</div>
		{/if}
	</div>
</div>
