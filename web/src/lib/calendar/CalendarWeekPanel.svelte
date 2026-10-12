<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { ChevronLeft, ChevronRight, LoaderCircle, X } from '@lucide/svelte';
	import { apiRequest, cachedFetch } from '#lib/core/api.ts';
	import {
		addDays,
		blockEdgeColor,
		type CalendarClash,
		type CalendarDay,
		type CalendarEvent,
		type CalendarRangePayload,
		type CalendarScheduleBlock,
		type EventEditorValue,
		fromEvent,
		minutesOnDay,
		toEventInput
	} from '#lib/calendar/calendar.ts';
	import type { RoutineRef } from '#lib/calendar/RoutineConflicts.svelte';
	import { formatClock } from '#lib/calendar/TimeTrack.svelte';
	import CalendarTimelinePanel from '#lib/calendar/CalendarTimelinePanel.svelte';

	// The week, time running down: routines shaded behind, events drawn over them.
	// The whole day fits the height there is, so the week never scrolls. Drag across
	// open time to add an event. Clicking an event selects it and shows its details
	// beside the week; only once Edit is chosen there can it be dragged (to another
	// day too) or have either edge dragged to change when it starts or ends, and
	// what was dragged is saved with the rest from the editor. The panel beside
	// the week is always open on one of its days: today, or the week's first day.
	//
	// Given a `day`, it is that one day of the week and nothing else: the month
	// opens a day this way, so a day reads and works there as it does on the week.
	let {
		initialDate = '',
		initialBlockId = '',
		revision = 0,
		day = '',
		onChanged,
		onLeaveDay,
		onManageRoutine
	}: {
		initialDate?: string;
		initialBlockId?: string;
		/** Show only this day, with no way round the week: the month has its own. */
		day?: string;
		/** Something on these dates was saved. */
		onChanged?: (dates: string[]) => void | Promise<void>;
		/** With a `day`: the panel led on to another day, which is the owner's to open. */
		onLeaveDay?: (date: string, blockId: string) => void;
		/** Bumped when the chat changed the calendar: load the week again. */
		revision?: number;
		onManageRoutine: (routine: RoutineRef) => void;
	} = $props();

	const SNAP = 15;
	const HOURS = Array.from({ length: 23 }, (_, index) => index + 1);

	type WeekBlock = { block: CalendarScheduleBlock; key: string; start: number; end: number };
	type Piece = WeekBlock & {
		top: number;
		bottom: number;
		clippedTop: boolean;
		clippedEnd: boolean;
	};
	type Inspector = {
		date: string;
		blockId: string;
		span: { start: number; end: number } | null;
		key: number;
	};
	/** Moving an event, or dragging where it ends ('resize') or where it starts. */
	type Retime = 'move' | 'resize' | 'resizeStart';
	type Drag =
		| {
				kind: 'create';
				day: number;
				anchor: number;
				from: number;
				to: number;
				moved: boolean;
		  }
		| {
				kind: Retime;
				eventId: string;
				blockId: string;
				day: number;
				downAt: number;
				shift: number;
				x: number;
				y: number;
				moved: boolean;
		  };

	let weekStart = $state(sundayOf(untrack(() => initialDate) || todayKey()));
	let days = $state<CalendarDay[]>([]);
	let loading = $state(false);
	let loadError = $state('');
	let inspector = $state<Inspector>(
		untrack(() =>
			initialDate && initialBlockId
				? { date: initialDate, blockId: initialBlockId, span: null, key: 0 }
				: {
						date: day || dayOfWeek(sundayOf(initialDate || todayKey())),
						blockId: '',
						span: null,
						key: 0
					}
		)
	);
	let inspectorKey = 0;
	let drag = $state<Drag | null>(null);
	// The event being added in the editor, drawn on the week and dragged there.
	let draft = $state<EventEditorValue | null>(null);
	let draftDrag = $state<{
		kind: Retime;
		downAt: number;
		origin: { date: string; start: number; end: number };
	} | null>(null);
	// The event being edited in the editor, as it stands there unsaved.
	let preview = $state<{
		eventId: string;
		title: string;
		date: string;
		start: number;
		end: number;
	} | null>(null);
	// The editor beside the week, which takes the times an edited event is dragged to.
	let panel = $state<
		{ setTimes: (times: { date: string; start: number; end: number }) => void } | undefined
	>();
	/** The one event that can be dragged: the one being added, or the one being edited. */
	const active = $derived(
		draft ? { title: draft.title, date: draft.date, start: draft.start, end: draft.end } : preview
	);

	function retimeActive(times: { date: string; start: number; end: number }) {
		if (draft) draft = { ...draft, ...times };
		else panel?.setTimes(times);
	}
	// A move saved but not yet reloaded: drawn where it went, not where it was.
	let pending = $state<{ eventId: string; start: number; end: number } | null>(null);
	let undo = $state<{ eventId: string; value: EventEditorValue; label: string } | null>(null);
	let undoTimer: ReturnType<typeof setTimeout> | undefined;
	let actionError = $state('');
	let now = $state(new Date());
	let columns = $state<HTMLDivElement[]>([]);

	const dates = $derived(Array.from({ length: 7 }, (_, index) => addDays(weekStart, index)));
	const today = $derived(formatDateKey(now));
	const nowMinute = $derived(now.getHours() * 60 + now.getMinutes());
	const weekLabel = $derived(formatWeekLabel(weekStart));

	function todayKey() {
		return formatDateKey(new Date());
	}

	function formatDateKey(value: Date) {
		return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
	}

	/** Whole days from the week's first day to `date`. */
	function dayOffset(date: string) {
		return Math.round(
			(Date.parse(`${date}T00:00:00Z`) - Date.parse(`${weekStart}T00:00:00Z`)) / 86_400_000
		);
	}

	/** The day a week opens on: today when it is in that week, or the week's first day. */
	function dayOfWeek(start: string) {
		const current = todayKey();
		return current >= start && current <= addDays(start, 6) ? current : start;
	}

	function sundayOf(date: string) {
		return addDays(date, -new Date(`${date}T00:00:00`).getDay());
	}

	function formatWeekLabel(start: string) {
		const first = new Date(`${start}T00:00:00`);
		const last = new Date(`${addDays(start, 6)}T00:00:00`);
		const month = (value: Date) => value.toLocaleDateString(undefined, { month: 'short' });
		const head = `${month(first)} ${first.getDate()}`;
		const tail =
			first.getMonth() === last.getMonth()
				? `${last.getDate()}`
				: `${month(last)} ${last.getDate()}`;
		return `${head} – ${tail}, ${last.getFullYear()}`;
	}

	function hourLabel(hour: number) {
		return `${hour % 12 || 12} ${hour < 12 ? 'AM' : 'PM'}`;
	}

	async function loadWeek(start: string) {
		loading = true;
		const first = start.slice(0, 7);
		const last = addDays(start, 6).slice(0, 7);
		try {
			const payload = await cachedFetch<CalendarRangePayload>(
				`/api/calendar/range?start=${first}&months=${first === last ? 1 : 2}`,
				fetch
			);
			if (start !== weekStart) return;
			const loaded = new Map(
				Object.values(payload.months)
					.flat()
					.map((day) => [day.date, day])
			);
			days = Array.from({ length: 7 }, (_, index) => {
				const date = addDays(start, index);
				return loaded.get(date) ?? { date, blocks: [] };
			});
			loadError = '';
		} catch (error) {
			loadError = error instanceof Error ? error.message : 'Unable to load the calendar.';
		} finally {
			if (start === weekStart) loading = false;
		}
	}

	$effect(() => {
		const start = weekStart;
		void revision;
		untrack(() => void loadWeek(start));
	});

	async function reload() {
		await loadWeek(weekStart);
		pending = null;
		await onChanged?.(dates);
	}

	function goToWeek(date: string) {
		const start = sundayOf(date);
		if (start === weekStart) return;
		weekStart = start;
		days = [];
	}

	function shiftWeek(weeks: number) {
		goToWeek(addDays(weekStart, weeks * 7));
		openInspector(dayOfWeek(weekStart), '');
	}

	// Every block once, in minutes from the week's first midnight: an overnight
	// block is on both its days under ids that differ only in the day.
	const weekBlocks = $derived.by(() => {
		const seen = new Map<string, WeekBlock>();
		for (const day of days) {
			for (const block of day.blocks) {
				const key = block.id.replace(/:day:[^:]+$/, '');
				if (seen.has(key)) continue;
				seen.set(key, {
					block,
					key,
					start: minutesOnDay(block.start, weekStart),
					end: minutesOnDay(block.end, weekStart)
				});
			}
		}
		return [...seen.values()];
	});

	/**
	 * A span moved by `shift` minutes, or with one end dragged by them. An event is
	 * at least one step long and at most a day, and either end can cross midnight.
	 */
	function retimed(span: { start: number; end: number }, kind: Retime, shift: number) {
		if (kind === 'move') return { start: span.start + shift, end: span.end + shift };
		if (kind === 'resize') {
			const end = Math.min(span.start + 1440, Math.max(span.start + SNAP, span.end + shift));
			return { start: span.start, end };
		}
		const start = Math.max(span.end - 1440, Math.min(span.end - SNAP, span.start + shift));
		return { start, end: span.end };
	}

	/** Where an event shows right now: dragged, or saved and waiting on the reload. */
	function placed(item: WeekBlock) {
		const eventId = item.block.event_id;
		if (drag && drag.kind !== 'create' && drag.eventId === eventId && drag.moved) {
			return retimed(item, drag.kind, drag.shift);
		}
		if (pending && pending.eventId === eventId) return pending;
		if (preview && preview.eventId === eventId) {
			const base = dayOffset(preview.date) * 1440;
			return { start: base + preview.start, end: base + preview.end };
		}
		return item;
	}

	/** The part of the event being added that falls on a day of the week. */
	function draftOn(index: number) {
		if (!active) return null;
		const start = dayOffset(active.date) * 1440 + active.start;
		const end = start + (active.end - active.start);
		const top = Math.max(0, start - index * 1440);
		const bottom = Math.min(1440, end - index * 1440);
		return bottom > top
			? { top, bottom, clippedTop: start < index * 1440, clippedEnd: end > (index + 1) * 1440 }
			: null;
	}

	function startDraftDrag(event: PointerEvent, kind: Retime) {
		if (event.button !== 0 || !active) return;
		event.stopPropagation();
		event.preventDefault();
		const point = pointAt(event.clientX, event.clientY);
		draftDrag = {
			kind,
			downAt: point.day * 1440 + point.minute,
			origin: { date: active.date, start: active.start, end: active.end }
		};
	}

	/** The event being added or edited follows the pointer, on the grid and in the editor at once. */
	function dragDraft(event: PointerEvent) {
		if (!draftDrag || !active) return;
		const point = pointAt(event.clientX, event.clientY);
		const { origin } = draftDrag;
		const shift = snap(point.day * 1440 + point.minute - draftDrag.downAt);
		// In minutes from the week's first midnight, then back onto its own day.
		const base = dayOffset(origin.date) * 1440;
		const span = retimed(
			{ start: base + origin.start, end: base + origin.end },
			draftDrag.kind,
			shift
		);
		const start = Math.min(7 * 1440 - SNAP, Math.max(0, span.start));
		const end = draftDrag.kind === 'move' ? start + (span.end - span.start) : span.end;
		const day = Math.floor(start / 1440);
		retimeActive({
			date: addDays(weekStart, day),
			start: start - day * 1440,
			end: Math.max(start + SNAP, end) - day * 1440
		});
	}

	function piecesOn(index: number, items: WeekBlock[]): Piece[] {
		const dayStart = index * 1440;
		return items.flatMap((item) => {
			const { start, end } = placed(item);
			const top = Math.max(0, start - dayStart);
			const bottom = Math.min(1440, end - dayStart);
			if (bottom <= top) return [];
			return [
				{ ...item, top, bottom, clippedTop: start < dayStart, clippedEnd: end > dayStart + 1440 }
			];
		});
	}

	/** Events that overlap share the column's width, side by side. */
	function sideBySide(pieces: Piece[]) {
		const sorted = [...pieces].sort((a, b) => a.top - b.top || b.bottom - a.bottom);
		const laid: { piece: Piece; lane: number; lanes: number }[] = [];
		let group: typeof laid = [];
		let groupEnd = -1;
		const close = () => {
			const lanes = Math.max(1, ...group.map((entry) => entry.lane + 1));
			for (const entry of group) entry.lanes = lanes;
			laid.push(...group);
			group = [];
		};
		for (const piece of sorted) {
			if (piece.top >= groupEnd) {
				close();
				groupEnd = -1;
			}
			const laneEnds: number[] = [];
			for (const entry of group) {
				laneEnds[entry.lane] = Math.max(laneEnds[entry.lane] ?? 0, entry.piece.bottom);
			}
			let lane = laneEnds.findIndex((laneEnd) => laneEnd === undefined || laneEnd <= piece.top);
			if (lane === -1) lane = laneEnds.length;
			group.push({ piece, lane, lanes: 1 });
			groupEnd = Math.max(groupEnd, piece.bottom);
		}
		close();
		return laid;
	}

	const columnsOfWeek = $derived(
		dates.map((date, index) => ({
			date,
			routines: piecesOn(
				index,
				weekBlocks.filter((item) => item.block.kind !== 'event')
			),
			// The event being edited is drawn apart, where it is being dragged to.
			events: sideBySide(
				piecesOn(
					index,
					weekBlocks.filter(
						(item) => item.block.kind === 'event' && item.block.event_id !== preview?.eventId
					)
				)
			)
		}))
	);

	/**
	 * Everything on the week is drawn as the same card, told apart by the strip of
	 * colour down its edge: an event's is the ink, a routine's its own. A routine
	 * sits a shade back, so an event laid over one still reads as on top of it.
	 */
	const BLOCK =
		'fit-block absolute cursor-pointer overflow-hidden rounded-md border border-l-[3px] px-1.5 py-1 text-left text-note leading-tight';

	function blockLook(kind: CalendarScheduleBlock['kind'], picked: boolean) {
		if (picked) return 'z-[5] border-primary bg-primary text-primary-foreground shadow-lg';
		return kind === 'event'
			? 'border-border bg-card text-foreground shadow-sm hover:border-primary/50'
			: 'border-border/70 bg-muted/70 text-foreground hover:border-primary/40';
	}

	/** The columns drawn, each with its place in the week: all seven, or the one day. */
	const shownColumns = $derived(
		columnsOfWeek
			.map((column, index) => ({ column, index }))
			.filter(({ column }) => !day || column.date === day)
	);
	const gridColumns = $derived(
		`grid-template-columns: 3.5rem repeat(${day ? 1 : 7}, minmax(0, 1fr));`
	);

	function percent(minutes: number) {
		return `${(minutes / 1440) * 100}%`;
	}

	function blockTimes(item: WeekBlock) {
		const { start, end } = placed(item);
		return `${formatClock(((start % 1440) + 1440) % 1440)} – ${formatClock(((end % 1440) + 1440) % 1440)}`;
	}

	function isSelected(item: WeekBlock) {
		return (
			Boolean(inspector?.blockId) && inspector!.blockId.replace(/:day:[^:]+$/, '') === item.key
		);
	}

	// Pointer: where on the week it is, by column and minute.
	function pointAt(clientX: number, clientY: number) {
		let day = 0;
		columns.forEach((column, index) => {
			if (column && clientX >= column.getBoundingClientRect().left) day = index;
		});
		const rect = columns[day]?.getBoundingClientRect();
		if (!rect) return { day, minute: 0 };
		return {
			day,
			minute: Math.min(1440, Math.max(0, ((clientY - rect.top) / rect.height) * 1440))
		};
	}

	const snap = (minutes: number) => Math.round(minutes / SNAP) * SNAP;

	function startCreate(event: PointerEvent, day: number) {
		if (event.button !== 0 || pending) return;
		const anchor = Math.min(
			1440 - SNAP,
			Math.floor(pointAt(event.clientX, event.clientY).minute / SNAP) * SNAP
		);
		drag = { kind: 'create', day, anchor, from: anchor, to: anchor + SNAP, moved: false };
		event.preventDefault();
	}

	function onPointerMove(event: PointerEvent) {
		if (draftDrag) return dragDraft(event);
		if (!drag) return;
		const point = pointAt(event.clientX, event.clientY);
		if (drag.kind === 'create') {
			const at = snap(point.minute);
			drag.moved ||= Math.abs(point.minute - drag.anchor) > SNAP;
			drag.from = Math.min(at, drag.anchor);
			drag.to = at > drag.anchor ? at : drag.anchor + SNAP;
			return;
		}
		drag.moved ||= Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 4;
		if (!drag.moved) return;
		// Either end follows the pointer into another day, as a move does.
		drag.shift = snap(point.day * 1440 + point.minute - drag.downAt);
	}

	function onPointerUp() {
		if (draftDrag) {
			draftDrag = null;
			return;
		}
		const finished = drag;
		drag = null;
		if (!finished) return;
		if (finished.kind === 'create') {
			// Only a drag adds an event. A click selects the routine under it, or does nothing.
			if (finished.moved) {
				openInspector(dates[finished.day], '', { start: finished.from, end: finished.to });
				return;
			}
			const under = columnsOfWeek[finished.day].routines
				.filter((piece) => finished.anchor >= piece.top && finished.anchor < piece.bottom)
				.sort((a, b) => a.bottom - a.top - (b.bottom - b.top))[0];
			if (under) openInspector(dates[finished.day], under.block.id);
			return;
		}
		if (!finished.moved) {
			openInspector(dates[finished.day], finished.blockId);
			return;
		}
		if (finished.shift === 0) return;
		const item = weekBlocks.find((entry) => entry.block.event_id === finished.eventId);
		if (item) {
			pending = { eventId: finished.eventId, ...retimed(item, finished.kind, finished.shift) };
		}
		void retime(finished.eventId, finished.kind, finished.shift);
	}

	/** Move an event, or where it ends, by `shift` minutes, and save it straight away. */
	async function retime(eventId: string, kind: Retime, shift: number) {
		actionError = '';
		try {
			const event = await apiRequest<CalendarEvent>(`/api/calendar/events/${eventId}`);
			const before = fromEvent(event);
			const after = onItsDay({ ...before, ...retimed(before, kind, shift) });
			await saveTimes(eventId, after, event.updated_at);
			offerUndo(
				eventId,
				before,
				kind === 'move' ? `Moved “${before.title}”` : `Changed “${before.title}”`
			);
		} catch (error) {
			actionError = error instanceof Error ? error.message : 'Unable to move this event.';
		} finally {
			await reload();
			if (inspector) inspector = { ...inspector, key: ++inspectorKey };
		}
	}

	/** Times past either midnight belong to that day: the date follows them. */
	function onItsDay(value: EventEditorValue): EventEditorValue {
		const days = Math.floor(value.start / 1440);
		return {
			...value,
			date: addDays(value.date, days),
			start: value.start - days * 1440,
			end: value.end - days * 1440
		};
	}

	/** Save new times, keeping only the routine changes it still overlaps. */
	async function saveTimes(eventId: string, value: EventEditorValue, updatedAt: string) {
		let routineChanges = value.routine_changes;
		if (routineChanges.length) {
			const input = toEventInput(value);
			const clashes = await apiRequest<CalendarClash[]>('/api/calendar/clashes', {
				method: 'POST',
				body: {
					date: input.date,
					start: input.start,
					end: input.end,
					end_day_offset: input.end_day_offset,
					event_id: eventId,
					margins: Object.fromEntries(
						routineChanges.map((change) => [
							`${change.window_id}:${change.occurrence_date}`,
							change.margin_minutes
						])
					)
				}
			});
			const offered = new Map(
				clashes
					.filter((clash) => clash.type === 'routine' && !clash.locked)
					.map((clash) => [clash.key, clash])
			);
			routineChanges = routineChanges.filter((change) =>
				offered
					.get(`${change.window_id}:${change.occurrence_date}`)
					?.options.some((option) => option.operation === change.operation)
			);
		}
		await apiRequest<CalendarEvent>(`/api/calendar/events/${eventId}`, {
			method: 'PUT',
			body: {
				...toEventInput({ ...value, routine_changes: routineChanges }),
				expected_updated_at: updatedAt
			},
			invalidate: ['/api/calendar']
		});
	}

	function offerUndo(eventId: string, value: EventEditorValue, label: string) {
		clearTimeout(undoTimer);
		undo = { eventId, value, label };
		undoTimer = setTimeout(() => (undo = null), 8000);
	}

	async function undoLast() {
		if (!undo) return;
		const { eventId, value } = undo;
		undo = null;
		clearTimeout(undoTimer);
		actionError = '';
		try {
			const event = await apiRequest<CalendarEvent>(`/api/calendar/events/${eventId}`);
			await saveTimes(eventId, value, event.updated_at);
		} catch (error) {
			actionError = error instanceof Error ? error.message : 'Unable to undo that.';
		} finally {
			await reload();
			if (inspector) inspector = { ...inspector, key: ++inspectorKey };
		}
	}

	function openInspector(
		date: string,
		blockId: string,
		span: { start: number; end: number } | null = null
	) {
		draft = null;
		preview = null;
		inspector = { date, blockId, span, key: ++inspectorKey };
	}

	// The event being added or edited was given a date in another week: go there.
	$effect(() => {
		const date = active?.date;
		if (date && !day && !dates.includes(date)) untrack(() => goToWeek(date));
	});

	/** The editor led on to another day: follow it, into another week if need be. */
	function followDay(date: string) {
		if (day) {
			if (date !== day) onLeaveDay?.(date, '');
			return;
		}
		goToWeek(date);
		if (inspector) inspector = { ...inspector, date };
	}

	function goToday() {
		goToWeek(today);
		openInspector(today, '');
	}

	/** The panel led on to another day, or to something on it. */
	function openDay(date: string, blockId: string) {
		if (day && date !== day) {
			onLeaveDay?.(date, blockId);
			return;
		}
		if (blockId) {
			goToWeek(date);
			openInspector(date, blockId);
		} else followDay(date);
	}

	/** Whether the panel is on something, not only on a day. */
	const focused = $derived(Boolean(inspector.blockId || inspector.span));

	function typing(target: EventTarget | null) {
		return (
			target instanceof HTMLElement &&
			(target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
		);
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && focused && !typing(event.target)) {
			openInspector(inspector.date, '');
			event.preventDefault();
			return;
		}
		if (typing(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
		// The month has its own keys for getting about.
		if (day) return;
		if (event.key === 't') goToday();
		else if (event.key === 'ArrowLeft') shiftWeek(-1);
		else if (event.key === 'ArrowRight') shiftWeek(1);
		else return;
		event.preventDefault();
	}

	onMount(() => {
		// Capture, so Escape closes the editor before the calendar hears it.
		window.addEventListener('keydown', onKeydown, { capture: true });
		const clock = setInterval(() => (now = new Date()), 60_000);
		return () => {
			window.removeEventListener('keydown', onKeydown, { capture: true });
			clearInterval(clock);
			clearTimeout(undoTimer);
		};
	});
</script>

<svelte:window
	onpointermove={onPointerMove}
	onpointerup={onPointerUp}
	onpointercancel={() => {
		drag = null;
		draftDrag = null;
	}}
/>

<div class="flex h-full min-h-0 flex-col">
	{#if !day}
		<div class="mx-auto mb-3 flex shrink-0 items-center gap-2">
			<div
				class="flex h-12 w-72 items-center justify-between rounded-full border border-border/80 bg-card/75 p-1 shadow-raised"
			>
				<button
					type="button"
					aria-label="Previous week"
					title="Previous week (←)"
					class="nav-button"
					onclick={() => shiftWeek(-1)}><ChevronLeft class="size-[1.125rem]" /></button
				>
				<p
					class="inline-flex min-w-0 items-center justify-center gap-2 truncate text-base font-semibold"
					aria-live="polite"
				>
					{#if loading && !days.length}<LoaderCircle class="size-4 shrink-0 animate-spin" />{/if}
					{weekLabel}
				</p>
				<button
					type="button"
					aria-label="Next week"
					title="Next week (→)"
					class="nav-button"
					onclick={() => shiftWeek(1)}><ChevronRight class="size-[1.125rem]" /></button
				>
			</div>
			<button
				type="button"
				title="Today (T)"
				class="h-12 rounded-full border border-border/80 bg-card/75 px-4 text-body font-medium shadow-raised hover:bg-muted disabled:opacity-50"
				disabled={dates.includes(today) && inspector.date === today && !focused}
				onclick={goToday}>Today</button
			>
		</div>
	{/if}

	<div class={['relative flex min-h-0 flex-1', !day && 'gap-3']}>
		<section
			class={[
				'flex min-h-0 min-w-0 flex-col overflow-hidden',
				day ? 'flex-1 py-2' : 'flex-1 rounded-xl border border-border/80 bg-card/75 shadow-raised'
			]}
			aria-label={day ? 'Day' : 'Week'}
		>
			{#if loadError}
				<p class="px-4 pt-3 text-body text-destructive" role="alert">{loadError}</p>
			{/if}
			{#if !day}
				<div class="grid shrink-0 border-b border-border/70 pr-2" style={gridColumns}>
					<div></div>
					{#each dates as date (date)}
						{@const value = new Date(`${date}T00:00:00`)}
						{@const isToday = date === today}
						<button
							type="button"
							aria-current={isToday ? 'date' : undefined}
							aria-label={value.toLocaleDateString(undefined, {
								weekday: 'long',
								month: 'long',
								day: 'numeric'
							})}
							class={[
								'flex flex-col items-center gap-0.5 rounded-lg py-2 transition-colors hover:bg-muted/60',
								date < today && 'text-muted-foreground',
								inspector?.date === date && !inspector.blockId && !inspector.span && 'bg-muted'
							]}
							onclick={() => openInspector(date, '')}
						>
							<span class="text-note font-semibold tracking-wide uppercase text-muted-foreground"
								>{value.toLocaleDateString(undefined, { weekday: 'short' })}</span
							>
							<span
								class={[
									'flex size-9 items-center justify-center rounded-full text-xl font-semibold tabular-nums',
									isToday && 'bg-primary text-primary-foreground'
								]}>{value.getDate()}</span
							>
						</button>
					{/each}
				</div>
			{/if}

			<div class="min-h-0 flex-1 overflow-hidden pr-2">
				<div class="grid h-full" style={gridColumns}>
					<div class="relative" aria-hidden="true">
						{#each HOURS as hour (hour)}
							<span
								class="absolute right-2 -translate-y-1/2 text-caption tabular-nums text-muted-foreground"
								style={`top: ${percent(hour * 60)};`}>{hourLabel(hour)}</span
							>
						{/each}
					</div>

					{#each shownColumns as { column, index: dayIndex } (column.date)}
						{@const draftPiece = draftOn(dayIndex)}
						<div
							bind:this={columns[dayIndex]}
							class={[
								'day-column relative border-l border-border/60 select-none',
								column.date < today && 'bg-muted/25',
								drag?.kind === 'create' && drag.moved ? 'cursor-ns-resize' : 'cursor-cell'
							]}
							role="presentation"
							onpointerdown={(event) => startCreate(event, dayIndex)}
						>
							{#each column.routines as piece (piece.key)}
								{@const picked = isSelected(piece)}
								<!-- The column under it hears the press: a click selects the routine, a drag adds an event. -->
								<div
									class={[BLOCK, 'inset-x-0.5', blockLook(piece.block.kind, picked)]}
									style={`top: ${percent(piece.top)}; height: calc(${percent(piece.bottom - piece.top)} - 2px); ${picked ? '' : `border-left-color: ${blockEdgeColor(piece.block.kind)};`}`}
								>
									{#if piece.bottom - piece.top >= 45}
										{@const quiet = picked ? 'text-primary-foreground/80' : 'text-muted-foreground'}
										<p class="truncate">
											<button
												type="button"
												class="rounded font-semibold hover:underline"
												onpointerdown={(event) => event.stopPropagation()}
												onclick={() => openInspector(column.date, piece.block.id)}
												>{piece.block.title}</button
											>
											<span class={['fit-inline tabular-nums', quiet]}>{blockTimes(piece)}</span>
										</p>
										<p class={['fit-second truncate tabular-nums', quiet]}>
											{blockTimes(piece)}
										</p>
									{/if}
								</div>
							{/each}

							{#each column.events as { piece, lane, lanes } (piece.key)}
								{@const quiet = isSelected(piece)
									? 'text-primary-foreground/80'
									: 'text-muted-foreground'}
								<div
									class={['event-block', BLOCK, blockLook('event', isSelected(piece))]}
									style={`top: ${percent(piece.top)}; height: calc(${percent(piece.bottom - piece.top)} - 2px); left: calc(${(lane / lanes) * 100}% + 2px); width: calc(${100 / lanes}% - 4px); ${isSelected(piece) ? '' : `border-left-color: ${blockEdgeColor('event')};`}`}
									role="button"
									tabindex="0"
									aria-label={`${piece.block.title}, ${blockTimes(piece)}`}
									onpointerdown={(event) => event.stopPropagation()}
									onclick={() => openInspector(column.date, piece.block.id)}
									onkeydown={(event) => {
										if (event.key === 'Enter' || event.key === ' ') {
											event.preventDefault();
											openInspector(column.date, piece.block.id);
										}
									}}
								>
									<!-- A line shows only where there is a whole one for it: half a line reads
									     as a fault. Without room for a second, the times sit beside the title. -->
									<p class="truncate">
										<span class="font-semibold">{piece.block.title}</span>
										<span class={['fit-inline tabular-nums', quiet]}>{blockTimes(piece)}</span>
									</p>
									<p class={['fit-second truncate tabular-nums', quiet]}>
										{blockTimes(piece)}
									</p>
									{#if piece.block.location}
										<p class={['fit-third truncate', quiet]}>
											{piece.block.location}
										</p>
									{/if}
								</div>
							{/each}

							{#if draftPiece && active}
								<div
									class={[
										'fit-block absolute inset-x-0.5 z-10 overflow-hidden rounded-md border border-primary bg-primary px-1.5 py-1 text-note leading-tight text-primary-foreground shadow-lg',
										draftDrag ? 'cursor-grabbing' : 'cursor-grab'
									]}
									style={`top: ${percent(draftPiece.top)}; height: calc(${percent(draftPiece.bottom - draftPiece.top)} - 2px);`}
									role="presentation"
									onpointerdown={(event) => startDraftDrag(event, 'move')}
								>
									<p class="truncate">
										<span class="font-semibold">{active.title.trim() || 'New event'}</span>
										<span class="fit-inline tabular-nums text-primary-foreground/80"
											>{formatClock(active.start % 1440)} – {formatClock(active.end % 1440)}</span
										>
									</p>
									<p class="fit-second truncate tabular-nums text-primary-foreground/80">
										{formatClock(active.start % 1440)} – {formatClock(active.end % 1440)}
									</p>
									{#if !draftPiece.clippedTop}
										<span
											class="resize-grip resize-grip-top absolute inset-x-0 top-0 h-3 max-h-1/3 cursor-ns-resize"
											aria-hidden="true"
											onpointerdown={(event) => startDraftDrag(event, 'resizeStart')}
										></span>
									{/if}
									{#if !draftPiece.clippedEnd}
										<span
											class="resize-grip absolute inset-x-0 bottom-0 h-3 max-h-1/3 cursor-ns-resize"
											aria-hidden="true"
											onpointerdown={(event) => startDraftDrag(event, 'resize')}
										></span>
									{/if}
								</div>
							{/if}

							{#if drag?.kind === 'create' && drag.moved && drag.day === dayIndex}
								<div
									class="pointer-events-none absolute inset-x-0.5 z-10 rounded-md border border-dashed border-primary bg-primary/10 px-1.5 py-1 text-note"
									style={`top: ${percent(drag.from)}; height: ${percent(drag.to - drag.from)};`}
								>
									<p class="font-semibold">New event</p>
									<p class="tabular-nums text-muted-foreground">
										{formatClock(drag.from)} – {formatClock(drag.to)}
									</p>
								</div>
							{/if}

							{#if column.date === today}
								<div
									class="pointer-events-none absolute inset-x-0 z-20 h-0.5 bg-primary"
									style={`top: ${percent(nowMinute)};`}
									aria-hidden="true"
								>
									<span class="absolute -top-1 -left-1 size-2.5 rounded-full bg-primary"></span>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		</section>

		{#if inspector}
			{@const shownDay = active?.date ?? inspector.date}
			<!-- Beside the week where there is room; over it, on a narrow screen, only for something selected. -->
			<aside
				class={[
					'absolute inset-0 z-30 min-h-0 flex-col overflow-hidden bg-card md:static md:z-auto md:flex',
					day
						? 'border-l border-border/70 md:w-[26rem] md:shrink-0 xl:w-[30rem]'
						: 'rounded-xl border border-border/90 shadow-xl md:w-[30rem] md:shrink-0 xl:w-[34rem]',
					focused ? 'flex' : 'hidden'
				]}
				aria-label="Day"
			>
				<div class="flex shrink-0 items-center justify-between border-b border-border/70 px-4 py-3">
					<div class="min-w-0">
						<h3 class="truncate text-base font-semibold leading-tight">
							{new Date(`${shownDay}T00:00:00`).toLocaleDateString(undefined, {
								weekday: 'long'
							})}
						</h3>
						<p class="text-body text-muted-foreground">
							{new Date(`${shownDay}T00:00:00`).toLocaleDateString(undefined, {
								month: 'long',
								day: 'numeric'
							})}
						</p>
					</div>
					{#if focused}
						<button
							type="button"
							aria-label="Back to the day"
							title="Back to the day (Esc)"
							class="inline-flex size-8 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
							onclick={() => openInspector(inspector.date, '')}><X class="size-4" /></button
						>
					{/if}
				</div>
				<div class="min-h-0 flex-1">
					{#key inspector.key}
						<CalendarTimelinePanel
							{days}
							selectedDate={inspector.date}
							initialBlockId={inspector.blockId}
							initialSpan={inspector.span}
							bind:this={panel}
							bind:newEvent={draft}
							bind:preview
							onChanged={reload}
							onOpenDay={openDay}
							{onManageRoutine}
						/>
					{/key}
				</div>
			</aside>
		{/if}

		{#if undo || actionError}
			<div
				class="absolute bottom-4 left-1/2 z-40 flex -translate-x-1/2 items-center gap-3 rounded-full border border-border bg-card px-4 py-2 text-body shadow-xl"
				role="status"
			>
				{#if actionError}
					<span class="text-destructive">{actionError}</span>
					<button
						type="button"
						class="font-medium text-muted-foreground hover:text-foreground"
						onclick={() => (actionError = '')}>Dismiss</button
					>
				{:else if undo}
					<span class="max-w-64 truncate">{undo.label}</span>
					<button
						type="button"
						class="font-semibold text-primary hover:underline"
						onclick={undoLast}>Undo</button
					>
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	.nav-button {
		display: inline-flex;
		width: 2.5rem;
		height: 2.5rem;
		align-items: center;
		justify-content: center;
		border-radius: 999px;
		color: var(--foreground);
	}

	.nav-button:hover {
		background: var(--muted);
	}

	/* A block shows the lines its height has room for, whatever height the day is
	   given and whatever size the type: its times get a line of their own from two
	   lines' height, and sit beside the title below that; a place needs three. */
	.fit-block {
		container-type: size;
	}

	.fit-second,
	.fit-third {
		display: none;
	}

	@container (min-height: 2.1rem) {
		.fit-second {
			display: block;
		}

		.fit-inline {
			display: none;
		}
	}

	@container (min-height: 3.2rem) {
		.fit-third {
			display: block;
		}
	}

	/* A line under each hour, whatever height the day is given. */
	.day-column {
		background-image: linear-gradient(
			to top,
			color-mix(in oklch, var(--border), transparent 40%) 1px,
			transparent 1px
		);
		background-size: 100% calc(100% / 24);
	}

	/* An edge an event is resized by: a strip to grab, with a short bar to show it. */
	.resize-grip::after {
		content: '';
		position: absolute;
		left: 50%;
		bottom: 3px;
		width: 1.5rem;
		height: 3px;
		border-radius: 999px;
		background: currentColor;
		opacity: 0.35;
		transform: translateX(-50%);
	}

	.resize-grip-top::after {
		top: 3px;
		bottom: auto;
	}

	.resize-grip:hover::after {
		opacity: 0.7;
	}
</style>
