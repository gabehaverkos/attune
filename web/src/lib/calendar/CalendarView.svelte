<script lang="ts">
	import type { RoutineRef } from '#lib/calendar/RoutineConflicts.svelte';
	import { onMount, untrack } from 'svelte';
	import CalendarContextSetupModal from '#lib/calendar/CalendarContextSetupModal.svelte';
	import RoutineScheduleModal, {
		type RoutineType
	} from '#lib/calendar/RoutineScheduleModal.svelte';
	import CalendarMonthPanel from '#lib/calendar/CalendarMonthPanel.svelte';
	import CalendarWeekPanel from '#lib/calendar/CalendarWeekPanel.svelte';
	import { BriefcaseBusiness, CalendarClock, ChevronDown, Moon, Shapes, X } from '@lucide/svelte';
	import { cachedFetch, hydrateCache } from '#lib/core/api.ts';
	import type { CalendarPayload } from '#lib/calendar/calendar.ts';
	import type { InterfaceViewProps } from '#lib/shell/interfaces.ts';

	let { onclose, link, revision }: InterfaceViewProps = $props();

	// What the link that opened the calendar asked for: a routine editor to open,
	const routineParam = untrack(() => link?.get('routine'));
	const routineIdParam = untrack(() => link?.get('routine_id') ?? null);
	// Or a day to open on, with one of its events selected.
	const dateParam = untrack(() => link?.get('date') ?? '');
	const eventIdParam = untrack(() => link?.get('event_id') ?? '');
	const initialRoutineType: RoutineType | null =
		routineParam === 'sleep' || routineParam === 'work' || routineParam === 'other'
			? routineParam
			: null;

	let calendar = $state<CalendarPayload | null>(null);
	let selectedDate = $state(/^\d{4}-\d{2}-\d{2}$/.test(dateParam) ? dateParam : '');
	let calendarError = $state('');
	let routinesMenuOpen = $state(false);
	let selectedRoutineType = $state<RoutineType | null>(initialRoutineType);
	let selectedRoutineId = $state<string | null>(routineIdParam);
	// The week by default; the month for looking further ahead. Remembered on this device.
	const VIEW_KEY = 'attune.calendar.view';
	let view = $state<'week' | 'month'>(storedView());

	function storedView(): 'week' | 'month' {
		try {
			return localStorage.getItem(VIEW_KEY) === 'month' ? 'month' : 'week';
		} catch {
			return 'week';
		}
	}

	function showView(next: 'week' | 'month') {
		if (next === view) return;
		view = next;
		try {
			localStorage.setItem(VIEW_KEY, next);
		} catch {
			// Only a convenience.
		}
		// The week may have changed what the month was handed.
		if (next === 'month') void loadCalendar();
	}

	// A routine editor's overlap links swap in the editor of the routine it names.
	function openRoutine(routine: RoutineRef) {
		selectedRoutineType = routine.kind;
		selectedRoutineId = routine.id;
	}
	const routineOptions: { type: RoutineType; label: string; icon: typeof Moon }[] = [
		{ type: 'sleep', label: 'Sleep', icon: Moon },
		{ type: 'work', label: 'Work', icon: BriefcaseBusiness },
		{ type: 'other', label: 'Other', icon: Shapes }
	];

	async function loadCalendar() {
		try {
			calendar = await cachedFetch<CalendarPayload>('/api/calendar', fetch);
			calendarError = '';
		} catch (error) {
			calendarError = error instanceof Error ? error.message : 'Unable to load calendar.';
		}
	}

	function handleRoutinesSaved(payload: CalendarPayload) {
		selectedRoutineType = null;
		calendar = payload;
		hydrateCache({ '/api/calendar': payload });
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.defaultPrevented || selectedRoutineType) return;
		if (event.key === 'Escape') return onclose();
		const target = event.target;
		const typing =
			target instanceof HTMLElement &&
			(target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
		if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
		if (event.key === 'w') showView('week');
		else if (event.key === 'm') showView('month');
	}

	onMount(() => void loadCalendar());

	// Chat changed it while it was open: show the change as it lands.
	$effect(() => {
		if (revision) untrack(() => void loadCalendar());
	});
</script>

<svelte:window onkeydown={handleKeydown} />

<div
	class="fixed inset-0 z-(--z-view) flex items-center justify-center bg-background/55 p-2 backdrop-blur-[1px] sm:p-4"
	role="presentation"
	onclick={(event) => event.currentTarget === event.target && onclose()}
