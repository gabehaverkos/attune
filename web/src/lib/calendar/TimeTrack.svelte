<script lang="ts" module>
	/** Something shown on the track around the block being edited, in minutes on the track's clock. */
	export type TrackBlock = {
		key: string;
		start: number;
		end: number;
		// wall: striped, can't be saved over. soft: grey, labelled context.
		// pattern: another pattern of the same routine, tinted its color.
		// pending: a proposal not yet on the calendar, dashed.
		style: 'wall' | 'soft' | 'pattern' | 'pending';
		color?: string;
		/**
		 * Drawn as the calendar draws what is on a day: a card with this colour down
		 * its edge. `raised` is an event, which sits forward of a routine.
		 */
		edge?: string;
		raised?: boolean;
		label?: string;
		highlight?: 'error' | 'warning' | null;
	};

	export function formatClock(minutes: number) {
		const wrapped = ((minutes % 1440) + 1440) % 1440;
		const hour = Math.floor(wrapped / 60);
		const minute = wrapped % 60;
		return `${hour % 12 || 12}:${String(minute).padStart(2, '0')} ${hour >= 12 ? 'PM' : 'AM'}`;
	}

	export function formatLength(minutes: number) {
		const hours = Math.floor(minutes / 60);
		const remainder = minutes % 60;
		if (!hours) return `${remainder} min`;
		return `${hours} hr${hours === 1 ? '' : 's'}${remainder ? ` ${remainder} min` : ''}`;
	}
</script>

