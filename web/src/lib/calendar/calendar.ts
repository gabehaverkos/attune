import { cachedFetch, type EntityId, type ISODate, type ISODateTime } from '#lib/core/api.ts';
import type { Artifact } from '#lib/chat/types.ts';
import { formatClock } from '#lib/calendar/TimeTrack.svelte';

// === Types =============================================================================

/**
 * The strip of colour down a block's edge, which is how everything on the
 * calendar is told apart: the ink for an event, a routine's own for a routine.
 */
export function blockEdgeColor(kind: CalendarScheduleBlock['kind']): string {
	if (kind === 'event') return 'var(--primary)';
	if (kind === 'sleep') return 'var(--schedule-blue)';
	if (kind === 'work') return 'var(--schedule-amber)';
	return 'var(--schedule-rose)';
}

export interface CalendarScheduleBlock {
	id: EntityId;
	title: string;
	kind: 'event' | 'sleep' | 'work' | 'other';
	start: ISODateTime;
	end: ISODateTime;
	source: 'user' | 'assistant';
	location?: string | null;
	/** The user's notes: an event's own, or one routine day's. */
	description?: string | null;
	event_id?: EntityId | null;
	/** A routine occurrence: its window, and the day it starts. */
	routine_id?: EntityId | null;
	window_id?: EntityId | null;
	occurrence_date?: ISODate | null;
	/** Who changed this occurrence: the user that day, or events (listed). */
	changed_by?: 'direct' | 'event' | null;
	changed_by_event_ids?: EntityId[];
}

export interface CalendarDay {
	date: ISODate;
	blocks: CalendarScheduleBlock[];
}

type CalendarRoutineType = 'sleep' | 'work' | 'other';

interface CalendarRoutineWindow {
	id?: EntityId | null;
	weekdays: number[];
	start_local_time: string;
	end_local_time: string;
	end_day_offset: 0 | 1;
}

export interface CalendarRoutine {
	id: EntityId;
	kind: CalendarRoutineType;
	status: 'active' | 'archived';
	archive_reason: string | null;
	name: string;
	location: string | null;
	effective_from: ISODate | null;
	effective_until: ISODate | null;
	windows: CalendarRoutineWindow[];
}

type CalendarRoutinesPayload = Record<CalendarRoutineType, CalendarRoutine[]>;

export interface CalendarRangePayload {
	months: Record<string, CalendarDay[]>;
}

export interface CalendarPayload {
	days: CalendarDay[];
	routines: CalendarRoutinesPayload;
	months: Record<string, CalendarDay[]>;
}

export type RoutineChangeOperation = 'keep' | 'start_later' | 'end_earlier' | 'split' | 'skip';

interface RoutineChange {
	window_id: EntityId;
	/** The day the occurrence starts: bedtime's day, for sleep. */
	occurrence_date: ISODate;
	operation: RoutineChangeOperation;
	margin_minutes: number;
}

/** An event as the API sends it: local date and clock times, and the routine changes it owns. */
export interface CalendarEvent {
	id: EntityId;
	title: string;
	location: string | null;
	description: string | null;
	date: ISODate;
	start: string;
	end: string;
	end_day_offset: 0 | 1;
	updated_at: ISODateTime;
	routine_changes: RoutineChange[];
}

interface CalendarClashOption {
	operation: RoutineChangeOperation;
	label: string;
	/** What would remain of the occurrence, as ISO local times. */
	segments: [ISODateTime, ISODateTime][];
}

/** Something an event overlaps: a routine occurrence (with changes on offer), or another event. */
export interface CalendarClash {
	key: string;
	type: 'routine' | 'event';
	title: string;
	start: ISODateTime;
	end: ISODateTime;
	routine_id: EntityId | null;
	routine_kind: CalendarRoutineType | null;
	window_id: EntityId | null;
	occurrence_date: ISODate | null;
	event_id: EntityId | null;
	/** The day was changed directly, which wins over any event's change. */
	locked: boolean;
	options: CalendarClashOption[];
}

/** `minutes` on `date`'s clock as an ISO local time, the inverse of `minutesOnDay`. */
function isoOnDay(minutes: number, date: string) {
	return `${addDays(date, Math.floor(minutes / 1440))}T${clock(minutes)}:00`;
}