>
	<div
		class="relative flex h-full w-full flex-col overflow-hidden rounded-xl border border-border bg-background shadow-2xl"
		role="dialog"
		aria-modal="true"
		aria-label="Calendar"
	>
		<!-- Top left, beside the rail the calendar was opened from. -->
		<div
			class="absolute top-[1.125rem] left-3 z-20 flex items-center gap-2 sm:top-[1.375rem] sm:left-4"
		>
			<button
				type="button"
				onclick={onclose}
				class="inline-flex size-9 items-center justify-center rounded-full border border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground"
				aria-label="Close calendar"
			>
				<X class="size-4" />
			</button>
			<div class="relative">
				<button
					type="button"
					aria-label="Choose a recurring routine"
					aria-expanded={routinesMenuOpen}
					onclick={() => (routinesMenuOpen = !routinesMenuOpen)}
					class="inline-flex h-9 items-center justify-center gap-2 rounded-full border border-border bg-background px-3 text-body font-medium hover:bg-muted"
				>
					<CalendarClock class="size-4" />
					<span>Routines</span>
					<ChevronDown class={['size-4 transition-transform', routinesMenuOpen && 'rotate-180']} />
				</button>
				{#if routinesMenuOpen}
					<div
						class="absolute top-full left-0 z-10 mt-2 w-48 rounded-xl border border-border bg-card p-1.5 shadow-xl"
						role="menu"
						aria-label="Routine types"
					>
						{#each routineOptions as option (option.type)}
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-body font-medium hover:bg-muted"
								onclick={() => {
									selectedRoutineType = option.type;
									selectedRoutineId = null;
									routinesMenuOpen = false;
								}}
							>
								<option.icon class="size-4 text-muted-foreground" />
								{option.label}
							</button>
						{/each}
					</div>
				{/if}
			</div>
		</div>

		<div
			class="absolute top-[1.125rem] right-3 z-20 flex h-9 items-center rounded-full border border-border bg-background p-0.5 sm:top-[1.375rem] sm:right-4"
			role="tablist"
			aria-label="Calendar view"
		>
			{#each [{ key: 'week', label: 'Week', shortcut: 'W' }, { key: 'month', label: 'Month', shortcut: 'M' }] as const as option (option.key)}
				<button
					type="button"
					role="tab"
					aria-selected={view === option.key}
					title={`${option.label} (${option.shortcut})`}
					class={[
						'h-8 rounded-full px-3 text-body font-medium transition-colors',
						view === option.key
							? 'bg-muted text-foreground'
							: 'text-muted-foreground hover:text-foreground'
					]}
					onclick={() => showView(option.key)}>{option.label}</button
				>
			{/each}
		</div>

		<div class="min-h-0 flex-1 overflow-hidden p-3 sm:p-4">
			{#if calendarError}
				<p class="mb-2 text-center text-body text-destructive" role="alert">{calendarError}</p>
			{/if}
			{#if view === 'week'}
				<CalendarWeekPanel
					initialDate={selectedDate}
					initialBlockId={eventIdParam}
					{revision}
					onManageRoutine={openRoutine}
				/>
			{:else if calendar}
				<div class="h-full min-h-0">
					<CalendarMonthPanel
						days={calendar.days}
						months={calendar.months}
						{selectedDate}
						onSelectDate={(date) => (selectedDate = date)}
						onManageRoutine={openRoutine}
						initialBlockId={eventIdParam}
					/>
				</div>
			{:else if !calendarError}
				<div class="p-6 text-body text-muted-foreground">Loading calendar…</div>
			{/if}
		</div>
	</div>
</div>

{#if selectedRoutineType === 'sleep'}
	<CalendarContextSetupModal
		onClose={() => (selectedRoutineType = null)}
		onSaved={handleRoutinesSaved}
		onOpenRoutine={openRoutine}
	/>
{:else if selectedRoutineType}
	{#key `${selectedRoutineType}:${selectedRoutineId}`}
		<RoutineScheduleModal
			routineType={selectedRoutineType}
			routineId={selectedRoutineId}
			onClose={() => (selectedRoutineType = null)}
			onSaved={handleRoutinesSaved}
			onOpenRoutine={openRoutine}
		/>
	{/key}
{/if}