<script lang="ts">
	import { untrack } from 'svelte';

	// A day as a straight line: one block to drag or resize, with what's around it.
	// A line zooms to fit a one-hour habit where a dial can't. Minutes count from
	// midnight on the block's own day, so an overnight end runs past 1440.
	//
	// The view fits the block with room either side. While dragging, each side of
	// the view eases toward the dragged edge plus a margin, never inside the
	// block's own fit: held against a side, the edge keeps reaching past it and the
	// view keeps growing; dragged back, the view eases in again. On release it
	// settles back to fit.
	let {
		start,
		end,
		color = 'var(--primary)',
		blocks = [],
		disabled = false,
		showBlock = true,
		emptyText = '',
		fitKey = '',
		min = 0,
		onchange
	}: {
		start: number;
		end: number;
		color?: string;
		blocks?: TrackBlock[];
		disabled?: boolean;
		showBlock?: boolean;
		emptyText?: string;
		/** Changing this refits the view, e.g. when a different pattern is selected. */
		fitKey?: string | number;
		/** The earliest minute the block may start: below zero reaches into the day before. */
		min?: number;
		onchange: (start: number, end: number) => void;
	} = $props();

	const MIN_LENGTH = 15;
	const MAX_LENGTH = 1435;
	const PADDING = 180;
	const MIN_RANGE = 720;
	const EDGE_MARGIN = 45;
	const EASE = 0.12;
	const MAX_STEP = 6;

	let track: HTMLDivElement | undefined = $state();
	let drag = $state<{ mode: 'start' | 'end' | 'move'; offset: number } | null>(null);
	let dragBase = { lo: 0, hi: 0 };
	let dragPointer = { clientX: 0 };
	let dragFrame = 0;
	// How far the clock reaches: from the block's own midnight to the end of the next day.
	const MAX_MINUTE = 2880;

	let range = $state(untrack(() => fit(start, end)));

	$effect(() => () => cancelAnimationFrame(dragFrame));

	$effect(() => {
		void fitKey;
		untrack(() => {
			if (!drag) range = fit(start, end);
		});
	});

	// The view refits a block that runs out of it.
	$effect(() => {
		const [from, to] = [start, end];
		untrack(() => {
			if (!showBlock || drag) return;
			if (from < range.lo || to > range.hi) range = fit(from, to);
		});
	});

	function fit(from: number, to: number) {
		let lo = Math.floor((from - PADDING) / 60) * 60;
		let hi = Math.ceil((to + PADDING) / 60) * 60;
		while (hi - lo < MIN_RANGE) {
			lo -= 60;
			hi += 60;
		}
		// The block starts on its own day, so the track never reaches back into
		// the one before it, unless `min` lets it.
		lo = Math.max(min, lo);
		return { lo, hi: Math.max(hi, lo + MIN_RANGE) };
	}

	function hourLabel(minutes: number) {
		const hour = (((minutes / 60) % 24) + 24) % 24;
		return `${hour % 12 || 12} ${hour >= 12 ? 'PM' : 'AM'}`;
	}

	const shown = $derived(
		blocks.flatMap((block) => {
			const from = Math.max(block.start, range.lo);
			const to = Math.min(block.end, range.hi);
			return to > from ? [{ ...block, start: from, end: to }] : [];
		})
	);

	function edgeAlign(minutes: number) {
		if (minutes <= range.lo) return 'translate-x-0';
		if (minutes >= range.hi) return '-translate-x-full';
		return '-translate-x-1/2';
	}

	const hourMarks = $derived.by(() => {
		const hours = (range.hi - range.lo) / 60;
		const every = hours > 18 ? 3 : hours > 10 ? 2 : 1;
		const marks: { minutes: number; label: boolean }[] = [];
		for (let minutes = Math.ceil(range.lo / 60) * 60; minutes <= range.hi; minutes += 60) {
			const hour = (((minutes / 60) % 24) + 24) % 24;
			marks.push({ minutes, label: hour % every === 0 });
		}
		return marks;
	});

	function percent(minutes: number) {
		return ((minutes - range.lo) / (range.hi - range.lo)) * 100;
	}

	function clamp(minutes: number, low: number, high: number) {
		return Math.min(Math.max(minutes, low), high);
	}

	function setSpan(from: number, to: number) {
		if (from !== start || to !== end) onchange(from, to);
	}

	function moveTo(mode: 'start' | 'end' | 'move', target: number) {
		// The view eases by fractions of a minute; times stay on the five-minute grid.
		const lo = Math.ceil(range.lo / 5) * 5;
		const hi = Math.floor(range.hi / 5) * 5;
		if (mode === 'start') {
			setSpan(
				clamp(target, Math.max(lo, end - MAX_LENGTH, min), Math.min(end - MIN_LENGTH, 1435)),
				end
			);
		} else if (mode === 'end') {
			setSpan(start, clamp(target, start + MIN_LENGTH, Math.min(hi, start + MAX_LENGTH)));
		} else {
			const length = end - start;
			const next = clamp(target, Math.max(lo, min), Math.min(hi - length, 1435));
			setSpan(next, next + length);
		}
	}

	function minuteAt(event: { clientX: number }) {
		if (!track) return start;
		const rect = track.getBoundingClientRect();
		const ratio = (event.clientX - rect.left) / rect.width;
		return Math.round((range.lo + ratio * (range.hi - range.lo)) / 5) * 5;
	}

	function startDrag(event: PointerEvent, mode: 'start' | 'end' | 'move') {
		if (disabled || !track) return;
		event.preventDefault();
		event.stopPropagation();
		track.setPointerCapture(event.pointerId);
		drag = { mode, offset: mode === 'move' ? minuteAt(event) - start : 0 };
		dragBase = fit(start, end);
		dragPointer = { clientX: event.clientX };
		cancelAnimationFrame(dragFrame);
		dragFrame = requestAnimationFrame(followDrag);
	}

	function updateDrag(event: PointerEvent) {
		if (!drag) return;
		dragPointer = { clientX: event.clientX };
		moveTo(drag.mode, minuteAt(event) - drag.offset);
	}

	function easeToward(from: number, to: number, maxStep = MAX_STEP) {
		const step = clamp((to - from) * EASE, -maxStep, maxStep);
		return Math.abs(to - from) < 0.5 ? to : from + step;
	}

	/** Each frame of a drag: ease the view toward the edges, and keep the edge under the pointer. */
	function followDrag() {
		if (!drag) return;
		const lo = easeToward(range.lo, Math.max(min, Math.min(dragBase.lo, start - EDGE_MARGIN)));
		const hi = easeToward(range.hi, Math.min(MAX_MINUTE, Math.max(dragBase.hi, end + EDGE_MARGIN)));
		if (lo !== range.lo || hi !== range.hi) {
			range = { lo, hi };
			moveTo(drag.mode, minuteAt(dragPointer) - drag.offset);
		}
		dragFrame = requestAnimationFrame(followDrag);
	}

	/** After a drag, the view eases back to fit the block. */
	function settle() {
		const target = fit(start, end);
		const lo = easeToward(range.lo, target.lo, 30);
		const hi = easeToward(range.hi, target.hi, 30);
		if (lo === range.lo && hi === range.hi) return;
		range = { lo, hi };
		dragFrame = requestAnimationFrame(settle);
	}

	function endDrag(event: PointerEvent) {
		if (!drag) return;
		drag = null;
		cancelAnimationFrame(dragFrame);
		if (track?.hasPointerCapture(event.pointerId)) track.releasePointerCapture(event.pointerId);
		dragFrame = requestAnimationFrame(settle);
	}

	function nudge(event: KeyboardEvent, mode: 'start' | 'end') {
		const step = event.shiftKey ? 60 : 15;
		const delta =
			event.key === 'ArrowLeft' || event.key === 'ArrowDown'
				? -step
				: event.key === 'ArrowRight' || event.key === 'ArrowUp'
					? step
					: 0;
		if (!delta) return;
		event.preventDefault();
		moveTo(mode, (mode === 'start' ? start : end) + delta);
	}

	function background(block: TrackBlock) {
		if (block.edge) return block.raised ? 'var(--day-event)' : 'var(--day-routine)';
		// Told by the strip of its colour down the edge, as on the week, not by a fill.
		if (block.style === 'pattern') return 'color-mix(in oklch, var(--foreground) 6%, transparent)';
		if (block.style === 'wall')
			return 'repeating-linear-gradient(135deg, color-mix(in oklch, var(--foreground) 13%, transparent) 0 5px, transparent 5px 10px)';
		if (block.style === 'pending') return 'color-mix(in oklch, var(--foreground) 5%, transparent)';
		return 'color-mix(in oklch, var(--foreground) 9%, transparent)';
	}