/**
 * The changes on offer for a routine occurrence an event overlaps, as the server
 * works them out (`routine_options` in the calendar's events.py, which this
 * mirrors): shown at once while its answer is on the way. All times are minutes
 * on `date`'s clock; `name` is what "Skip …" calls a routine of no special kind.
 */
export function clashOptions(
	kind: CalendarRoutineType,
	name: string,
	base: [number, number],
	event: [number, number],
	margin: number,
	date: string
): CalendarClashOption[] {
	const [from, to] = base;
	const before = event[0] - margin;
	const after = event[1] + margin;
	const cuts: [RoutineChangeOperation, [number, number] | null][] = [['keep', null]];
	if (from < after && after < to) cuts.push(['start_later', [-Infinity, after]]);
	if (from < before && before < to) cuts.push(['end_earlier', [before, Infinity]]);
	if (from < before && after < to) cuts.push(['split', [before, after]]);
	cuts.push(['skip', [-Infinity, Infinity]]);

	return cuts.map(([operation, cut]) => {
		const remaining: [number, number][] = [];
		if (!cut || cut[1] <= from || cut[0] >= to) remaining.push([from, to]);
		else {
			if (cut[0] > from) remaining.push([from, cut[0]]);
			if (cut[1] < to) remaining.push([cut[1], to]);
		}
		let label = 'Keep';
		if (operation === 'skip') {
			label = kind === 'sleep' ? 'Skip this sleep' : kind === 'work' ? 'Skip work' : `Skip ${name}`;
		} else if (operation === 'start_later') {
			label = `${kind === 'sleep' ? 'Bedtime' : 'Start at'} ${formatClock(remaining[0][0])}`;
		} else if (operation === 'end_earlier') {
			const verb = kind === 'sleep' ? 'Wake at' : kind === 'work' ? 'Leave at' : 'End at';
			label = `${verb} ${formatClock(remaining[remaining.length - 1][1])}`;
		} else if (operation === 'split') {
			const gap = `${formatClock(remaining[0][1])}–${formatClock(remaining[remaining.length - 1][0])}`;
			label = `${kind === 'sleep' ? 'Up' : 'Out'} ${gap}`;
		}
		return {
			operation,
			label,
			segments: remaining.map(([start, end]) => [isoOnDay(start, date), isoOnDay(end, date)])
		};
	});
}

// === Editing an event ==================================================================

/** An event while it's being edited, and the calendar around it. */

/**
 * Times are minutes from midnight on `date`, so an event that ends the next day
 * has an `end` past 1440, the same clock the track uses.
 */
export interface EventEditorValue {
	title: string;
	location: string | null;
	/** Left out where there's no field for it (the chat's proposals), so a save keeps what's there. */
	description?: string;
	date: string;
	start: number;
	end: number;
	routine_changes: RoutineChange[];
}

/** What the events API takes: local date and clock times, and the routine changes. */
export interface EventInput {
	title: string;
	location: string | null;
	description?: string;
	date: string;
	start: string;
	end: string;
	end_day_offset: 0 | 1;
	routine_changes: RoutineChange[];
	expected_updated_at?: string | null;
}

export function clock(minutes: number) {
	const wrapped = ((minutes % 1440) + 1440) % 1440;
	return `${String(Math.floor(wrapped / 60)).padStart(2, '0')}:${String(wrapped % 60).padStart(2, '0')}`;
}

export function minutesOf(value: string) {
	const [hour, minute] = value.split(':').map(Number);
	return hour * 60 + minute;
}

export function addDays(date: string, days: number) {
	const value = new Date(`${date}T00:00:00`);
	value.setDate(value.getDate() + days);
	return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}

export function newEventValue(date: string, start = 9 * 60, end = 10 * 60): EventEditorValue {
	return { title: '', location: null, description: '', date, start, end, routine_changes: [] };
}

export function fromEvent(event: CalendarEvent): EventEditorValue {
	const start = minutesOf(event.start);
	return {
		title: event.title,
		location: event.location,
		description: event.description ?? '',
		date: event.date,
		start,
		end: minutesOf(event.end) + event.end_day_offset * 1440,
		routine_changes: event.routine_changes
	};
}

