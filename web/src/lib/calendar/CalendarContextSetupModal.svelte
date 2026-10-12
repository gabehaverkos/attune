<script lang="ts">
	import type { RoutineRef } from '#lib/calendar/RoutineConflicts.svelte';
	import Button from '#lib/core/ui/Button.svelte';

	import { onMount } from 'svelte';
	import { Trash2 } from '@lucide/svelte';
	import { apiRequest, cachedFetch } from '#lib/core/api.ts';
	import type { CalendarPayload } from '#lib/calendar/calendar.ts';
	import WeeklySleepEditor, { type SleepWindow } from '#lib/calendar/WeeklySleepEditor.svelte';

	let {
		onClose,
		onSaved,
		onOpenRoutine
	}: {
		onClose: () => void;
		onSaved: (payload: CalendarPayload) => void;
		onOpenRoutine?: (routine: RoutineRef) => void;
	} = $props();

	type SleepRoutine = {
		id: string;
		status: 'active' | 'archived';
		archive_reason: string | null;
		name: string;
		location: string | null;
		windows: SleepWindow[];
	};

	let routine = $state<SleepRoutine | null>(null);
	let sleepWindows = $state<SleepWindow[]>([]);
	let sleepComplete = $state(false);
	let loading = $state(true);
	let hasExistingRoutine = $state(false);
	let saving = $state(false);
	let deleting = $state(false);
	let errorMessage = $state('');
	let initialWindowsSnapshot = $state('[]');
	let initialWindows = $state<SleepWindow[]>([]);
	let editorVersion = $state(0);

	function cloneWindows(windows: SleepWindow[]): SleepWindow[] {
		return windows.map((window) => ({
			...window,
			weekdays: [...window.weekdays]
		}));
	}

	function windowsSnapshot(windows: SleepWindow[]) {
		return JSON.stringify(
			windows.map((window) => ({
				id: window.id ?? null,
				weekdays: [...window.weekdays].sort((left, right) => left - right),
				start_local_time: window.start_local_time.slice(0, 5),
				end_local_time: window.end_local_time.slice(0, 5),
				end_day_offset: window.end_day_offset
			}))
		);
	}

	const hasChanges = $derived(windowsSnapshot(sleepWindows) !== initialWindowsSnapshot);

	onMount(async () => {
		try {
			const calendar = await cachedFetch<CalendarPayload>('/api/calendar', fetch);
			routine = calendar.routines.sleep[0] ?? null;
			hasExistingRoutine = routine !== null;
			sleepWindows = routine?.windows ?? [
				{
					weekdays: [6, 0, 1, 2, 3],
					start_local_time: '23:00',
					end_local_time: '07:00',
					end_day_offset: 1
				}
			];
			initialWindows = cloneWindows(sleepWindows);
			initialWindowsSnapshot = windowsSnapshot(sleepWindows);
			sleepComplete = sleepWindows.length > 0;
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Unable to load your sleep routine.';
		} finally {
			loading = false;
		}
	});

	function resetChanges() {
		sleepWindows = cloneWindows(initialWindows);
		sleepComplete = sleepWindows.length > 0;
		errorMessage = '';
		editorVersion += 1;
	}

	async function deleteRoutine() {
		if (!window.confirm('Delete this sleep routine?')) return;
		deleting = true;
		errorMessage = '';
		try {
			const payload = await apiRequest<CalendarPayload>('/api/calendar/routines/sleep', {
				method: 'DELETE',
				invalidate: ['/api/calendar']
			});
			onSaved(payload);
		} catch (error) {
			errorMessage =
				error instanceof Error ? error.message : 'Unable to delete your sleep routine.';
		} finally {
			deleting = false;
		}
	}

	async function saveRoutine() {
		errorMessage = '';
		if (!sleepComplete || sleepWindows.length === 0) {
			errorMessage = 'Confirm your sleep schedule before saving.';
			return;
		}
		saving = true;
		try {
			const payload = await apiRequest<CalendarPayload>('/api/calendar/routines/sleep', {
				method: 'PUT',
				body: {
					id: routine?.id,
					kind: 'sleep',
					name: 'Sleep',
					location: null,
					windows: sleepWindows
				},
				invalidate: ['/api/calendar']
			});
			onSaved(payload);
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Unable to save your sleep routine.';
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
		aria-label="Cancel sleep schedule changes"
		onclick={onClose}
	></button>
	<div
		class="relative max-h-[95vh] w-full max-w-4xl overflow-y-auto rounded-lg border border-border bg-card p-5 shadow-lg"
		role="dialog"
		aria-modal="true"
		aria-labelledby="sleep-routine-title"
	>
		<form onsubmit={submit}>
			<h2 id="sleep-routine-title" class="sr-only">Set sleep routine</h2>
			{#if loading}
				<p class="py-12 text-center text-body text-muted-foreground">
					Loading your sleep schedule…
				</p>
			{:else}
				{#key editorVersion}
					<WeeklySleepEditor
						bind:windows={sleepWindows}
						bind:complete={sleepComplete}
						initialWindows={sleepWindows}
						{onOpenRoutine}
					/>
				{/key}
			{/if}
			{#if errorMessage}<p class="mt-4 text-body text-destructive" role="alert">
					{errorMessage}
				</p>{/if}
			<div class="mt-6 flex items-center justify-center gap-3">
				{#if hasExistingRoutine}
					<button
						type="button"
						class="inline-flex h-10 items-center gap-2 rounded-md px-3 text-body font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
						disabled={deleting || saving}
						onclick={deleteRoutine}
						><Trash2 class="size-4" />
						{deleting
							? 'Deleting…'
							: routine?.status === 'archived'
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
					<Button type="button" disabled={saving || deleting} onclick={saveRoutine}
						>{saving ? 'Restoring…' : 'Restore'}</Button
					>
				{:else}
					<Button
						type="submit"
						disabled={loading ||
							!sleepComplete ||
							(hasExistingRoutine && !hasChanges) ||
							saving ||
							deleting}>{saving ? 'Saving…' : 'Save'}</Button
					>
				{/if}
			</div>
		</form>
	</div>
</div>
