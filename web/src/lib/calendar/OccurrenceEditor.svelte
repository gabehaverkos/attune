<script lang="ts" module>
	/** One day of a routine as changed directly: skipped, or its own times (minutes on its start day). */
	export interface OccurrenceValue {
		skip: boolean;
		start: number;
		end: number;
	}
</script>

<script lang="ts">
	import { untrack } from 'svelte';
	import { CalendarDays } from '@lucide/svelte';
	import TimeTrack, {
		formatClock,
		formatLength,
		type TrackBlock
	} from '#lib/calendar/TimeTrack.svelte';
	import {
		blockEdgeColor,
		clock,
		loadDays,
		minutesOf,
		placeBlocks,
		type PlacedBlock
	} from '#lib/calendar/calendar.ts';

	// "Off work Friday", "sleeping in Saturday": one occurrence of a routine,
	// changed on its own without any event. The track is the occurrence's start
	// day, so sleep's wake time runs past midnight as it does in the sleep editor,
	// and a bedtime moved earlier than midnight reaches into the evening before.
	// On the calendar's day (`layout="day"`) the day is on the grid beside it, and
	// its times are typed here.
	let {
		value = $bindable(),
		note = $bindable(''),
		date,
		windowId,
		title,
		status = '',
		disabled = false,
		layout = 'card'
	}: {
		value: OccurrenceValue;
		/** The user's note on this day, on the calendar's day only. */
		note?: string;
		/** The day the occurrence starts. */
		date: string;
		windowId: string;
		title: string;
		/** What's already true of this day, e.g. "changed for this day". */
		status?: string;
		disabled?: boolean;
		layout?: 'card' | 'day';
	} = $props();

	let placed = $state<PlacedBlock[]>([]);

	$effect(() => {
		const day = date;
		untrack(async () => {
			try {
				const days = await loadDays(day);
				if (day === date) placed = placeBlocks(days, day);
			} catch {
				placed = [];
			}
		});
	});

	const trackBlocks = $derived<TrackBlock[]>(
		placed
			.filter(({ block }) => !(block.window_id === windowId && block.occurrence_date === date))
			.map(({ block, start, end }) => ({
				key: block.id,
				start,
				end,
				style: 'soft',
				edge: blockEdgeColor(block.kind),
				raised: block.kind === 'event',
				label: block.title
			}))
	);

	const dateLabel = $derived(
		new Date(`${date}T00:00:00`).toLocaleDateString(undefined, {
			weekday: 'long',
			month: 'short',
			day: 'numeric'
		})
	);

	/** A typed start keeps the day's length, and lands on whichever day is nearer the start it replaces. */
	function typeStart(text: string) {
		if (!text) return;
		const typed = minutesOf(text);
		const start =
			Math.abs(typed - 1440 - value.start) < Math.abs(typed - value.start) ? typed - 1440 : typed;
		value = { ...value, start, end: start + (value.end - value.start) };
	}

	/** A typed end is the first time that clock comes round after the start. */
	function typeEnd(text: string) {
		if (!text) return;
		let end = minutesOf(text) - 1440;
		while (end <= value.start) end += 1440;
		value = { ...value, end };
	}

	/** Which day a time falls on, when it is not the occurrence's own. */
	function dayOf(minutes: number) {
		return minutes < 0 ? 'day before' : minutes >= 1440 ? 'next day' : '';
	}
</script>

{#if layout === 'day'}
	<div class="flex flex-1 flex-col gap-5">
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div class="min-w-0">
				<p class="truncate text-xl font-semibold">{title}</p>
				<p class="mt-1 text-body tabular-nums text-muted-foreground">
					{#if value.skip}
						Skipped this day
					{:else}
						<span
							class="inline-flex h-8 items-center gap-1.5 rounded-full bg-muted/60 px-3 text-foreground focus-within:ring-2 focus-within:ring-primary/40"
							inert={disabled}
						>
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
							{value.start < 0 ? 'from the day before' : dayOf(value.end)}
						</span>
						· {formatLength(value.end - value.start)}
					{/if}
				</p>
				<p class="mt-1 text-sm text-muted-foreground">{status}</p>
			</div>
			<button
				type="button"
				role="switch"
				aria-checked={value.skip}
				class={[
					'h-8 shrink-0 rounded-full px-3 text-body',
					value.skip
						? 'bg-foreground font-medium text-background'
						: 'bg-muted/60 text-muted-foreground hover:text-foreground'
				]}
				{disabled}
				onclick={() => (value = { ...value, skip: !value.skip })}>Skip this day</button
			>
		</div>

		<textarea
			class="min-h-24 w-full flex-1 resize-none rounded-lg border border-transparent bg-muted/40 px-3 py-2 text-body leading-relaxed outline-none placeholder:text-muted-foreground/70 hover:border-border focus:border-primary/60"
			aria-label="Description"
			placeholder="Add a note for this day…"
			maxlength="5000"
			value={note}
			oninput={(event) => (note = event.currentTarget.value)}
		></textarea>
	</div>
{:else}
	<div class="space-y-4">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<div>
				<p class="text-xl font-semibold">{title}</p>
				<p class="mt-0.5 inline-flex items-center gap-1.5 text-body text-muted-foreground">
					<CalendarDays class="size-3.5" />
					{dateLabel} only
				</p>
			</div>
			<button
				type="button"
				role="switch"
				aria-checked={value.skip}
				class={[
					'h-8 rounded-full px-3 text-body',
					value.skip
						? 'bg-foreground font-medium text-background'
						: 'bg-muted/60 text-muted-foreground hover:text-foreground'
				]}
				{disabled}
				onclick={() => (value = { ...value, skip: !value.skip })}>Skip this day</button
			>
		</div>

		<section
			inert={disabled}
			class="rounded-2xl border border-border bg-muted/35 p-5"
			aria-label="Start and end"
		>
			<div class={['grid grid-cols-3 items-end gap-3 text-center', value.skip && 'opacity-40']}>
				<div>
					<p class="text-[15.6px] font-semibold uppercase text-muted-foreground">Starts</p>
					<p class="mt-1 text-[20.8px] font-semibold">{formatClock(value.start)}</p>
					<p class="text-sm text-muted-foreground">{dayOf(value.start) || 'same day'}</p>
				</div>
				<div>
					<p class="mt-1 text-xl font-semibold">{formatLength(value.end - value.start)}</p>
					<p class="text-body text-muted-foreground">{value.skip ? 'skipped' : 'this day'}</p>
				</div>
				<div>
					<p class="text-[15.6px] font-semibold uppercase text-muted-foreground">Ends</p>
					<p class="mt-1 text-[20.8px] font-semibold">{formatClock(value.end)}</p>
					<p class="text-sm text-muted-foreground">
						{dayOf(value.end) || 'same day'}
					</p>
				</div>
			</div>
			<div class="mt-6">
				<TimeTrack
					start={value.start}
					end={value.end}
					blocks={trackBlocks}
					disabled={disabled || value.skip}
					showBlock={!value.skip}
					min={-1440}
					emptyText="Skipped this day"
					onchange={(start, end) => (value = { ...value, start, end })}
				/>
			</div>
		</section>
	</div>
{/if}
