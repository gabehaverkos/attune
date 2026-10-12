<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { cachedFetch } from '#lib/core/api.ts';
	import type { CalendarPayload, CalendarRoutine } from '#lib/calendar/calendar.ts';
	import RoutineConflicts, {
		type RoutineConflict,
		type RoutineRef
	} from '#lib/calendar/RoutineConflicts.svelte';
	import { AlarmClock, BedDouble, Moon, Plus, Sun, Trash2 } from '@lucide/svelte';

	export type SleepWindow = {
		id?: string | null;
		weekdays: number[];
		start_local_time: string;
		end_local_time: string;
		end_day_offset: 0 | 1;
	};

	let {
		windows = $bindable(),
		complete = $bindable(false),
		initialWindows = [],
		externalSave = false,
		readOnly = false,
		onOpenRoutine
	}: {
		windows: SleepWindow[];
		complete: boolean;
		initialWindows?: SleepWindow[];
		externalSave?: boolean;
		readOnly?: boolean;
		onOpenRoutine?: (routine: RoutineRef) => void;
	} = $props();

	const days = [
		{ short: 'M', long: 'Monday' },
		{ short: 'T', long: 'Tuesday' },
		{ short: 'W', long: 'Wednesday' },
		{ short: 'T', long: 'Thursday' },
		{ short: 'F', long: 'Friday' },
		{ short: 'S', long: 'Saturday' },
		{ short: 'S', long: 'Sunday' }
	];
	const initialExternalSave = untrack(() => externalSave);

	const tickMarks = Array.from({ length: 288 }, (_, index) => {
		const minutes = index * 5;
		const tier: 'major' | 'mid' | 'minor' =
			minutes % 30 === 0 ? 'major' : minutes % 15 === 0 ? 'mid' : 'minor';
		return { angle: (minutes / 1440) * 360, tier };
	});

	function shiftDays(weekdays: number[], offset: number) {
		return weekdays.map((day) => (day + offset + 7) % 7).sort();
	}

	function toDisplaySchedule(schedule: SleepWindow): SleepWindow {
		return {
			...schedule,
			// Persisted windows use their start day; the editor presents the day
			// the schedule applies to, which is the following day overnight.
			weekdays:
				schedule.end_day_offset === 1 ? shiftDays(schedule.weekdays, 1) : [...schedule.weekdays]
		};
	}

	function toPersistedSchedule(schedule: SleepWindow): SleepWindow {
		return {
			...schedule,
			weekdays:
				schedule.end_day_offset === 1 ? shiftDays(schedule.weekdays, -1) : [...schedule.weekdays]
		};
	}

	const existingSchedules = untrack(() =>
		initialWindows.map((schedule) =>
			initialExternalSave
				? { ...schedule, weekdays: [...schedule.weekdays] }
				: toDisplaySchedule(schedule)
		)
	);
	const initialSchedule = existingSchedules[0];
	let schedules = $state<SleepWindow[]>(existingSchedules);
	let editingIndex = $state<number | null>(initialSchedule ? 0 : null);
	let draftActive = $state(true);
	let activeDays = $state(initialSchedule ? [...initialSchedule.weekdays] : [0, 1, 2, 3, 4]);
	let bedtimeMinutes = $state(
		initialSchedule ? parseTime(initialSchedule.start_local_time) : 23 * 60
	);
	let wakeMinutes = $state(initialSchedule ? parseTime(initialSchedule.end_local_time) : 7 * 60);
	let dragging = $state<'bedtime' | 'wake' | null>(null);
	let dial: HTMLDivElement | undefined = $state();
	let errorMessage = $state('');
	let suppressDayAnimation = $state(false);
	let dayAnimationsSuppressed = $state<number[]>([]);
	let others = $state<CalendarRoutine[]>([]);
	let ready = $state(existingSchedules.length > 0);
	windows = initialExternalSave ? existingSchedules : existingSchedules.map(toPersistedSchedule);

	onMount(async () => {
		// Work and other routines are context, as on the work track: drawn around
		// the dial, and walls where the calendar would refuse the overlap. The
		// sleep routine itself is the one being replaced, so it is never context.
		if (readOnly) return;
		try {
			const calendar = await cachedFetch<CalendarPayload>('/api/calendar', fetch);
			others = [...calendar.routines.work, ...calendar.routines.other].filter(
				(routine) => routine.status === 'active'
			);
		} catch {
			others = [];
		}
	});

	/** Every context occurrence in week minutes, by the day it starts. */
	const contextIntervals = $derived(
		others.flatMap((routine) => {
			// The calendar keeps sleep clear of every other routine.
			const hard = true;
			const label = routine.name || (routine.kind === 'work' ? 'Work' : 'Routine');
			return routine.windows.flatMap((window) => {
				const start = parseTime(window.start_local_time);
				const length = duration(start, parseTime(window.end_local_time));
				return window.weekdays.map((day) => ({
					start: day * 1440 + start,
					end: day * 1440 + start + length,
					label,
					hard,
					routine
				}));
			});
		})
	);

	/** The context around the selected days' sleep, folded onto the dial's clock. */
	const shownContext = $derived.by(() => {
		// The dial is a clock, so an occurrence near this night's sleep is drawn
		// whole, and the same hours on neighbouring days merge into one arc
		// rather than being cut where a day would end.
		const minutes = { hard: new Map<number, Set<string>>(), soft: new Map<number, Set<string>>() };
		for (const wakeDay of activeDays) {
			const sleep = sleepInterval(wakeDay, bedtimeMinutes, wakeMinutes);
			const middle = (sleep.start + sleep.end) / 2;
			for (const block of contextIntervals) {
				for (const weekOffset of [-7, 0, 7]) {
					const start = block.start + weekOffset * 1440;
					const end = block.end + weekOffset * 1440;
					if (end <= middle - 720 || start >= middle + 720) continue;
					const covered = block.hard ? minutes.hard : minutes.soft;
					for (let minute = start; minute < Math.min(end, start + 1440); minute += 1) {
						const clock = ((minute % 1440) + 1440) % 1440;
						covered.set(clock, (covered.get(clock) ?? new Set()).add(block.label));
					}
				}
			}
		}
		const shown: { start: number; sweep: number; label: string; hard: boolean }[] = [];
		for (const [hard, covered] of [
			[true, minutes.hard],
			[false, minutes.soft]
		] as const) {
			if (!covered.size) continue;
			if (covered.size === 1440) {
				shown.push({
					start: 0,
					sweep: 1440,
					label: [...new Set([...covered.values()].flatMap((labels) => [...labels]))].join(', '),
					hard
				});
				continue;
			}
			// Runs begin where the minute before is uncovered, so one crossing
			// midnight stays whole.
			for (const first of covered.keys()) {
				if (covered.has((first + 1439) % 1440)) continue;
				const labels = new Set<string>();
				let sweep = 0;
				while (covered.has((first + sweep) % 1440)) {
					for (const label of covered.get((first + sweep) % 1440)!) labels.add(label);
					sweep += 1;
				}
				shown.push({ start: first, sweep, label: [...labels].join(', '), hard });
			}
		}
		return shown;
	});

	// Walls don't stop the planning, only the saving: overlaps are named below
	// the dial, and the schedule isn't complete while any remain.
	const conflicts = $derived.by(() => {
		const found = new Map<string, RoutineConflict>();
		schedules.forEach((schedule, scheduleIndex) => {
			const start = parseTime(schedule.start_local_time);
			const end = parseTime(schedule.end_local_time);
			for (const wakeDay of schedule.weekdays) {
				const own = sleepInterval(wakeDay, start, end);
				schedules.forEach((other, otherIndex) => {
					if (otherIndex <= scheduleIndex) return;
					for (const otherWakeDay of other.weekdays)
						for (const weekOffset of [-7, 0, 7]) {
							const span = sleepInterval(
								otherWakeDay + weekOffset,
								parseTime(other.start_local_time),
								parseTime(other.end_local_time)
							);
							if (own.start < span.end && span.start < own.end)
								found.set('own', { label: 'another sleep schedule', routine: null });
						}
				});
				for (const block of contextIntervals)
					for (const weekOffset of [-7, 0, 7])
						if (
							block.hard &&
							own.start < block.end + weekOffset * 1440 &&
							block.start + weekOffset * 1440 < own.end
						)
							found.set(block.routine.id, { label: block.label, routine: block.routine });
			}
		});
		return [...found.values()];
	});

	$effect(() => {
		complete = ready && conflicts.length === 0;
	});

	// Context arcs are SVG, not conic gradients: a gradient's hard stops aren't
	// anti-aliased, so its stripes and ends come out jagged.
	function ringPoint(radius: number, angle: number) {
		const radians = (angle * Math.PI) / 180;
		return `${(50 + Math.sin(radians) * radius).toFixed(3)} ${(50 - Math.cos(radians) * radius).toFixed(3)}`;
	}

	/** An annulus sector of the dial's ring, in degrees clockwise from midnight. */
	function ringSector(from: number, to: number): string {
		if (to - from >= 360)
			return `${ringSector(from, from + 180)} ${ringSector(from + 180, from + 360)}`;
		const large = to - from > 180 ? 1 : 0;
		return `M ${ringPoint(50, from)} A 50 50 0 ${large} 1 ${ringPoint(50, to)} L ${ringPoint(34, to)} A 34 34 0 ${large} 0 ${ringPoint(34, from)} Z`;
	}

	/** Wedge stripes every 3°, aligned to the dial so neighbouring arcs line up. */
	function ringStripes(from: number, to: number) {
		const wedges: string[] = [];
		for (let angle = Math.floor(from / 3) * 3; angle < to; angle += 3) {
			const start = Math.max(angle, from);
			const end = Math.min(angle + 1.5, to);
			if (end > start) wedges.push(ringSector(start, end));
		}
		return wedges.join(' ');
	}

	/** Whether a wall's arc crosses the sleep being edited, on the dial's clock. */
	function clashes(block: { start: number; sweep: number; hard: boolean }) {
		if (!block.hard || activeDays.length === 0) return false;
		return (
			(block.start - bedtimeMinutes + 1440) % 1440 < sleepMinutes ||
			(bedtimeMinutes - block.start + 1440) % 1440 < block.sweep
		);
	}

	const sleepMinutes = $derived(duration(bedtimeMinutes, wakeMinutes));
	const bedtimeAngle = $derived((bedtimeMinutes / 1440) * 360);
	const wakeAngle = $derived((wakeMinutes / 1440) * 360);
	const sleepAngle = $derived((sleepMinutes / 1440) * 360);
	const bedtimePosition = $derived(handlePosition(bedtimeAngle));
	const wakePosition = $derived(handlePosition(wakeAngle));

	function syncDraft() {
		if (activeDays.length === 0) {
			if (editingIndex !== null) {
				schedules = schedules.map((schedule, index) =>
					index === editingIndex ? { ...schedule, weekdays: [] } : schedule
				);
				windows = externalSave ? schedules : schedules.map(toPersistedSchedule);
			}
			ready = false;
			return;
		}
		const schedule: SleepWindow = {
			id: editingIndex === null ? undefined : schedules[editingIndex]?.id,
			weekdays: [...activeDays],
			start_local_time: timeValue(bedtimeMinutes),
			end_local_time: timeValue(wakeMinutes),
			end_day_offset: wakeMinutes <= bedtimeMinutes ? 1 : 0
		};
		schedules =
			editingIndex === null
				? [...schedules, schedule]
				: schedules.map((item, index) => (index === editingIndex ? schedule : item));
		if (editingIndex === null) editingIndex = schedules.length - 1;
		windows = externalSave ? schedules : schedules.map(toPersistedSchedule);
		ready = true;
	}

	function toggleDay(day: number) {
		const existingColorCount = scheduleIndicesForDay(day).length;
		const isAddingFirstColor = !activeDays.includes(day) && existingColorCount === 0;
		if (isAddingFirstColor) {
			dayAnimationsSuppressed = [...dayAnimationsSuppressed, day];
			requestAnimationFrame(() =>
				requestAnimationFrame(() => {
					dayAnimationsSuppressed = dayAnimationsSuppressed.filter((value) => value !== day);
				})
			);
		}
		const nextDays = activeDays.includes(day)
			? activeDays.filter((value) => value !== day)
			: [...activeDays, day].sort();
		activeDays = nextDays;
		ready = false;
		errorMessage = '';
		syncDraft();
	}

	function duration(start: number, end: number) {
		const result = end - start;
		return result > 0 ? result : result + 1440;
	}

	function timeValue(minutes: number) {
		return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
	}

	function parseTime(value: string) {
		const [hour, minute] = value.split(':').map(Number);
		return hour * 60 + minute;
	}

	function sleepInterval(wakeDay: number, start: number, end: number) {
		const crossesMidnight = end <= start;
		return {
			start: wakeDay * 1440 + start - (crossesMidnight ? 1440 : 0),
			end: wakeDay * 1440 + end
		};
	}

	function formatTime(minutes: number) {
		const hour = Math.floor(minutes / 60);
		const minute = minutes % 60;
		return `${hour % 12 || 12}:${String(minute).padStart(2, '0')} ${hour >= 12 ? 'PM' : 'AM'}`;
	}

	function formatDuration(minutes: number) {
		const hours = Math.floor(minutes / 60);
		const remainder = minutes % 60;
		return `${hours} hr${hours === 1 ? '' : 's'}${remainder ? ` ${remainder} min` : ''}`;
	}

	function handlePosition(angle: number) {
		const radians = (angle * Math.PI) / 180;
		return {
			left: 50 + Math.sin(radians) * 43,
			top: 50 - Math.cos(radians) * 43
		};
	}

	function minuteAtPointer(event: PointerEvent) {
		if (!dial) return 0;
		const rect = dial.getBoundingClientRect();
		const x = event.clientX - (rect.left + rect.width / 2);
		const y = event.clientY - (rect.top + rect.height / 2);
		const degrees = (Math.atan2(x, -y) * 180) / Math.PI;
		const normalized = (degrees + 360) % 360;
		return (Math.round((normalized / 360) * 288) % 288) * 5;
	}

	function startDrag(event: PointerEvent, handle: 'bedtime' | 'wake') {
		event.preventDefault();
		if (!dial) return;
		dragging = handle;
		dial.setPointerCapture(event.pointerId);
		updateDrag(event);
	}

	function updateDrag(event: PointerEvent) {
		if (!dragging) return;
		const value = minuteAtPointer(event);
		if (dragging === 'bedtime') bedtimeMinutes = value;
		else wakeMinutes = value;
		errorMessage = '';
		ready = false;
		syncDraft();
	}

	function endDrag(event: PointerEvent) {
		dragging = null;
		if (dial?.hasPointerCapture(event.pointerId)) dial.releasePointerCapture(event.pointerId);
	}

	function addSchedule() {
		activeDays = [];
		bedtimeMinutes = 23 * 60;
		wakeMinutes = 7 * 60;
		editingIndex = null;
		draftActive = true;
		ready = false;
		errorMessage = '';
		syncDraft();
	}

	function editSchedule(index: number) {
		const schedule = schedules[index];
		activeDays = [...schedule.weekdays];
		bedtimeMinutes = parseTime(schedule.start_local_time);
		wakeMinutes = parseTime(schedule.end_local_time);
		editingIndex = index;
		draftActive = true;
		ready = schedules.length > 0;
		errorMessage = '';
	}

	function removeSchedule(index: number) {
		// The removed segment no longer exists to animate alongside its siblings.
		// Let the remaining colors fill the circle immediately instead of exposing
		// a temporary transparent slice.
		suppressDayAnimation = true;
		schedules = schedules.filter((_, candidate) => candidate !== index);
		windows = externalSave ? schedules : schedules.map(toPersistedSchedule);
		ready = schedules.length > 0;
		requestAnimationFrame(() => requestAnimationFrame(() => (suppressDayAnimation = false)));
		if (schedules.length === 0) {
			addSchedule();
			return;
		}
		editSchedule(Math.min(index, schedules.length - 1));
	}

	const scheduleColors = [
		{ dot: 'bg-primary', border: 'border-primary', ring: 'ring-primary', css: 'var(--primary)' },
		{
			dot: 'bg-schedule-amber',
			border: 'border-schedule-amber',
			ring: 'ring-schedule-amber',
			css: 'var(--schedule-amber)'
		},
		{
			dot: 'bg-schedule-blue',
			border: 'border-schedule-blue',
			ring: 'ring-schedule-blue',
			css: 'var(--schedule-blue)'
		},
		{
			dot: 'bg-schedule-rose',
			border: 'border-schedule-rose',
			ring: 'ring-schedule-rose',
			css: 'var(--schedule-rose)'
		}
	] as const;

	function colorFor(index: number) {
		return scheduleColors[index % scheduleColors.length];
	}

	const selectedScheduleColor = $derived(colorFor(editingIndex ?? schedules.length).css);

	const displayRows = $derived([
		...schedules.map((schedule, index) => ({
			index,
			color: colorFor(index).css,
			bedtime: index === editingIndex ? bedtimeMinutes : parseTime(schedule.start_local_time),
			wake: index === editingIndex ? wakeMinutes : parseTime(schedule.end_local_time)
		})),
		...(editingIndex === null
			? [{ index: null, color: selectedScheduleColor, bedtime: bedtimeMinutes, wake: wakeMinutes }]
			: [])
	]);

	function scheduleArc(schedule: SleepWindow, color: string) {
		const start = parseTime(schedule.start_local_time);
		const sweep = duration(start, parseTime(schedule.end_local_time));
		return `conic-gradient(from ${(start / 1440) * 360}deg, ${color} 0deg ${(sweep / 1440) * 360}deg, transparent ${(sweep / 1440) * 360}deg 360deg)`;
	}

	function positionForTime(value: string) {
		return handlePosition((parseTime(value) / 1440) * 360);
	}

	function scheduleCoverage(schedule: SleepWindow) {
		if (activeDays.length === 0) return 'none';
		const matchingDays = activeDays.filter((day) => schedule.weekdays.includes(day)).length;
		if (matchingDays === 0) return 'none';
		return matchingDays === activeDays.length ? 'full' : 'partial';
	}

	function scheduleIndicesForDay(day: number) {
		return schedules
			.map((schedule, index) => (schedule.weekdays.includes(day) ? index : -1))
			.filter((index) => index >= 0);
	}

	function selectDay(day: number) {
		// A day may participate in multiple non-overlapping sleep windows. The
		// selected color controls which window this click edits.
		if (!draftActive) addSchedule();
		toggleDay(day);
	}

	function removeSelectedSchedule() {
		if (editingIndex !== null) removeSchedule(editingIndex);
	}

	function scheduleLabel(schedule: SleepWindow) {
		return `${formatTime(parseTime(schedule.start_local_time))}–${formatTime(parseTime(schedule.end_local_time))}`;
	}

	function dayScheduleLabel(indices: number[]) {
		if (indices.length === 0) return 'no schedule';
		return indices.map((index) => scheduleLabel(schedules[index])).join(', ');
	}

	if (existingSchedules.length === 0) syncDraft();