/** Only the changes that do something: keep is the same as no change. */
export function toEventInput(value: EventEditorValue): EventInput {
	return {
		title: value.title.trim(),
		location: value.location?.trim() || null,
		...(value.description !== undefined && { description: value.description.trim() }),
		date: value.date,
		start: clock(value.start),
		end: clock(value.end),
		end_day_offset: value.end >= 1440 ? 1 : 0,
		routine_changes: value.routine_changes.filter((change) => change.operation !== 'keep')
	};
}

export function eventInputKey(value: EventEditorValue) {
	return JSON.stringify(toEventInput(value));
}

/** Minutes of an ISO local time on `date`'s clock: the next day's times run past 1440. */
export function minutesOnDay(iso: string, date: string) {
	const days = Math.round(
		(Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) - Date.parse(`${date}T00:00:00Z`)) / 86_400_000
	);
	const [hour, minute] = iso.slice(11, 16).split(':').map(Number);
	return days * 1440 + hour * 60 + minute;
}

function monthOf(date: string) {
	return date.slice(0, 7);
}

/** The calendar's days either side of `date` and `date` itself, as the timeline shows them. */
export async function loadDays(date: string): Promise<CalendarDay[]> {
	const dates = [addDays(date, -1), date, addDays(date, 1)];
	const months = [...new Set(dates.map(monthOf))];
	const loaded = await Promise.all(
		months.map((month) =>
			cachedFetch<CalendarRangePayload>(`/api/calendar/range?start=${month}&months=1`, fetch)
		)
	);
	return loaded
		.flatMap((payload) => Object.values(payload.months).flat())
		.filter((day) => dates.includes(day.date));
}

/** A block on `date`'s clock, once: an overnight block shows on both days. */
export type PlacedBlock = { block: CalendarScheduleBlock; start: number; end: number };

export function placeBlocks(days: CalendarDay[], date: string): PlacedBlock[] {
	const placed = new Map<string, PlacedBlock>();
	for (const day of days) {
		for (const block of day.blocks) {
			const key = block.id.replace(/:day:[^:]+$/, '');
			if (placed.has(key)) continue;
			placed.set(key, {
				block,
				start: minutesOnDay(block.start, date),
				end: minutesOnDay(block.end, date)
			});
		}
	}
	return [...placed.values()];
}

// === Editing a routine =================================================================

export type RoutineEditorType = 'sleep' | 'work' | 'other';

export interface RoutineEditorWindow {
	id?: string | null;
	weekdays: number[];
	start_local_time: string | null;
	end_local_time: string | null;
	end_day_offset: 0 | 1 | null;
}

export interface RoutineEditorValue {
	kind: RoutineEditorType;
	name: string | null;
	location: string | null;
	effective_from: string | null;
	effective_until: string | null;
	windows: RoutineEditorWindow[];
}

const defaults: Record<RoutineEditorType, { start: string; end: string; offset: 0 | 1 }> = {
	sleep: { start: '23:00', end: '07:00', offset: 1 },
	work: { start: '09:00', end: '17:00', offset: 0 },
	other: { start: '18:00', end: '19:00', offset: 0 }
};

export function createRoutineEditorValue(type: RoutineEditorType): RoutineEditorValue {
	const initial = defaults[type];
	return {
		kind: type,
		name: type === 'other' ? '' : type[0].toUpperCase() + type.slice(1),
		location: null,
		effective_from: null,
		effective_until: null,
		windows: [
			{
				weekdays: type === 'sleep' ? [0, 1, 2, 3, 4, 5, 6] : [0, 1, 2, 3, 4],
				start_local_time: initial.start,
				end_local_time: initial.end,
				end_day_offset: initial.offset
			}
		]
	};
}

export function emptyRoutineWindow(type: RoutineEditorType): RoutineEditorWindow {
	const initial = defaults[type];
	return {
		weekdays: [],
		start_local_time: initial.start,
		end_local_time: initial.end,
		end_day_offset: initial.offset
	};
}

