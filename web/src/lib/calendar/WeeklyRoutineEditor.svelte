<script lang="ts">
	import { onMount } from 'svelte';
	import { BriefcaseBusiness, CalendarRange, MapPin, Plus, Repeat, Trash2 } from '@lucide/svelte';
	import { cachedFetch } from '#lib/core/api.ts';
	import {
		type CalendarPayload,
		type CalendarRoutine,
		emptyRoutineWindow,
		type RoutineEditorValue
	} from '#lib/calendar/calendar.ts';
	import RoutineConflicts, {
		type RoutineConflict,
		type RoutineRef
	} from '#lib/calendar/RoutineConflicts.svelte';
	import TimeTrack, {
		formatClock,
		formatLength,
		type TrackBlock
	} from '#lib/calendar/TimeTrack.svelte';

	// A weekly routine that lives inside the waking day, edited on a track rather
	// than the sleep dial: a straight line zooms to fit a one-hour habit, and a
	// circle cannot. The day strip and pattern colors are the sleep editor's.
	let {
		value = $bindable(),
		missingFields = [],
		disabled = false,
		conflicted = $bindable(false),
		onOpenRoutine
	}: {
		value: RoutineEditorValue;
		missingFields?: string[];
		disabled?: boolean;
		/** Whether any pattern overlaps a wall; the schedule can't be saved while it does. */
		conflicted?: boolean;
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
	const scheduleColors = [
		{ dot: 'bg-primary', ring: 'ring-primary', css: 'var(--primary)' },
		{ dot: 'bg-schedule-amber', ring: 'ring-schedule-amber', css: 'var(--schedule-amber)' },
		{ dot: 'bg-schedule-blue', ring: 'ring-schedule-blue', css: 'var(--schedule-blue)' },
		{ dot: 'bg-schedule-rose', ring: 'ring-schedule-rose', css: 'var(--schedule-rose)' }
	] as const;
	type Block = {
		start: number;
		end: number;
		label: string;
		hard: boolean;
		color?: string;
		routine?: CalendarRoutine;
	};
	type Span = { start: number; end: number };

	let selected = $state(0);
	let others = $state<CalendarRoutine[]>([]);
	let datesOpen = $state(false);
	let errorMessage = $state('');
	// As on the sleep editor, a day's fill only animates between patterns: a
	// day's first color appears at once, and a deleted pattern's siblings snap
	// to fill its place rather than sweeping across the gap.
	let suppressDayAnimation = $state(false);
	let dayAnimationsSuppressed = $state<number[]>([]);

	const index = $derived(Math.max(0, Math.min(selected, value.windows.length - 1)));
	const current = $derived(spanOf(index));
	const activeDays = $derived(value.windows[index]?.weekdays ?? []);

	onMount(async () => {
		// Other routines are context: shown around this one, and walls where the
		// calendar would refuse the overlap. A read-only view is a record, not a
		// plan, and skips them.
		if (disabled) return;
		try {
			const calendar = await cachedFetch<CalendarPayload>('/api/calendar', fetch);
			others = Object.values(calendar.routines)
				.flat()
				.filter((routine) => routine.status === 'active');
		} catch {
			others = [];
		}
	});

	function parseTime(value: string | null | undefined) {
		if (!value) return null;
		const [hour, minute] = value.split(':').map(Number);
		return hour * 60 + minute;
	}

	function timeValue(minutes: number) {
		const wrapped = ((minutes % 1440) + 1440) % 1440;
		return `${String(Math.floor(wrapped / 60)).padStart(2, '0')}:${String(wrapped % 60).padStart(2, '0')}`;
	}

	function formatDate(value: string) {
		return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
			month: 'short',
			day: 'numeric'
		});
	}

	/** A window's times on its own start day's clock; an overnight end runs past 1440. */
	function spanOf(windowIndex: number): Span {
		const window = value.windows[windowIndex];
		const fallback = emptyRoutineWindow(value.kind);
		const start = parseTime(window?.start_local_time) ?? parseTime(fallback.start_local_time)!;
		let end = parseTime(window?.end_local_time) ?? parseTime(fallback.end_local_time)!;
		if (end <= start) end += 1440;
		return { start, end };
	}

	function colorFor(patternIndex: number) {
		return scheduleColors[patternIndex % scheduleColors.length];
	}

	function isMissing(path: string) {
		return missingFields.includes(path);
	}

	/** Where each occurrence of a window falls on `day`'s clock, including the nights either side. */
	function occurrences(
		window: { weekdays: number[]; start_local_time: string | null; end_local_time: string | null },
		day: number
	): Span[] {
		const start = parseTime(window.start_local_time);
		const end = parseTime(window.end_local_time);
		if (start === null || end === null) return [];
		const length = end <= start ? end + 1440 - start : end - start;
		return [-1, 0, 1]
			.filter((shift) => window.weekdays.includes((day + shift + 7) % 7))
			.map((shift) => ({ start: shift * 1440 + start, end: shift * 1440 + start + length }));
	}

	const ownIds = $derived(new Set(value.windows.map((window) => window.id).filter(Boolean)));
	// The routine this proposal edits is already on the calendar; it is this one, not context.
	const context = $derived(
		others.filter(
			(routine) => !routine.windows.some((window) => window.id && ownIds.has(window.id))
		)
	);

	function blocksFor(day: number, exclude = index): Block[] {
		const blocks: Block[] = [];
		value.windows.forEach((window, windowIndex) => {
			if (windowIndex === exclude) return;
			for (const span of occurrences(window, day))
				blocks.push({ ...span, label: '', hard: true, color: colorFor(windowIndex).css });
		});
		for (const routine of context) {
			// The calendar keeps work and sleep clear of everything; other routines may share time.
			const hard = value.kind !== 'other' || routine.kind !== 'other';
			const label =
				routine.kind === 'sleep'
					? 'Sleep'
					: routine.name || (routine.kind === 'work' ? 'Work' : 'Routine');
			for (const window of routine.windows)
				for (const span of occurrences(window, day)) blocks.push({ ...span, label, hard, routine });
		}
		return blocks;
	}

	// Walls don't stop the planning, only the saving: each pattern's overlaps are
	// named here, and the card holding the editor refuses to save while any remain.
	function conflictsOf(windowIndex: number) {
		const window = value.windows[windowIndex];
		const span = spanOf(windowIndex);
		const found = new Map<string, RoutineConflict>();
		for (const day of window.weekdays)
			for (const block of blocksFor(day, windowIndex))
				if (block.hard && span.start < block.end && block.start < span.end)
					found.set(
						block.routine?.id ?? 'own',
						block.routine
							? { label: block.label, routine: block.routine }
							: { label: 'another schedule in this routine', routine: null }
					);
		return found;
	}

	const conflicts = $derived.by(() => {
		const found = new Map<string, RoutineConflict>();
		value.windows.forEach((_, windowIndex) => {
			for (const [key, conflict] of conflictsOf(windowIndex)) found.set(key, conflict);
		});
		return [...found.values()];
	});

	$effect(() => {
		conflicted = conflicts.length > 0;
	});

	const trackBlocks = $derived.by(() => {
		const shown = new Map<string, TrackBlock>();
		for (const day of activeDays) {
			for (const block of blocksFor(day)) {
				const key = `${block.start}:${block.end}:${block.label}:${block.color}`;
				if (shown.has(key)) continue;
				shown.set(key, {
					key,
					start: block.start,
					end: block.end,
					style: block.color ? 'pattern' : block.hard ? 'wall' : 'soft',
					color: block.color,
					label: block.hard ? undefined : block.label,
					highlight:
						block.hard && block.start < current.end && current.start < block.end ? 'error' : null
				});
			}
		}
		return [...shown.values()];
	});

	function updateWindow(patch: Partial<RoutineEditorValue['windows'][number]>) {
		value = {
			...value,
			windows: value.windows.map((window, windowIndex) =>
				windowIndex === index ? { ...window, ...patch } : window
			)
		};
	}

	function setSpan(start: number, end: number) {
		updateWindow({
			start_local_time: timeValue(start),
			end_local_time: timeValue(end),
			end_day_offset: end >= 1440 ? 1 : 0
		});
	}

	function toggleDay(day: number) {
		if (!value.windows.length) addPattern();
		if (!activeDays.includes(day) && patternIndicesForDay(day).length === 0) {
			dayAnimationsSuppressed = [...dayAnimationsSuppressed, day];
			requestAnimationFrame(() =>
				requestAnimationFrame(() => {
					dayAnimationsSuppressed = dayAnimationsSuppressed.filter((value) => value !== day);
				})
			);
		}
		const weekdays = activeDays.includes(day)
			? activeDays.filter((value) => value !== day)
			: [...activeDays, day].sort((a, b) => a - b);
		updateWindow({ weekdays });
		errorMessage = '';
	}

	function selectPattern(patternIndex: number) {
		selected = patternIndex;
		errorMessage = '';
	}

	function addPattern() {
		value = { ...value, windows: [...value.windows, emptyRoutineWindow(value.kind)] };
		selectPattern(value.windows.length - 1);
	}

	function removePattern() {
		suppressDayAnimation = true;
		requestAnimationFrame(() => requestAnimationFrame(() => (suppressDayAnimation = false)));
		value = { ...value, windows: value.windows.filter((_, windowIndex) => windowIndex !== index) };
		// Deleting the last pattern starts a fresh one, as the sleep editor does.
		if (!value.windows.length) {
			addPattern();
			return;
		}
		selectPattern(Math.min(index, value.windows.length - 1));
	}

	function patternLabel(patternIndex: number) {
		const { start, end } = spanOf(patternIndex);
		return `${formatClock(start)}–${formatClock(end)}`;
	}

	function patternIndicesForDay(day: number) {
		return value.windows.flatMap((window, windowIndex) =>
			window.weekdays.includes(day) ? [windowIndex] : []
		);
	}

	const datesLabel = $derived(
		`${value.effective_from ? `Starts ${formatDate(value.effective_from)}` : 'Starts now'} · ${value.effective_until ? `until ${formatDate(value.effective_until)}` : 'ongoing'}`
	);
	const startMissing = $derived(!value.windows[index]?.start_local_time);
	const endMissing = $derived(!value.windows[index]?.end_local_time);
	const overnight = $derived(current.end >= 1440);
	const KindIcon = $derived(value.kind === 'work' ? BriefcaseBusiness : Repeat);