</script>

<div class="space-y-6">
	<fieldset>
		<legend class="sr-only">Sleep schedule patterns</legend>
		<div class="mb-2 flex min-h-7 items-center justify-center gap-1.5" aria-label="Schedule colors">
			{#each schedules, index (index)}
				<button
					type="button"
					aria-label={`${readOnly ? 'View' : 'Edit'} schedule ${index + 1}`}
					aria-pressed={editingIndex === index}
					class={[
						'size-9 rounded-full border-2 border-background shadow-sm transition',
						colorFor(index).dot,
						editingIndex === index && [
							'ring-2 ring-offset-1 ring-offset-card',
							colorFor(index).ring
						]
					]}
					onclick={() => editSchedule(index)}
				></button>
			{/each}
			{#if draftActive && editingIndex === null}
				<span
					class={[
						'size-9 rounded-full border-2 border-background shadow-sm ring-2 ring-offset-1 ring-offset-card',
						colorFor(schedules.length).dot,
						colorFor(schedules.length).ring
					]}
					aria-label="New schedule"
				></span>
			{/if}
			{#if !readOnly}
				<button
					type="button"
					aria-label="Add schedule"
					class="inline-flex size-7 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
					onclick={addSchedule}><Plus class="size-4" /></button
				>
				{#if editingIndex !== null}
					<button
						type="button"
						aria-label="Delete selected schedule"
						class="inline-flex size-7 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-destructive"
						onclick={removeSelectedSchedule}><Trash2 class="size-4" /></button
					>
				{/if}
			{/if}
		</div>
		<div inert={readOnly} class="grid grid-cols-7 gap-2 rounded-xl bg-muted/60 p-2">
			{#each days as day, index (index)}
				{@const scheduleIndices = scheduleIndicesForDay(index)}
				<button
					type="button"
					aria-label={`${day.long}, ${dayScheduleLabel(scheduleIndices)}`}
					aria-pressed={draftActive && activeDays.includes(index)}
					class={[
						'relative mx-auto inline-flex size-10 items-center justify-center overflow-hidden rounded-full border-2 [background-clip:padding-box] text-body font-semibold transition-shadow',
						scheduleIndices.length
							? 'border-background text-primary-foreground shadow-sm'
							: 'border-transparent text-muted-foreground hover:bg-background',
						draftActive && activeDays.includes(index)
							? `ring-2 ring-offset-2 ring-offset-background ${colorFor(editingIndex ?? schedules.length).ring}`
							: ''
					]}
					onclick={() => selectDay(index)}
				>
					<span class="pointer-events-none absolute inset-0 flex overflow-hidden rounded-full">
						{#each schedules as schedule, scheduleIndex (schedule.id ?? scheduleIndex)}
							{@const isActive = schedule.weekdays.includes(index)}
							<span
								class={[
									'h-full min-w-0',
									!suppressDayAnimation &&
										!dayAnimationsSuppressed.includes(index) &&
										scheduleIndices.length > 0 &&
										'transition-[width] duration-180 ease-[ease]'
								]}
								style:background={colorFor(scheduleIndex).css}
								style:transition={suppressDayAnimation ? 'none' : undefined}
								style:width={isActive ? `${100 / scheduleIndices.length}%` : '0%'}
							></span>
						{/each}
					</span>
					<span class="relative z-10">{day.short}</span>
				</button>
			{/each}
		</div>
	</fieldset>

	{#if draftActive}
		<section
			inert={readOnly}
			class="rounded-2xl border border-border bg-muted/35 p-5"
			aria-label="Bedtime and wake up"
		>
			<div class="flex flex-col gap-5 sm:flex-row sm:items-start">
				<div class="min-w-0 flex-1 space-y-1.5">
					<p
						class="flex items-center justify-center gap-2 text-[15.6px] font-semibold uppercase text-muted-foreground"
					>
						<BedDouble class="size-[18px]" /> Bedtime
					</p>
					<div class="mx-auto w-fit space-y-1.5">
						{#each displayRows as row (row.index ?? 'draft')}
							{@const previousNight = row.wake <= row.bedtime}
							<button
								type="button"
								class="flex items-start gap-2.5 rounded-lg bg-background/70 px-3 py-1.5 text-left hover:bg-background"
								onclick={() => {
									if (row.index !== null) editSchedule(row.index);
								}}
							>
								<span class="mt-1.5 size-2.5 shrink-0 rounded-full" style:background={row.color}
								></span>
								<span class="leading-tight">
									<span class="block text-[20.8px] font-semibold">{formatTime(row.bedtime)}</span>
									<span class="block text-sm text-muted-foreground"
										>{previousNight ? 'previous day' : 'same day'}</span
									>
								</span>
							</button>
						{/each}
					</div>
				</div>

				<div
					class="mx-auto aspect-square w-full max-w-[26rem] shrink-0 rounded-full bg-foreground p-7 shadow-xl dark:bg-background dark:ring-1 dark:ring-border sm:self-center"
				>
					<div
						bind:this={dial}
						role="group"
						aria-label="24-hour sleep schedule dial"
						class="relative size-full touch-none overflow-hidden rounded-full"
						style={`background: conic-gradient(from ${bedtimeAngle}deg, ${selectedScheduleColor} 0deg ${sleepAngle}deg, var(--muted) ${sleepAngle}deg 360deg);`}
						onpointermove={updateDrag}
						onpointerup={endDrag}
						onpointercancel={endDrag}
					>
						<svg
							class="pointer-events-none absolute inset-0 size-full"
							viewBox="0 0 100 100"
							aria-hidden="true"
						>
							{#each shownContext as block (`${block.start}:${block.sweep}:${block.label}:${block.hard}`)}
								{@const from = (block.start / 1440) * 360}
								{@const to = from + (block.sweep / 1440) * 360}
								<path
									d={block.hard ? ringStripes(from, to) : ringSector(from, to)}
									style:fill={block.hard
										? `color-mix(in oklch, ${clashes(block) ? 'var(--destructive) 55%' : 'var(--foreground) 16%'}, transparent)`
										: 'color-mix(in oklch, var(--foreground) 9%, transparent)'}
								/>
							{/each}
						</svg>
						{#each schedules as schedule, scheduleIndex (schedule.id ?? scheduleIndex)}
							{@const coverage = scheduleCoverage(schedule)}
							{#if editingIndex === scheduleIndex || coverage !== 'none'}
								<div
									class="pointer-events-none absolute inset-[-1px]"
									style:background={scheduleArc(schedule, colorFor(scheduleIndex).css)}
									style:opacity={editingIndex === scheduleIndex ? 1 : 0.3}
									style:z-index={editingIndex === scheduleIndex ? 2 : 1}
								></div>
								{#if editingIndex !== scheduleIndex}
									{@const inactiveBedPosition = positionForTime(schedule.start_local_time)}
									{@const inactiveWakePosition = positionForTime(schedule.end_local_time)}
									<div
										class="pointer-events-none absolute z-[4] grid size-14 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full text-foreground/60"
										style={`left:${inactiveBedPosition.left}%; top:${inactiveBedPosition.top}%; background:color-mix(in oklch, ${colorFor(scheduleIndex).css} 30%, var(--muted));`}
									>
										<BedDouble class="size-6" />
									</div>
									<div
										class="pointer-events-none absolute z-[4] grid size-14 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full text-foreground/60"
										style={`left:${inactiveWakePosition.left}%; top:${inactiveWakePosition.top}%; background:color-mix(in oklch, ${colorFor(scheduleIndex).css} 30%, var(--muted));`}
									>
										<Sun class="size-6" />
									</div>
								{/if}
							{/if}
						{/each}
						{#if editingIndex === null}
							<div
								class="pointer-events-none absolute inset-[-1px] z-[2]"
								style={`background: conic-gradient(from ${bedtimeAngle}deg, ${selectedScheduleColor} 0deg ${sleepAngle}deg, transparent ${sleepAngle}deg 360deg);`}
							></div>
						{/if}
						<div class="absolute inset-[calc(15%-1px)] z-[3] rounded-full bg-card shadow-inner">
							<div class="pointer-events-none absolute inset-0 overflow-hidden rounded-full">
								{#each tickMarks as tick, index (index)}
									<div class="absolute inset-0" style={`transform: rotate(${tick.angle}deg);`}>
										<div
											class={[
												'absolute left-1/2 top-0.5 -translate-x-1/2 bg-foreground',
												tick.tier === 'major'
													? 'h-2 w-px opacity-60'
													: tick.tier === 'mid'
														? 'h-1.5 w-px opacity-25'
														: 'h-1 w-px opacity-10'
											]}
										></div>
									</div>
								{/each}
							</div>
							<span class="absolute left-1/2 top-3 -translate-x-1/2 text-body font-semibold"
								>12 AM</span
							>
							<span class="absolute right-3 top-1/2 -translate-y-1/2 text-body font-semibold"
								>6 AM</span
							>
							<span class="absolute bottom-3 left-1/2 -translate-x-1/2 text-body font-semibold"
								>12 PM</span
							>
							<span class="absolute left-3 top-1/2 -translate-y-1/2 text-body font-semibold"
								>6 PM</span
							>
							<div class="absolute inset-0 grid place-items-center text-center">
								<div>
									<Moon class="mx-auto size-6 text-foreground" />
									<p class="mt-1 text-xl font-semibold">{formatDuration(sleepMinutes)}</p>
									<RoutineConflicts
										class="mx-auto w-36"
										{conflicts}
										fallback="sleep duration"
										{onOpenRoutine}
									/>
								</div>
							</div>
						</div>
						<button
							type="button"
							aria-label={`Drag bedtime, currently ${formatTime(bedtimeMinutes)}`}
							class="absolute z-10 grid size-14 -translate-x-1/2 -translate-y-1/2 touch-none place-items-center rounded-full text-primary-foreground shadow-sm"
							style={`left:${bedtimePosition.left}%; top:${bedtimePosition.top}%; background:${selectedScheduleColor};`}
							onpointerdown={(event) => startDrag(event, 'bedtime')}
							><BedDouble class="size-6" /></button
						>
						<button
							type="button"
							aria-label={`Drag wake time, currently ${formatTime(wakeMinutes)}`}
							class="absolute z-10 grid size-14 -translate-x-1/2 -translate-y-1/2 touch-none place-items-center rounded-full text-primary-foreground shadow-sm"
							style={`left:${wakePosition.left}%; top:${wakePosition.top}%; background:${selectedScheduleColor};`}
							onpointerdown={(event) => startDrag(event, 'wake')}><Sun class="size-6" /></button
						>
					</div>
				</div>

				<div class="min-w-0 flex-1 space-y-1.5">
					<p
						class="flex items-center justify-center gap-2 text-[15.6px] font-semibold uppercase text-muted-foreground"
					>
						<AlarmClock class="size-[18px]" /> Wake up
					</p>
					<div class="mx-auto w-fit space-y-1.5">
						{#each displayRows as row (row.index ?? 'draft')}
							<button
								type="button"
								class="flex items-center gap-2.5 rounded-lg bg-background/70 px-3 py-1.5 text-left hover:bg-background"
								onclick={() => {
									if (row.index !== null) editSchedule(row.index);
								}}
							>
								<span class="size-2.5 shrink-0 rounded-full" style:background={row.color}></span>
								<span class="text-[20.8px] font-semibold">{formatTime(row.wake)}</span>
							</button>
						{/each}
					</div>
				</div>
			</div>
		</section>
	{/if}

	{#if errorMessage}<p class="text-body text-destructive" role="alert">{errorMessage}</p>{/if}
</div>
