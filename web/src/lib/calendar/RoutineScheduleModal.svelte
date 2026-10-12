<script lang="ts">
	import type { RoutineRef } from '#lib/calendar/RoutineConflicts.svelte';
	import Button from '#lib/core/ui/Button.svelte';

	import { onMount, untrack } from 'svelte';
	import { Plus, Trash2 } from '@lucide/svelte';
	import { apiRequest, cachedFetch } from '#lib/core/api.ts';
	import {
		type CalendarPayload,
		type CalendarRoutine,
		createRoutineEditorValue,
		normalizeRoutineEditorValue,
		routineEditorErrors,
		type RoutineEditorType,
		type RoutineEditorValue
	} from '#lib/calendar/calendar.ts';
	import WeeklyRoutineEditor from '#lib/calendar/WeeklyRoutineEditor.svelte';

	export type RoutineType = RoutineEditorType;

	// The calendar's side of the work and other routine artifacts: the same
	// editor, framed the way the sleep modal frames the sleep dial.
	let {
		routineType,
		routineId = null,
		onClose,
		onSaved,
		onOpenRoutine
	}: {
		routineType: RoutineType;
		/** The routine to open with; the first of its kind otherwise. */
		routineId?: string | null;
		onClose: () => void;
		onSaved: (payload: CalendarPayload) => void;
		onOpenRoutine?: (routine: RoutineRef) => void;
	} = $props();

	const labels: Record<RoutineType, string> = {
		sleep: 'Sleep',
		work: 'Work',
		other: 'Other'
	};
	const initialType = untrack(() => routineType);
	let editorValue = $state<RoutineEditorValue>(createRoutineEditorValue(initialType));
	let initialValue = $state<RoutineEditorValue>(createRoutineEditorValue(initialType));
	let routine = $state<CalendarRoutine | null>(null);
	let routines = $state<CalendarRoutine[]>([]);
	let loading = $state(true);
	let saving = $state(false);
	let deleting = $state(false);
	let errorMessage = $state('');
	let editorVersion = $state(0);
	let conflicted = $state(false);

	function comparable(value: RoutineEditorValue) {
		return JSON.stringify(normalizeRoutineEditorValue(value));
	}

	function cloneValue(value: RoutineEditorValue): RoutineEditorValue {
		return {
			...value,
			windows: value.windows.map((window) => ({ ...window, weekdays: [...window.weekdays] }))
		};
	}

	const hasChanges = $derived(comparable(editorValue) !== comparable(initialValue));
	const invalid = $derived(
		conflicted || routineEditorErrors(normalizeRoutineEditorValue(editorValue)).length > 0
	);

	function load(value: RoutineEditorValue) {
		initialValue = cloneValue(value);
		editorValue = cloneValue(value);
		errorMessage = '';
		editorVersion += 1;
	}

	function editRoutine(existing: CalendarRoutine) {
		routine = existing;
		load({
			kind: existing.kind,
			name: existing.name,
			location: existing.location,
			effective_from: existing.effective_from,
			effective_until: existing.effective_until,
			windows: existing.windows
		});
	}

	function addRoutine() {
		routine = null;
		load(createRoutineEditorValue(routineType));
	}

	function resetChanges() {
		load(initialValue);
	}

	onMount(async () => {
		try {
			const calendar = await cachedFetch<CalendarPayload>('/api/calendar', fetch);
			routines = calendar.routines[routineType];
			const opening = routines.find((existing) => existing.id === routineId) ?? routines[0];
			if (opening) editRoutine(opening);
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Unable to load your routine.';
		} finally {
			loading = false;
		}
	});

	async function deleteRoutine() {
		if (!routine) return;
		if (!window.confirm(`Permanently delete this ${labels[routineType].toLowerCase()} routine?`))
			return;
		deleting = true;
		errorMessage = '';
		try {
			const payload = await apiRequest<CalendarPayload>(
				`/api/calendar/routines/${routineType}/${routine.id}`,
				{
					method: 'DELETE',
					invalidate: ['/api/calendar']
				}
			);
			onSaved(payload);
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Unable to delete your routine.';
		} finally {
			deleting = false;
		}
	}

	async function saveRoutine() {
		errorMessage = '';
		const errors = routineEditorErrors(normalizeRoutineEditorValue(editorValue));
		if (errors.includes('name')) {
			errorMessage = 'Give this routine a name.';
			return;
		}
		if (errors.includes('effective_until')) {
			errorMessage = 'The routine end date cannot be before its start date.';
			return;
		}
		if (errors.length) {
			errorMessage = 'Pick the days for every schedule before saving.';
			return;
		}
		saving = true;
		try {
			const normalized = normalizeRoutineEditorValue(editorValue);
			const payload = await apiRequest<CalendarPayload>(`/api/calendar/routines/${routineType}`, {
				method: 'PUT',
				body: { ...normalized, id: routine?.id },
				invalidate: ['/api/calendar']
			});
			onSaved(payload);
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Unable to save your routine.';
		} finally {
			saving = false;
		}
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		await saveRoutine();
	}
