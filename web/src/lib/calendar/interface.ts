import type { InterfaceDescriptor } from '#lib/shell/interfaces.ts';
import { CalendarDays } from '@lucide/svelte';
import CalendarMark from '#lib/calendar/CalendarMark.svelte';
import CalendarView from '#lib/calendar/CalendarView.svelte';
import type { ArtifactView } from '#lib/chat/cards/registry.ts';
import CalendarChangeSnapshot from '#lib/calendar/cards/CalendarChangeSnapshot.svelte';
import EventArtifact from '#lib/calendar/cards/EventArtifact.svelte';
import OccurrenceChangeArtifact from '#lib/calendar/cards/OccurrenceChangeArtifact.svelte';
import RemovalArtifact from '#lib/calendar/cards/RemovalArtifact.svelte';
import RoutineArtifact from '#lib/calendar/cards/RoutineArtifact.svelte';
import RoutineSnapshot from '#lib/calendar/cards/RoutineSnapshot.svelte';
import SleepRoutineArtifact from '#lib/calendar/cards/SleepRoutineArtifact.svelte';

// === The interface =====================================================================

/** The calendar as an interface: what it declares to the registry. */

export const KEY = 'calendar';

export const INTERFACE: InterfaceDescriptor = {
	key: KEY,
	icon: CalendarDays,
	Mark: CalendarMark,
	View: CalendarView,
	link: {
		// `?calendar=open`, from an artifact card; `?routine=` (and `?routine_id=`),
		// the routine editor to open with it; `?date=` (and `?event_id=`), the day to
		// open on and what to select.
		params: ['calendar', 'routine', 'routine_id', 'date', 'event_id'],
		opens: (params) => params.get('calendar') === 'open'
	},
	data: ['/api/calendar']
};

// === Its cards =========================================================================

/** The calendar's artifact types, as chat shows them: its routines. */

const opens = (routine: string) => ({ interface: KEY, link: `/?calendar=open&routine=${routine}` });

export const ARTIFACTS: readonly ArtifactView[] = [
	{
		type: 'work_routine',
		schemaVersion: 1,
		Component: RoutineArtifact,
		Snapshot: RoutineSnapshot,
		opens: opens('work')
	},
	{
		type: 'other_routine',
		schemaVersion: 1,
		Component: RoutineArtifact,
		Snapshot: RoutineSnapshot,
		opens: opens('other')
	},
	{
		type: 'sleep_routine',
		schemaVersion: 1,
		Component: SleepRoutineArtifact,
		Snapshot: RoutineSnapshot,
		opens: opens('sleep')
	},
	{
		type: 'event',
		schemaVersion: 1,
		Component: EventArtifact,
		Snapshot: CalendarChangeSnapshot,
		opens: { interface: KEY, link: '/?calendar=open' }
	},
	{
		type: 'occurrence_change',
		schemaVersion: 1,
		Component: OccurrenceChangeArtifact,
		Snapshot: CalendarChangeSnapshot,
		opens: { interface: KEY, link: '/?calendar=open' }
	},
	{
		type: 'calendar_removal',
		schemaVersion: 1,
		Component: RemovalArtifact,
		Snapshot: CalendarChangeSnapshot,
		opens: { interface: KEY, link: '/?calendar=open' }
	}
];