export function normalizeRoutineEditorValue(value: RoutineEditorValue): RoutineEditorValue {
	return {
		...value,
		name: value.name?.trim() || null,
		location: value.location?.trim() || null,
		effective_from: value.effective_from || null,
		effective_until: value.effective_until || null,
		windows: value.windows.map((window) => ({
			...window,
			end_day_offset:
				window.start_local_time && window.end_local_time
					? window.end_local_time <= window.start_local_time
						? 1
						: 0
					: window.end_day_offset
		}))
	};
}

export function routineEditorErrors(value: RoutineEditorValue): string[] {
	const errors: string[] = [];
	if (!value.name?.trim()) errors.push('name');
	if (value.effective_from && value.effective_until && value.effective_until < value.effective_from)
		errors.push('effective_until');
	if (!value.windows.length) errors.push('windows');
	value.windows.forEach((window, index) => {
		if (!window.weekdays.length) errors.push(`windows.${index}.weekdays`);
		if (!window.start_local_time) errors.push(`windows.${index}.start_local_time`);
		if (!window.end_local_time) errors.push(`windows.${index}.end_local_time`);
	});
	return errors;
}

// === Event cards =======================================================================

/** The calendar's event, one-day change and removal proposals: their payloads, and how they read. */

export interface EventArtifactPayload {
	event_id: string | null;
	target_updated_at: string | null;
	title: string | null;
	location: string | null;
	date: string | null;
	start: string | null;
	end: string | null;
	end_day_offset: 0 | 1 | null;
	routine_changes: RoutineChange[];
}

export interface OccurrenceArtifactPayload {
	window_id: string | null;
	occurrence_date: string | null;
	operation: 'skip' | 'custom';
	start: string | null;
	end: string | null;
	routine_title: string | null;
	routine_kind: 'sleep' | 'work' | 'other' | null;
	base_start: string | null;
	base_end: string | null;
	target_created_at: string | null;
}

export interface RemovalArtifactPayload {
	target_type: 'event' | 'sleep' | 'work' | 'other' | null;
	target_id: string | null;
	title: string | null;
	summary: string[];
	consequences: string[];
	target_updated_at: string | null;
}

/** The draft, not the approved command: an event's approved payload is its API input. */
export function eventPayload(artifact: Artifact): EventArtifactPayload | null {
	const value = artifact.draft_payload;
	return value && typeof value === 'object' && Array.isArray(value.routine_changes)
		? (value as unknown as EventArtifactPayload)
		: null;
}

export function occurrencePayload(artifact: Artifact): OccurrenceArtifactPayload | null {
	const value = artifact.draft_payload;
	return value && (value.operation === 'skip' || value.operation === 'custom')
		? (value as unknown as OccurrenceArtifactPayload)
		: null;
}

export function removalPayload(artifact: Artifact): RemovalArtifactPayload | null {
	const value = artifact.draft_payload;
	return value && Array.isArray(value.summary)
		? (value as unknown as RemovalArtifactPayload)
		: null;
}