</script>

<div class="space-y-6">
	<div inert={disabled} class="space-y-3">
		<input
			class={[
				'w-full border-b bg-transparent pb-1 text-xl font-semibold outline-none placeholder:text-muted-foreground/70 focus:border-primary',
				isMissing('name') ? 'border-destructive' : 'border-transparent hover:border-border'
			]}
			aria-label="Routine name"
			aria-invalid={isMissing('name')}
			placeholder={value.kind === 'work' ? 'Work' : 'Name this routine'}
			maxlength="200"
			value={value.name ?? ''}
			oninput={(event) => (value = { ...value, name: event.currentTarget.value })}
		/>
		<div class="flex flex-wrap items-center gap-2 text-body">
			<label
				class="inline-flex h-8 items-center gap-1.5 rounded-full bg-muted/60 px-3 text-muted-foreground focus-within:ring-2 focus-within:ring-primary/40"
			>
				<MapPin class="size-3.5 shrink-0" />
				<input
					class="field-sizing-content min-w-[6ch] max-w-[28ch] bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
					aria-label="Location"
					placeholder="No location"
					value={value.location ?? ''}
					oninput={(event) => (value = { ...value, location: event.currentTarget.value || null })}
				/>
			</label>
			<button
				type="button"
				class={[
					'inline-flex h-8 items-center gap-1.5 rounded-full px-3 hover:text-foreground',
					isMissing('effective_until')
						? 'bg-destructive/10 text-destructive'
						: 'bg-muted/60 text-muted-foreground',
					datesOpen && 'text-foreground'
				]}
				aria-expanded={datesOpen}
				onclick={() => (datesOpen = !datesOpen)}
			>
				<CalendarRange class="size-3.5" />
				{datesLabel}
			</button>
		</div>
		{#if datesOpen}
			<div
				class="flex flex-wrap gap-3 rounded-xl bg-muted/35 p-3 text-note font-medium text-muted-foreground"
			>
				<label class="space-y-1">
					<span class="block">Starts</span>
					<input
						class="h-9 rounded-md border border-border bg-card px-2 text-body text-foreground"
						type="date"
						value={value.effective_from ?? ''}
						oninput={(event) =>
							(value = { ...value, effective_from: event.currentTarget.value || null })}
					/>
				</label>
				<label class="space-y-1">
					<span class="block">Ends</span>
					<input
						class={[
							'h-9 rounded-md border bg-card px-2 text-body text-foreground',
							isMissing('effective_until') ? 'border-destructive' : 'border-border'
						]}
						type="date"
						min={value.effective_from ?? undefined}
						value={value.effective_until ?? ''}
						oninput={(event) =>
							(value = { ...value, effective_until: event.currentTarget.value || null })}
					/>
				</label>
				<p class="basis-full font-normal">
					Leave either blank to begin now or continue indefinitely.
				</p>
			</div>
		{/if}
	</div>

	<fieldset>
		<legend class="sr-only">Schedule patterns</legend>
		<div class="mb-2 flex min-h-7 items-center justify-center gap-1.5" aria-label="Schedule colors">
			{#each value.windows, patternIndex (patternIndex)}
				<button
					type="button"
					aria-label={`${disabled ? 'View' : 'Edit'} schedule ${patternIndex + 1}`}
					aria-pressed={index === patternIndex}
					class={[
						'size-9 rounded-full border-2 border-background shadow-sm transition',
						colorFor(patternIndex).dot,
						index === patternIndex && [
							'ring-2 ring-offset-1 ring-offset-card',
							colorFor(patternIndex).ring
						]
					]}
					onclick={() => selectPattern(patternIndex)}
				></button>
			{/each}
			{#if !disabled}
				<button
					type="button"
					aria-label="Add schedule"
					class="inline-flex size-7 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
					onclick={addPattern}><Plus class="size-4" /></button
				>
				{#if value.windows.length}
					<button
						type="button"
						aria-label="Delete selected schedule"
						class="inline-flex size-7 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-destructive"
						onclick={removePattern}><Trash2 class="size-4" /></button
					>
				{/if}
			{/if}
		</div>
		<div
			inert={disabled}
			class={[
				'grid grid-cols-7 gap-2 rounded-xl bg-muted/60 p-2',
				(isMissing(`windows.${index}.weekdays`) || isMissing('windows')) &&
					'ring-1 ring-destructive/60'
			]}
		>
			{#each days as day, dayIndex (dayIndex)}
				{@const patternIndices = patternIndicesForDay(dayIndex)}
				<button
					type="button"
					aria-label={`${day.long}, ${patternIndices.length ? patternIndices.map(patternLabel).join(', ') : 'no schedule'}`}
					aria-pressed={activeDays.includes(dayIndex)}
					class={[
						'relative mx-auto inline-flex size-10 items-center justify-center overflow-hidden rounded-full border-2 [background-clip:padding-box] text-body font-semibold transition-shadow',
						patternIndices.length
							? 'border-background text-primary-foreground shadow-sm'
							: 'border-transparent text-muted-foreground hover:bg-background',
						activeDays.includes(dayIndex) &&
							`ring-2 ring-offset-2 ring-offset-background ${colorFor(index).ring}`
					]}
					onclick={() => toggleDay(dayIndex)}
				>
					<span class="pointer-events-none absolute inset-0 flex overflow-hidden rounded-full">
						{#each value.windows as window, patternIndex (window.id ?? patternIndex)}
							<span
								class={[
									'h-full min-w-0',
									!suppressDayAnimation &&
										!dayAnimationsSuppressed.includes(dayIndex) &&
										patternIndices.length > 0 &&
										'transition-[width] duration-180 ease-[ease]'
								]}
								style:background={colorFor(patternIndex).css}
								style:transition={suppressDayAnimation ? 'none' : undefined}
								style:width={window.weekdays.includes(dayIndex)
									? `${100 / patternIndices.length}%`
									: '0%'}
							></span>
						{/each}
					</span>
					<span class="relative z-10">{day.short}</span>
				</button>
			{/each}
		</div>
	</fieldset>

	<section
		inert={disabled}
		class="rounded-2xl border border-border bg-muted/35 p-5"
		aria-label="Start and end"
	>
		<div class="grid grid-cols-3 items-end gap-3 text-center">
			<div>
				<p class="text-[15.6px] font-semibold uppercase text-muted-foreground">Starts</p>
				<p class={['mt-1 text-[20.8px] font-semibold', startMissing && 'text-destructive']}>
					{startMissing ? 'Not set' : formatClock(current.start)}
				</p>
			</div>
			<div class="min-w-0">
				<KindIcon class="mx-auto size-5 text-foreground" />
				<p class="mt-1 text-xl font-semibold">{formatLength(current.end - current.start)}</p>
				<!-- Overlaps take this line's place so the card never shifts as they come and go. -->
				<RoutineConflicts
					{conflicts}
					fallback={activeDays.length
						? `${activeDays.length} day${activeDays.length === 1 ? '' : 's'} a week`
						: 'no days yet'}
					{onOpenRoutine}
				/>
			</div>
			<div>
				<p class="text-[15.6px] font-semibold uppercase text-muted-foreground">Ends</p>
				<p class={['mt-1 text-[20.8px] font-semibold', endMissing && 'text-destructive']}>
					{endMissing ? 'Not set' : formatClock(current.end)}
				</p>
				<p class="text-sm text-muted-foreground">{overnight ? 'next day' : 'same day'}</p>
			</div>
		</div>

		<div class="mt-6">
			<TimeTrack
				start={current.start}
				end={current.end}
				color={colorFor(index).css}
				blocks={trackBlocks}
				{disabled}
				showBlock={activeDays.length > 0 || !value.windows.length}
				emptyText="Pick the days this happens."
				fitKey={index}
				onchange={setSpan}
			/>
		</div>
	</section>

	{#if errorMessage}<p class="text-body text-destructive" role="alert">{errorMessage}</p>{/if}
</div>
