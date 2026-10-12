<script lang="ts">
	import { untrack } from 'svelte';
	import { CalendarClock, CalendarDays, MapPin } from '@lucide/svelte';
	import { apiRequest } from '#lib/core/api.ts';
	import {
		blockEdgeColor,
		type CalendarClash,
		clashOptions,
		clock,
		type EventEditorValue,
		loadDays,
		minutesOf,
		minutesOnDay,
		placeBlocks,
		type PlacedBlock,
		type RoutineChangeOperation
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

	// One event on its real day: title, location and date, a track to drag its
	// times on with what's already around it, and a row for each routine it
	// overlaps offering what that routine could do instead. Overlaps never stop an
	// event, so they are amber, not red. Shared by the calendar's day timeline and
	// the chat's event proposals. On the calendar's day (`layout="day"`) the track
	// is the day's own timeline and comes first; its other blocks select them.
	let {
		value = $bindable(),
		eventId = null,
		disabled = false,
		pending = [],
		titleMissing = false,
		onOpenRoutine,
		layout = 'card',
		focusTitle = false,
		onsubmit
	}: {
		value: EventEditorValue;
		/** The event being changed: it and its own routine changes aren't context. */
		eventId?: string | null;
		disabled?: boolean;
		/** Other proposals in the same set, not on the calendar yet. */
		pending?: { key: string; title: string; date: string; start: number; end: number }[];
		titleMissing?: boolean;
		onOpenRoutine?: (routine: RoutineRef) => void;
		layout?: 'card' | 'day';
		/** Start in the title, for an event just begun. */
		focusTitle?: boolean;
		/** Enter in the title: add or save it. */
		onsubmit?: () => void;
	} = $props();

	let titleInput: HTMLInputElement | undefined = $state();
	$effect(() => {
		if (focusTitle) untrack(() => titleInput?.focus());
	});

	let placed = $state<PlacedBlock[]>([]);
	// What the server last said the event overlaps.
	let clashes = $state<CalendarClash[]>([]);
	let clashTimer: ReturnType<typeof setTimeout> | undefined;
	let clashRequest = 0;

	function changeKey(change: { window_id: string; occurrence_date: string }) {
		return `${change.window_id}:${change.occurrence_date}`;
	}

	$effect(() => {
		const date = value.date;
		untrack(() => void loadContext(date));
	});

	async function loadContext(date: string) {
		try {
			const days = await loadDays(date);
			if (value.date === date) placed = placeBlocks(days, date);
		} catch {
			placed = [];
		}
	}

	const clashQuery = $derived(
		JSON.stringify({
			date: value.date,
			start: clock(value.start),
			end: clock(value.end),
			end_day_offset: value.end >= 1440 ? 1 : 0,
			event_id: eventId,
			margins: Object.fromEntries(
				value.routine_changes.map((change) => [changeKey(change), change.margin_minutes])
			)
		})
	);

	$effect(() => {
		const query = clashQuery;
		clearTimeout(clashTimer);
		clashTimer = setTimeout(() => void fetchClashes(query), 250);
		return () => clearTimeout(clashTimer);
	});

	async function fetchClashes(query: string) {
		const request = ++clashRequest;
		try {
			const result = await apiRequest<CalendarClash[]>('/api/calendar/clashes', {
				method: 'POST',
				body: JSON.parse(query)
			});
			if (request !== clashRequest) return;
			clashes = result;
			if (!disabled) pruneChanges(result);
		} catch {
			// Context is a courtesy; the event saves without it.
		}
	}

	/** Drop changes to routines the event no longer overlaps, or options that no longer apply. */
	function pruneChanges(result: CalendarClash[]) {
		const offered = new Map(
			result
				.filter((clash) => clash.type === 'routine' && !clash.locked)
				.map((clash) => [clash.key, clash])
		);
		const kept = value.routine_changes.filter((change) =>
			offered
				.get(changeKey(change))
				?.options.some((option) => option.operation === change.operation)
		);
		if (kept.length !== value.routine_changes.length) value = { ...value, routine_changes: kept };
	}

	function chosen(clash: CalendarClash): RoutineChangeOperation {
		return (
			value.routine_changes.find((change) => changeKey(change) === clash.key)?.operation ?? 'keep'
		);
	}

	function marginFor(clash: CalendarClash) {
		return (
			value.routine_changes.find((change) => changeKey(change) === clash.key)?.margin_minutes ?? 0
		);
	}

	function choose(
		clash: CalendarClash,
		operation: RoutineChangeOperation,
		margin = marginFor(clash)
	) {
		if (!clash.window_id || !clash.occurrence_date) return;
		const others = value.routine_changes.filter((change) => changeKey(change) !== clash.key);
		value = {
			...value,
			routine_changes:
				operation === 'keep'
					? others
					: [
							...others,
							{
								window_id: clash.window_id,
								occurrence_date: clash.occurrence_date,
								operation,
								margin_minutes: margin
							}
						]
		};
	}

	function overlaps(start: number, end: number) {
		return start < value.end && end > value.start;
	}

	// The clashes last asked for, less any the event has since moved clear of: the
	// answer for where it is now is still on its way.
	const liveClashes = $derived(
		clashes.filter((clash) =>
			overlaps(minutesOnDay(clash.start, value.date), minutesOnDay(clash.end, value.date))
		)
	);

	function optionsFor(
		kind: NonNullable<CalendarClash['routine_kind']>,
		title: string,
		start: string,
		end: string,
		key: string
	) {
		const margin =
			value.routine_changes.find((change) => changeKey(change) === key)?.margin_minutes ?? 0;
		return clashOptions(
			kind,
			title,
			[minutesOnDay(start, value.date), minutesOnDay(end, value.date)],
			[value.start, value.end],
			margin,
			value.date
		);
	}

	// The routines the event overlaps and what each could do instead, kept in step
	// with the event as it moves without waiting to ask: the choices of a clash
	// already known are worked out again here, and a routine just reached is
	// offered from its block on the day. The server's answer then takes their
	// place, and is what a save goes by.
	const routineClashes = $derived.by(() => {
		const known = liveClashes.filter((clash) => clash.type === 'routine');
		const shown: CalendarClash[] = known.map((clash) =>
			clash.locked || !clash.routine_kind
				? clash
				: {
						...clash,
						options: optionsFor(
							clash.routine_kind,
							clash.title,
							clash.start,
							clash.end,
							clash.key
						).map((option) => ({
							...option,
							// "Keep" and "Skip …" never change with the times; the server names the routine best.
							label:
								option.operation === 'keep' || option.operation === 'skip'
									? (clash.options.find((had) => had.operation === option.operation)?.label ??
										option.label)
									: option.label
						}))
					}
		);
		const keys = new Set(known.map((clash) => clash.key));
		for (const { block, start, end } of placed) {
			if (block.kind === 'event' || !block.window_id || !block.occurrence_date) continue;
			// A day an event already changed is drawn as that left it, not as the routine has it.
			if (block.changed_by === 'event' || !overlaps(start, end)) continue;
			const key = `${block.window_id}:${block.occurrence_date}`;
			if (keys.has(key)) continue;
			keys.add(key);
			const locked = block.changed_by === 'direct';
			shown.push({
				key,
				type: 'routine',
				title: block.title,
				start: block.start,
				end: block.end,
				routine_id: block.routine_id ?? null,
				routine_kind: block.kind,
				window_id: block.window_id,
				occurrence_date: block.occurrence_date,
				event_id: null,
				locked,
				options: locked ? [] : optionsFor(block.kind, block.title, block.start, block.end, key)
			});
		}
		return shown;
	});

	const trackBlocks = $derived.by(() => {
		const blocks: TrackBlock[] = [];
		const clashed = new Set(routineClashes.map((clash) => clash.key));
		for (const { block, start, end } of placed) {
			if (eventId && block.event_id === eventId) continue;
			// A routine this event overlaps is drawn from its clash below, as the option chosen leaves it.
			if (
				block.window_id &&
				block.occurrence_date &&
				clashed.has(`${block.window_id}:${block.occurrence_date}`)
			)
				continue;
			blocks.push({
				key: block.id,
				start,
				end,
				style: 'soft',
				edge: blockEdgeColor(block.kind),
				raised: block.kind === 'event',
				label: block.title,
				highlight: overlaps(start, end) ? 'warning' : null
			});
		}
		for (const clash of routineClashes) {
			const option = clash.options.find((candidate) => candidate.operation === chosen(clash));
			const segments = option?.segments ?? [[clash.start, clash.end]];
			segments.forEach(([from, to], index) => {
				const start = minutesOnDay(from, value.date);
				const end = minutesOnDay(to, value.date);
				blocks.push({
					key: `${clash.key}:${index}`,
					start,
					end,
					style: 'soft',
					edge: blockEdgeColor(clash.routine_kind ?? 'event'),
					raised: clash.type === 'event',
					label: clash.title,
					highlight: overlaps(start, end) ? 'warning' : null
				});
			});
		}
		for (const sibling of pending) {
			if (sibling.date !== value.date) continue;
			blocks.push({
				key: `pending:${sibling.key}`,
				start: sibling.start,
				end: sibling.end,
				style: 'pending',
				label: sibling.title,
				highlight: overlaps(sibling.start, sibling.end) ? 'warning' : null
			});
		}
		return blocks;
	});

	// What the event overlaps, named the moment it does: from the clashes asked
	// for, and until those arrive from the day's blocks already here. A routine
	// the event changes is no longer in its way.
	const conflicts = $derived.by<RoutineConflict[]>(() => {
		const changed = new Set(value.routine_changes.map(changeKey));
		const found = new Map<string, RoutineConflict>();
		for (const clash of liveClashes) {
			if (clash.type === 'routine' && changed.has(clash.key)) continue;
			found.set(clash.key, {
				label: clash.title,
				routine:
					clash.routine_id && clash.routine_kind
						? { id: clash.routine_id, kind: clash.routine_kind }
						: null
			});
		}
		for (const { block, start, end } of placed) {
			if (eventId && block.event_id === eventId) continue;
			if (!overlaps(start, end)) continue;
			const key =
				block.window_id && block.occurrence_date
					? `${block.window_id}:${block.occurrence_date}`
					: `event:${block.event_id ?? block.id}`;
			if (found.has(key) || changed.has(key)) continue;
			found.set(key, {
				label: block.title,
				routine:
					block.routine_id && block.kind !== 'event'
						? { id: block.routine_id, kind: block.kind }
						: null
			});
		}
		return [
			...found.values(),
			...pending
				.filter((sibling) => sibling.date === value.date && overlaps(sibling.start, sibling.end))
				.map((sibling) => ({ label: sibling.title, routine: null }))
		];
	});

	const dateLabel = $derived(
		value.date
			? new Date(`${value.date}T00:00:00`).toLocaleDateString(undefined, {
					weekday: 'short',
					month: 'short',
					day: 'numeric'
				})
			: 'No date'
	);

	function setSpan(start: number, end: number) {
		value = { ...value, start, end };
	}

	/** A typed start keeps the event's length. */
	function typeStart(text: string) {
		if (!text) return;
		const start = minutesOf(text);
		value = { ...value, start, end: start + (value.end - value.start) };
	}

	/** A typed end at or before the start is the next day's. */
	function typeEnd(text: string) {
		if (!text) return;
		const end = minutesOf(text);
		value = { ...value, end: end > value.start ? end : end + 1440 };
	}

	const margins = [0, 15, 30, 45, 60];
</script>

{#snippet titleField()}
	<input
		class={[
			'w-full border-b bg-transparent pb-1 text-xl font-semibold outline-none placeholder:text-muted-foreground/70 focus:border-primary',
			titleMissing ? 'border-destructive' : 'border-transparent hover:border-border'
		]}
		aria-label="Event title"
		aria-invalid={titleMissing}
		placeholder="What's happening?"
		maxlength="200"
		bind:this={titleInput}
		value={value.title}
		oninput={(event) => (value = { ...value, title: event.currentTarget.value })}
		onkeydown={(event) => {
			if (event.key !== 'Enter' || event.isComposing || !onsubmit) return;
			event.preventDefault();
			onsubmit();
		}}
	/>
{/snippet}

{#snippet chips()}
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
	<label
		class="relative inline-flex h-8 items-center gap-1.5 rounded-full bg-muted/60 px-3 text-muted-foreground focus-within:ring-2 focus-within:ring-primary/40"
	>
		<CalendarDays class="size-3.5 shrink-0" />
		<span class="text-foreground">{dateLabel}</span>
		<input
			class="absolute inset-0 cursor-pointer opacity-0"
			type="date"
			aria-label="Date"
			value={value.date}
			onchange={(event) => {
				if (event.currentTarget.value) value = { ...value, date: event.currentTarget.value };
			}}
		/>
	</label>
{/snippet}

{#snippet track()}
	<TimeTrack
		start={value.start}
		end={value.end}
		blocks={trackBlocks}
		{disabled}
		onchange={setSpan}
	/>
{/snippet}

{#snippet clashList(className: string)}
	{#if routineClashes.length}
		<div class={className} aria-label="Routines this overlaps">
			{#each routineClashes as clash (clash.key)}
				{@const operation = chosen(clash)}
				<div class={layout === 'day' ? 'py-3' : 'rounded-xl bg-background/70 p-3'}>
					<p class="text-body">
						<span class="font-medium">{clash.title}</span>
						<span class="text-muted-foreground">
							· {formatClock(minutesOnDay(clash.start, value.date))}–{formatClock(
								minutesOnDay(clash.end, value.date)
							)}</span
						>
					</p>
					{#if clash.locked}
						<p class="mt-1 text-sm text-muted-foreground">
							This day was changed on its own, so it stays as it is.
						</p>
					{:else}
						<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
							<div
								role="radiogroup"
								aria-label={`What happens to ${clash.title}`}
								class="flex flex-wrap gap-1.5"
							>
								{#each clash.options as option (option.operation)}
									<button
										type="button"
										role="radio"
										aria-checked={operation === option.operation}
										class={[
											'h-8 rounded-full px-3 text-body',
											operation === option.operation
												? 'bg-foreground font-medium text-background'
												: 'bg-muted/60 text-muted-foreground hover:text-foreground'
										]}
										onclick={() => choose(clash, option.operation)}>{option.label}</button
									>
								{/each}
							</div>
							{#if operation === 'start_later' || operation === 'end_earlier' || operation === 'split'}
								<label class="flex items-center gap-2 text-sm text-muted-foreground">
									With
									<select
										class="h-7 rounded-md border border-border bg-card px-1.5 text-sm text-foreground"
										value={marginFor(clash)}
										onchange={(event) =>
											choose(clash, operation, Number(event.currentTarget.value))}
									>
										{#each margins as margin (margin)}<option value={margin}
												>{margin ? `${margin} min` : 'no'}</option
											>{/each}
									</select>
									to get there and back
								</label>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
{/snippet}

{#if layout === 'day'}
	<div class="flex flex-1 flex-col gap-5">
		<div inert={disabled} class="flex flex-1 flex-col gap-3">
			{@render titleField()}
			<div class="flex flex-wrap items-center gap-2 text-body">
				<span
					class="inline-flex h-8 items-center gap-1.5 rounded-full bg-muted/60 px-3 tabular-nums focus-within:ring-2 focus-within:ring-primary/40"
				>
					<CalendarClock class="size-3.5 shrink-0 text-muted-foreground" />
					<input
						class="bg-transparent outline-none"
						type="time"
						aria-label="Starts"
						value={clock(value.start)}
						onchange={(event) => typeStart(event.currentTarget.value)}
					/>
					–
					<input
						class="bg-transparent outline-none"
						type="time"
						aria-label="Ends"
						value={clock(value.end)}
						onchange={(event) => typeEnd(event.currentTarget.value)}
					/>
					{value.end >= 1440 ? 'next day' : ''}
					<span class="text-muted-foreground">· {formatLength(value.end - value.start)}</span>
				</span>
				{@render chips()}
			</div>
			{#if conflicts.length}
				<RoutineConflicts {conflicts} fallback="" tone="warning" {onOpenRoutine} />
			{/if}
			{@render clashList('divide-y divide-border/70 border-t border-border/70')}
			<textarea
				class="min-h-24 w-full flex-1 resize-none rounded-lg border border-transparent bg-muted/40 px-3 py-2 text-body leading-relaxed outline-none placeholder:text-muted-foreground/70 hover:border-border focus:border-primary/60"
				aria-label="Description"
				placeholder="Add a description…"
				maxlength="5000"
				value={value.description ?? ''}
				oninput={(event) => (value = { ...value, description: event.currentTarget.value })}
			></textarea>
		</div>
	</div>
{:else}
	<div class="space-y-6">
		<div inert={disabled} class="space-y-3">
			{@render titleField()}
			<div class="flex flex-wrap items-center gap-2 text-body">
				{@render chips()}
			</div>
		</div>

		<section
			inert={disabled}
			class="rounded-2xl border border-border bg-muted/35 p-5"
			aria-label="Start and end"
		>
			<div class="grid grid-cols-3 items-end gap-3 text-center">
				<div>
					<p class="text-[15.6px] font-semibold uppercase text-muted-foreground">Starts</p>
					<p class="mt-1 text-[20.8px] font-semibold">{formatClock(value.start)}</p>
				</div>
				<div class="min-w-0">
					<CalendarClock class="mx-auto size-5 text-foreground" />
					<p class="mt-1 text-xl font-semibold">{formatLength(value.end - value.start)}</p>
					<RoutineConflicts {conflicts} fallback={dateLabel} tone="warning" {onOpenRoutine} />
				</div>
				<div>
					<p class="text-[15.6px] font-semibold uppercase text-muted-foreground">Ends</p>
					<p class="mt-1 text-[20.8px] font-semibold">{formatClock(value.end)}</p>
					<p class="text-sm text-muted-foreground">
						{value.end >= 1440 ? 'next day' : 'same day'}
					</p>
				</div>
			</div>

			<div class="mt-6">
				{@render track()}
			</div>

			{@render clashList('mt-5 space-y-2')}
		</section>
	</div>
{/if}