function today() {
	const now = new Date();
	return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

/** What the editor edits; a draft missing its day or times starts from a sensible hour. */
export function toEventValue(payload: EventArtifactPayload): EventEditorValue {
	const start = payload.start ? minutesOf(payload.start) : 9 * 60;
	let end = payload.end ? minutesOf(payload.end) : start + 60;
	if (payload.end_day_offset === 1 || (payload.end_day_offset === null && end <= start))
		end += 1440;
	return {
		title: payload.title ?? '',
		location: payload.location,
		date: payload.date ?? today(),
		start,
		end,
		routine_changes: payload.routine_changes
	};
}

export function fromEventValue(
	payload: EventArtifactPayload,
	value: EventEditorValue
): EventArtifactPayload {
	return {
		...payload,
		title: value.title,
		location: value.location?.trim() || null,
		date: value.date,
		start: clock(value.start),
		end: clock(value.end),
		end_day_offset: value.end >= 1440 ? 1 : 0,
		routine_changes: value.routine_changes.filter((change) => change.operation !== 'keep')
	};
}

function dayLabel(date: string) {
	return new Date(`${date}T00:00:00`).toLocaleDateString(undefined, {
		weekday: 'short',
		month: 'short',
		day: 'numeric'
	});
}

export function eventSummary(payload: EventArtifactPayload): string {
	if (!payload.date || !payload.start || !payload.end) return 'Needs a day and time';
	const value = toEventValue(payload);
	return `${dayLabel(value.date)} · ${formatClock(value.start)}–${formatClock(value.end)}`;
}

/** An occurrence change's times on the occurrence's start day, as the one-day editor holds them. */
export function toOccurrenceValue(payload: OccurrenceArtifactPayload) {
	const date = payload.occurrence_date ?? today();
	const baseStart = payload.base_start ? minutesOnDay(payload.base_start, date) : 9 * 60;
	const baseEnd = payload.base_end ? minutesOnDay(payload.base_end, date) : baseStart + 60;
	const near = (value: string | null, around: number) => {
		if (!value) return around;
		const minutes = minutesOf(value);
		return [minutes - 1440, minutes, minutes + 1440, minutes + 2880].reduce((best, candidate) =>
			Math.abs(candidate - around) < Math.abs(best - around) ? candidate : best
		);
	};
	const start = payload.operation === 'custom' ? near(payload.start, baseStart) : baseStart;
	let end = payload.operation === 'custom' ? near(payload.end, baseEnd) : baseEnd;
	if (end <= start) end += 1440;
	return { skip: payload.operation === 'skip', start, end };
}

export function occurrenceSummary(payload: OccurrenceArtifactPayload): string {
	if (!payload.occurrence_date) return 'Needs a day';
	const value = toOccurrenceValue(payload);
	return value.skip
		? `Skipped · ${dayLabel(payload.occurrence_date)}`
		: `${dayLabel(payload.occurrence_date)} · ${formatClock(value.start)}–${formatClock(value.end)}`;
}

// === Routine cards =====================================================================

/** The calendar's routine artifacts: their payloads, and how they read. */

interface WorkRoutineArtifactWindow {
	id?: string | null;
	weekdays: number[];
	start_local_time: string | null;
	end_local_time: string | null;
	end_day_offset: 0 | 1 | null;
}

export interface WorkRoutineArtifactPayload {
	kind: 'work';
	name: string | null;
	location: string | null;
	effective_from: string | null;
	effective_until: string | null;
	windows: WorkRoutineArtifactWindow[];
}

export interface SleepRoutineArtifactPayload {
	kind: 'sleep';
	name: string | null;
	location: string | null;
	effective_from: string | null;
	effective_until: string | null;
	windows: WorkRoutineArtifactWindow[];
}

export interface OtherRoutineArtifactPayload {
	kind: 'other';
	name: string | null;
	location: string | null;
	effective_from: string | null;
	effective_until: string | null;
	windows: WorkRoutineArtifactWindow[];
}

function routinePayload<Kind extends 'work' | 'other'>(
	value: Record<string, unknown> | null,
	kind: Kind
): (Kind extends 'work' ? WorkRoutineArtifactPayload : OtherRoutineArtifactPayload) | null {
	if (!value || value.kind !== kind || !Array.isArray(value.windows)) return null;
	const windows = value.windows.map((window) => {
		if (!window || typeof window !== 'object') return null;
		const item = window as Record<string, unknown>;
		if (!Array.isArray(item.weekdays) || !item.weekdays.every((day) => Number.isInteger(day)))
			return null;
		return {
			id: typeof item.id === 'string' ? item.id : null,
			weekdays: item.weekdays as number[],
			start_local_time: typeof item.start_local_time === 'string' ? item.start_local_time : null,
			end_local_time: typeof item.end_local_time === 'string' ? item.end_local_time : null,
			end_day_offset:
				item.end_day_offset === 0 || item.end_day_offset === 1 ? item.end_day_offset : null
		};
	});
	if (windows.some((window) => window === null)) return null;
	return {
		kind,
		name: typeof value.name === 'string' ? value.name : null,
		location: typeof value.location === 'string' ? value.location : null,
		effective_from: typeof value.effective_from === 'string' ? value.effective_from : null,
		effective_until: typeof value.effective_until === 'string' ? value.effective_until : null,
		windows: windows as WorkRoutineArtifactPayload['windows']
	} as Kind extends 'work' ? WorkRoutineArtifactPayload : OtherRoutineArtifactPayload;
}

export function workRoutinePayload(
	value: Record<string, unknown> | null
): WorkRoutineArtifactPayload | null {
	return routinePayload(value, 'work');
}

export function otherRoutinePayload(
	value: Record<string, unknown> | null
): OtherRoutineArtifactPayload | null {
	return routinePayload(value, 'other');
}

function routineDisplayTitle(typeLabel: 'Work' | 'Other', name: string | null): string {
	const normalizedName = name?.trim();
	if (!normalizedName || normalizedName.toLocaleLowerCase() === typeLabel.toLocaleLowerCase())
		return typeLabel;
	// "Other" says nothing a name doesn't: a run is "Run", not "Other · Run".
	if (typeLabel === 'Other') return normalizedName;
	if (normalizedName.toLocaleLowerCase().startsWith(`${typeLabel.toLocaleLowerCase()} ·`))
		return normalizedName;
	return `${typeLabel} · ${normalizedName}`;
}

function workRoutineDisplayTitle(payload: WorkRoutineArtifactPayload): string {
	return routineDisplayTitle('Work', payload.name);
}

function otherRoutineDisplayTitle(payload: OtherRoutineArtifactPayload): string {
	return routineDisplayTitle('Other', payload.name);
}

export function artifactDisplayTitle(artifact: Artifact): string {
	if (artifact.type === 'sleep_routine') return 'Sleep routine';
	if (artifact.type === 'work_routine') {
		const payload = workRoutinePayload(artifact.approved_payload ?? artifact.draft_payload);
		return payload ? workRoutineDisplayTitle(payload) : artifact.title;
	}
	if (artifact.type === 'other_routine') {
		const payload = otherRoutinePayload(artifact.approved_payload ?? artifact.draft_payload);
		return payload ? otherRoutineDisplayTitle(payload) : artifact.title;
	}
	return artifact.title;
}

export function toRoutineEditorValue(
	payload: WorkRoutineArtifactPayload | OtherRoutineArtifactPayload
): RoutineEditorValue {
	return { ...payload, windows: payload.windows.map((window) => ({ ...window })) };
}

export function formatRoutineDays(days: number[]): string {
	const labels = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
	const normalized = [...new Set(days)].filter((day) => day >= 0 && day <= 6).sort((a, b) => a - b);
	if (normalized.length === 7) return 'Every day';
	if (days.join(',') === '6,0,1,2,3') return 'Sunday–Thursday';

	const ranges: Array<[number, number]> = [];
	for (const day of normalized) {
		const current = ranges.at(-1);
		if (current && day === current[1] + 1) current[1] = day;
		else ranges.push([day, day]);
	}
	return ranges
		.map(([start, end]) => (start === end ? labels[start] : `${labels[start]}–${labels[end]}`))
		.join(', ');
}

export function formatLocalTime(value: string | null): string {
	if (!value) return 'Not set';
	const [hours, minutes] = value.split(':').map(Number);
	return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(
		new Date(2000, 0, 1, hours, minutes)
	);
}

export function artifactSummary(artifact: Artifact): string {
	if (artifact.type === 'work_routine') {
		const payload = workRoutinePayload(artifact.approved_payload ?? artifact.draft_payload);
		if (payload) return formatRoutineSummary(payload) ?? artifact.description;
	}
	if (artifact.type === 'other_routine') {
		const payload = otherRoutinePayload(artifact.approved_payload ?? artifact.draft_payload);
		if (payload) return formatRoutineSummary(payload) ?? artifact.description;
	}
	return artifact.description;
}

function formatRoutineSummary(
	payload: WorkRoutineArtifactPayload | OtherRoutineArtifactPayload
): string | null {
	if (!payload.windows.length) return null;
	const windows = payload.windows.map(
		(window) =>
			`${formatRoutineDays(window.weekdays)}, ${formatLocalTime(window.start_local_time)}–${formatLocalTime(window.end_local_time)}`
	);
	const formatDate = (value: string) =>
		new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		});
	const effective = payload.effective_from
		? `beginning ${formatDate(payload.effective_from)}`
		: 'effective immediately';
	const ending = payload.effective_until ? ` through ${formatDate(payload.effective_until)}` : '';
	return `${windows.join('; ')} · ${effective}${ending}.`;
}