</script>

<div
	bind:this={track}
	role="group"
	aria-label="Time of day"
	class="relative h-20 touch-none select-none"
	onpointermove={updateDrag}
	onpointerup={endDrag}
	onpointercancel={endDrag}
>
	{#each hourMarks as mark (mark.minutes)}
		<div
			class="pointer-events-none absolute top-0 bottom-0"
			style:left={`${percent(mark.minutes)}%`}
		>
			{#if mark.label}
				<span
					class={[
						'absolute top-0 whitespace-nowrap text-sm font-medium text-muted-foreground',
						edgeAlign(mark.minutes)
					]}>{hourLabel(mark.minutes)}</span
				>
			{/if}
			<div
				class={[
					'absolute top-6 bottom-0 w-px',
					mark.minutes % 1440 === 0
						? 'bg-foreground/35'
						: mark.label
							? 'bg-foreground/15'
							: 'bg-foreground/[0.07]'
				]}
			></div>
		</div>
	{/each}
	<div class="pointer-events-none absolute inset-x-0 top-7 bottom-1 rounded-xl bg-muted/70"></div>
	{#each shown as block (block.key)}
		<div
			class={[
				'pointer-events-none absolute top-7 bottom-1 flex items-center justify-center overflow-hidden rounded-lg text-sm font-medium',
				block.style === 'pending' && 'border border-dashed border-foreground/35',
				block.edge && [
					'border',
					block.raised ? 'z-[1] border-foreground/30 shadow-md' : 'border-foreground/15'
				],
				block.highlight === 'error'
					? 'text-destructive ring-2 ring-inset ring-destructive'
					: block.highlight === 'warning'
						? 'text-warning ring-2 ring-inset ring-warning'
						: 'text-muted-foreground'
			]}
			style:left={`${percent(block.start)}%`}
			style:width={`${percent(block.end) - percent(block.start)}%`}
			style:background={background(block)}
			style:border-left={block.edge
				? `3px solid ${block.edge}`
				: block.style === 'pattern'
					? `3px solid ${block.color ?? 'var(--primary)'}`
					: undefined}
		>
			{#if block.label}<span class="truncate px-2">{block.label}</span>{/if}
		</div>
	{/each}
	{#if showBlock}
		<div
			role="presentation"
			class={[
				'absolute top-7 bottom-1 z-10 flex items-center justify-center rounded-lg text-body font-semibold text-primary-foreground shadow-sm',
				!disabled && (drag?.mode === 'move' ? 'cursor-grabbing' : 'cursor-grab')
			]}
			style:left={`${percent(start)}%`}
			style:width={`${percent(end) - percent(start)}%`}
			style:background={color}
			onpointerdown={(event) => startDrag(event, 'move')}
		>
			<button
				type="button"
				aria-label={`Start time, ${formatClock(start)}`}
				class="absolute inset-y-0 left-0 grid w-3.5 cursor-ew-resize place-items-center rounded-l-lg"
				onpointerdown={(event) => startDrag(event, 'start')}
				onkeydown={(event) => nudge(event, 'start')}
				><span class="h-5 w-0.5 rounded-full bg-primary-foreground/70"></span></button
			>
			<button
				type="button"
				aria-label={`End time, ${formatClock(end)}`}
				class="absolute inset-y-0 right-0 grid w-3.5 cursor-ew-resize place-items-center rounded-r-lg"
				onpointerdown={(event) => startDrag(event, 'end')}
				onkeydown={(event) => nudge(event, 'end')}
				><span class="h-5 w-0.5 rounded-full bg-primary-foreground/70"></span></button
			>
		</div>
	{:else if emptyText}
		<p
			class="pointer-events-none absolute inset-x-0 top-7 bottom-1 grid place-items-center text-body text-muted-foreground"
		>
			{emptyText}
		</p>
	{/if}
</div>