</script>

<div
	class="fixed inset-0 z-(--z-dialog) flex items-center justify-center bg-background/70 p-4 backdrop-blur-sm"
>
	<button
		type="button"
		class="absolute inset-0 cursor-default"
		aria-label={`Cancel ${labels[routineType].toLowerCase()} routine changes`}
		onclick={onClose}
	></button>
	<div
		class="relative max-h-[95vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-border bg-card p-5 shadow-lg"
		role="dialog"
		aria-modal="true"
		aria-labelledby="routine-title"
	>
		<form onsubmit={submit}>
			<h2 id="routine-title" class="sr-only">Set {labels[routineType].toLowerCase()} routine</h2>
			{#if loading}
				<p class="py-12 text-center text-body text-muted-foreground">
					Loading your {labels[routineType].toLowerCase()} routine…
				</p>
			{:else}
				{#if routines.length}
					<div
						class="mb-5 flex flex-wrap items-center gap-1.5"
						aria-label={`${labels[routineType]} routines`}
					>
						{#each routines as existing (existing.id)}
							<button
								type="button"
								aria-pressed={routine?.id === existing.id}
								class={[
									'h-8 rounded-full px-3 text-body',
									routine?.id === existing.id
										? 'bg-foreground font-medium text-background'
										: 'bg-muted/60 text-muted-foreground hover:text-foreground'
								]}
								onclick={() => editRoutine(existing)}>{existing.name}</button
							>
						{/each}
						<button
							type="button"
							aria-pressed={routine === null}
							class={[
								'inline-flex h-8 items-center gap-1 rounded-full px-3 text-body',
								routine === null
									? 'bg-foreground font-medium text-background'
									: 'text-muted-foreground hover:bg-muted hover:text-foreground'
							]}
							onclick={addRoutine}><Plus class="size-3.5" /> New</button
						>
					</div>
				{/if}
				{#key editorVersion}
					<WeeklyRoutineEditor
						bind:value={editorValue}
						bind:conflicted
						disabled={saving || deleting}
						{onOpenRoutine}
					/>
				{/key}
			{/if}
			{#if errorMessage}<p class="mt-4 text-body text-destructive" role="alert">
					{errorMessage}
				</p>{/if}
			<div class="mt-6 flex items-center justify-center gap-3">
				{#if routine}
					<button
						type="button"
						class="inline-flex h-10 items-center gap-2 rounded-md px-3 text-body font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
						disabled={deleting || saving}
						onclick={deleteRoutine}
						><Trash2 class="size-4" />
						{deleting
							? 'Deleting…'
							: routine.status === 'archived'
								? 'Permanently delete'
								: 'Delete'}</button
					>
				{/if}
				<button
					type="button"
					class="h-10 w-20 rounded-md border border-border px-4 text-body font-medium text-foreground hover:bg-muted disabled:opacity-50"
					disabled={saving || deleting}
					onclick={hasChanges ? resetChanges : onClose}>{hasChanges ? 'Reset' : 'Cancel'}</button
				>
				{#if routine?.status === 'archived'}
					<Button type="button" disabled={saving || deleting || conflicted} onclick={saveRoutine}
						>{saving ? 'Restoring…' : 'Restore'}</Button
					>
				{:else}
					<Button
						type="submit"
						disabled={loading || invalid || (routine !== null && !hasChanges) || saving || deleting}
						>{saving ? 'Saving…' : 'Save'}</Button
					>
				{/if}
			</div>
		</form>
	</div>
</div>
