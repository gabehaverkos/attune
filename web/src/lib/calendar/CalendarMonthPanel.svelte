<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import { ChevronLeft, ChevronRight, LoaderCircle } from '@lucide/svelte';
	import { apiRequest, cachedFetch } from '#lib/core/api.ts';
	import type {
		CalendarDay,
		CalendarPayload,
		CalendarRangePayload
	} from '#lib/calendar/calendar.ts';
	import CalendarWeekPanel from '#lib/calendar/CalendarWeekPanel.svelte';
	import type { RoutineRef } from '#lib/calendar/RoutineConflicts.svelte';

	type CalendarCell = CalendarDay | { date: string; blocks: []; ghost: true };

	let {
		days,
		months: initialMonths = {},
		selectedDate,
		onSelectDate,
		onManageRoutine = () => undefined,
		initialBlockId = ''
	}: {
		days: CalendarDay[];
		months?: Record<string, CalendarDay[]>;
		selectedDate: string;
		onSelectDate: (date: string) => void;
		onManageRoutine?: (routine: RoutineRef) => void;
		/** What to select on the timeline when it first opens, e.g. an event a link names. */
		initialBlockId?: string;
	} = $props();

	function formatDayNumber(value: string) {
		const [, , day] = value.split('-').map(Number);
		return day;
	}

	const weekdayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

	/** A day's events, earliest first, for its cell: the routines stay out of the month. */
	function eventsOf(day: CalendarCell) {
		return day.blocks
			.filter((block) => block.kind === 'event')
			.sort((a, b) => a.start.localeCompare(b.start));
	}

	function chipTime(iso: string, date: string) {
		if (iso.slice(0, 10) !== date) return '';
		const [hour, minute] = iso.slice(11, 16).split(':').map(Number);
		return `${hour % 12 || 12}${minute ? `:${String(minute).padStart(2, '0')}` : ''}${hour < 12 ? 'a' : 'p'}`;
	}

	async function goToday() {
		const month = todayKey().slice(0, 7);
		if (month !== visibleMonth && !(await fetchMonth(month))) return;
		navigatedMonth = month === loadedMonth ? '' : month;
		pendingAdjacentMonthDate = '';
		openBlockId = '';
		onSelectDate(todayKey());
	}

	function onKeydown(event: KeyboardEvent) {
		const target = event.target;
		if (
			event.defaultPrevented ||
			event.metaKey ||
			event.ctrlKey ||
			event.altKey ||
			(target instanceof HTMLElement &&
				(target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)))
		)
			return;
		if (event.key === 't') void goToday();
		else if (event.key === 'ArrowLeft' && !selectedDate && !monthLoading) void navigateMonth(-1);
		else if (event.key === 'ArrowRight' && !selectedDate && !monthLoading) void navigateMonth(1);
		else return;
		event.preventDefault();
	}

	function todayKey() {
		const d = new Date();
		return formatDateKey(d);
	}

	function formatDateKey(value: Date) {
		return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
	}

	function monthKeyFromDate(value: string | undefined) {
		if (!value) return todayKey().slice(0, 7);
		return value.slice(0, 7);
	}

	function formatMonthLabel(monthKey: string) {
		return new Date(`${monthKey}-01T00:00:00`).toLocaleDateString('en-US', {
			month: 'long'
		});
	}

	function formatYearLabel(monthKey: string) {
		return monthKey.slice(0, 4);
	}

	function addMonths(monthKey: string, offset: number) {
		const [year, month] = monthKey.split('-').map(Number);
		const date = new Date(year, month - 1 + offset, 1);
		return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
	}

	function daysForMonth(monthKey: string, sourceDays: CalendarDay[]): CalendarCell[] {
		const [year, month] = monthKey.split('-').map(Number);
		const dayCount = new Date(year, month, 0).getDate();
		const firstDayOfWeek = new Date(year, month - 1, 1).getDay();
		const dayMap = new Map(sourceDays.map((day) => [day.date, day]));
		const monthDays: CalendarCell[] = Array.from({ length: dayCount }, (_, index) => {
			const date = `${monthKey}-${String(index + 1).padStart(2, '0')}`;
			return dayMap.get(date) ?? { date, blocks: [] };
		});
		const leadingGhostCellCount = firstDayOfWeek;
		const visibleCellCount = 42;
		const trailingGhostCellCount = visibleCellCount - leadingGhostCellCount - dayCount;

		return [
			...Array.from({ length: leadingGhostCellCount }, (_, index) => ({
				date: formatDateKey(new Date(year, month - 1, 1 - leadingGhostCellCount + index)),
				blocks: [],
				ghost: true as const
			})),
			...monthDays,
			...Array.from({ length: trailingGhostCellCount }, (_, index) => ({
				date: formatDateKey(new Date(year, month, index + 1)),
				blocks: [],
				ghost: true as const
			}))
		];
	}

	// What a newly opened day selects first: the link's event, or a block clicked from the day before.
	let openBlockId = $state(untrack(() => initialBlockId));
	const loadedMonth = $derived(monthKeyFromDate(days[0]?.date));
	let monthCache = $state<Record<string, CalendarDay[]>>({});
	let lastLoadedDays: CalendarDay[] | undefined;
	let rangeRequests = new Map<string, Promise<void>>();
	// Opened on a day in another month (from a link), that month shows first.
	let navigatedMonth = $state(
		untrack(() =>
			selectedDate && selectedDate.slice(0, 7) !== monthKeyFromDate(days[0]?.date)
				? selectedDate.slice(0, 7)
				: ''
		)
	);
	let pendingAdjacentMonthDate = $state('');
	const visibleMonth = $derived(navigatedMonth || loadedMonth);
	const visibleMonthLabel = $derived(formatMonthLabel(visibleMonth));
	const visibleYearLabel = $derived(formatYearLabel(visibleMonth));
	const visibleDateLabel = $derived(`${visibleMonthLabel} ${visibleYearLabel}`);
	const visibleDays = $derived(
		daysForMonth(
			visibleMonth,
			monthCache[visibleMonth] ?? (visibleMonth === loadedMonth ? days : [])
		)
	);
	const weeks = $derived(
		Array.from({ length: Math.ceil(visibleDays.length / 7) }, (_, index) =>
			visibleDays.slice(index * 7, index * 7 + 7)
		)
	);
	const weekRowHeight = $derived(
		`calc((100% - ${(Math.max(weeks.length - 1, 0) * 0.5).toFixed(1)}rem) / ${Math.max(weeks.length, 1)})`
	);
	const drawerHeight = $derived(`calc(100% - ${weekRowHeight} - 0.5rem)`);

	let calendarBody: HTMLDivElement | undefined = $state();
	let accordionRow: HTMLDivElement | undefined = $state();
	let showCustomScrollbar = $state(false);
	let calendarLoadError = $state('');
	let monthLoading = $state(false);
	let loadingMonth = $state('');
	let scrollbarThumbTop = $state('0px');
	let scrollbarThumbHeight = $state('0px');

	$effect(() => {
		if (days.length > 0 && days !== lastLoadedDays) {
			lastLoadedDays = days;
			monthCache = { ...monthCache, [loadedMonth]: days };
		}
	});

	$effect(() => {
		if (!selectedDate) return;
		tick().then(() => {
			if (!calendarBody || !accordionRow) return;
			requestAnimationFrame(() => {
				if (!calendarBody || !accordionRow) return;
				const bodyRect = calendarBody.getBoundingClientRect();
				const rowRect = accordionRow.getBoundingClientRect();
				const delta = rowRect.bottom - bodyRect.bottom;
				calendarBody.scrollTo({
					top: Math.max(0, calendarBody.scrollTop + delta),
					behavior: 'smooth'
				});
				updateCustomScrollbar();
			});
		});
	});

	$effect(() => {
		const dayCount = visibleDays.length;
		const activeDate = selectedDate;
		if (dayCount >= 0 && activeDate !== undefined) {
			tick().then(updateCustomScrollbar);
		}
	});

	async function prefetchRange(startMonth: string, monthCount: number) {
		const requestKey = `${startMonth}:${monthCount}`;
		const existingRequest = rangeRequests.get(requestKey);
		if (existingRequest) return existingRequest;
		const request = (async () => {
			const url = `/api/calendar/range?start=${startMonth}&months=${monthCount}`;
			const payload = await cachedFetch<CalendarRangePayload>(url, fetch);
			monthCache = Object.fromEntries([
				...Object.entries(payload.months).filter(([monthKey]) => !monthCache[monthKey]),
				...Object.entries(monthCache)
			]);
		})().finally(() => rangeRequests.delete(requestKey));
		rangeRequests.set(requestKey, request);
		return request;
	}

	function replenishBuffer(monthKey: string) {
		const cachedMonths = Object.keys(monthCache).sort();
		if (cachedMonths.length === 0) return;
		const lower = cachedMonths[0];
		const upper = cachedMonths[cachedMonths.length - 1];
		if (monthKey <= addMonths(lower, 3)) {
			void prefetchRange(addMonths(lower, -6), 6).catch(() => undefined);
		}
		if (monthKey >= addMonths(upper, -3)) {
			void prefetchRange(addMonths(upper, 1), 6).catch(() => undefined);
		}
	}

	async function fetchMonth(monthKey: string) {
		if (monthCache[monthKey]) {
			replenishBuffer(monthKey);
			return true;
		}
		monthLoading = true;
		loadingMonth = monthKey;
		calendarLoadError = '';
		try {
			const payload = await apiRequest<CalendarPayload>(`/api/calendar?month=${monthKey}`);
			monthCache = { ...monthCache, [monthKey]: payload.days };
			replenishBuffer(monthKey);
			return true;
		} catch (error) {
			calendarLoadError = error instanceof Error ? error.message : 'Unable to load this month.';
			return false;
		} finally {
			monthLoading = false;
			loadingMonth = '';
		}
	}

	async function navigateMonth(offset: number) {
		const targetMonth = addMonths(visibleMonth, offset);
		if (!(await fetchMonth(targetMonth))) return;
		navigatedMonth = targetMonth;
		pendingAdjacentMonthDate = '';
		openBlockId = '';
		onSelectDate('');
	}

	async function selectAdjacentMonthDay(date: string) {
		if (!(await fetchMonth(date.slice(0, 7)))) return;
		navigatedMonth = date.slice(0, 7);
		pendingAdjacentMonthDate = '';
		openBlockId = '';
		onSelectDate(selectedDate === date ? '' : date);
	}

	/** Straight to a day from the open one's timeline, bringing its month in if it's another. */
	async function openDay(date: string, blockId: string) {
		openBlockId = blockId;
		const monthKey = date.slice(0, 7);
		if (monthKey !== visibleMonth) {
			if (!(await fetchMonth(monthKey))) return;
			navigatedMonth = monthKey;
		}
		pendingAdjacentMonthDate = '';
		onSelectDate(date);
	}

	function selectCurrentMonthDay(date: string) {
		pendingAdjacentMonthDate = '';
		openBlockId = '';
		onSelectDate(selectedDate === date ? '' : date);
	}

	onMount(() => {
		monthCache = { ...monthCache, ...initialMonths, [loadedMonth]: days };
		if (navigatedMonth && !monthCache[navigatedMonth]) void fetchMonth(navigatedMonth);
		if (Object.keys(initialMonths).length === 0) {
			void prefetchRange(addMonths(loadedMonth, -6), 13).catch(() => undefined);
		}
		updateCustomScrollbar();
		const resizeObserver = new ResizeObserver(updateCustomScrollbar);
		if (calendarBody) resizeObserver.observe(calendarBody);
		window.addEventListener('resize', updateCustomScrollbar);

		return () => {
			resizeObserver.disconnect();
			window.removeEventListener('resize', updateCustomScrollbar);
		};
	});

	/** Reload the months these dates fall in, after an event or a day of a routine changed. */
	async function refreshDates(dates: string[]) {
		const monthKeys = [...new Set(dates.map((date) => date.slice(0, 7)))];
		const loaded = await Promise.all(
			monthKeys.map((monthKey) =>
				apiRequest<CalendarRangePayload>(`/api/calendar/range?start=${monthKey}&months=1`)
			)
		);
		const months = Object.assign({}, ...loaded.map((payload) => payload.months)) as Record<
			string,
			CalendarDay[]
		>;
		monthCache = { ...monthCache, ...months };
		if (months[loadedMonth]) days = months[loadedMonth];
	}

	function updateCustomScrollbar() {
		if (!calendarBody) return;

		const { clientHeight, scrollHeight, scrollTop } = calendarBody;
		showCustomScrollbar = Boolean(selectedDate) && scrollHeight > clientHeight + 1;

		if (!showCustomScrollbar) {
			scrollbarThumbTop = '0px';
			scrollbarThumbHeight = '0px';
			return;
		}

		const trackHeight = clientHeight;
		const thumbHeight = Math.max(42, (clientHeight / scrollHeight) * trackHeight);
		const maxThumbTop = Math.max(0, trackHeight - thumbHeight);
		const maxScrollTop = Math.max(1, scrollHeight - clientHeight);
		const thumbTop = (scrollTop / maxScrollTop) * maxThumbTop;

		scrollbarThumbTop = `${thumbTop}px`;
		scrollbarThumbHeight = `${thumbHeight}px`;
	}
</script>

<svelte:window onkeydown={onKeydown} />

<div class="calendar-month-panel-shell flex h-full min-h-[34rem] min-h-0 flex-col">
	<div class="mx-auto mb-3 flex shrink-0 items-center gap-2">
		<div
			class="calendar-nav flex h-12 w-64 shrink-0 items-center justify-between rounded-full border border-border/80 bg-card/75 p-1 shadow-raised"
		>
			<button
				type="button"
				aria-label="Previous month"
				class="date-nav-button transition-colors duration-150 ease-[ease]"
				disabled={monthLoading}
				onclick={() => navigateMonth(-1)}
			>
				<ChevronLeft class="size-[1.125rem]" />
			</button>

			<p
				class="inline-flex h-10 w-36 min-w-0 items-center justify-center gap-2 truncate rounded-full px-2 text-center text-base font-semibold leading-none"
				aria-live="polite"
			>
				{#if monthLoading}
					<LoaderCircle class="size-4 shrink-0 animate-spin" />
					<span class="truncate text-body">Loading {formatMonthLabel(loadingMonth)}</span>
				{:else}
					{visibleDateLabel}
				{/if}
			</p>

			<button
				type="button"
				aria-label="Next month"
				class="date-nav-button transition-colors duration-150 ease-[ease]"
				disabled={monthLoading}
				onclick={() => navigateMonth(1)}
			>
				<ChevronRight class="size-[1.125rem]" />
			</button>
		</div>
		<button
			type="button"
			title="Today (T)"
			class="h-12 rounded-full border border-border/80 bg-card/75 px-4 text-body font-medium shadow-raised hover:bg-muted disabled:opacity-50"
			disabled={monthLoading ||
				(visibleMonth === todayKey().slice(0, 7) && selectedDate === todayKey())}
			onclick={goToday}>Today</button
		>
	</div>

	<section
		class={[
			'panel flex min-h-0 flex-1 flex-col rounded-xl border border-border/80 bg-card/75 p-4 shadow-raised transition-opacity sm:p-5',
			monthLoading ? 'opacity-60' : 'opacity-100'
		]}
		aria-busy={monthLoading}
	>
		{#if calendarLoadError}<p class="mb-3 text-body text-destructive" role="alert">
				{calendarLoadError}
			</p>{/if}
		<div
			class="mb-2 grid shrink-0 grid-cols-7 gap-2 pr-1 text-center"
			aria-label="Days of the week"
		>
			{#each weekdayNames as weekday (weekday)}
				<div class="px-1 text-note font-semibold text-muted-foreground sm:text-body">
					{weekday}
				</div>
			{/each}
		</div>
		<div class="calendar-scroll-shell relative h-full min-h-0 flex-1">
			<div
				bind:this={calendarBody}
				class="calendar-scroll-area flex h-full min-h-0 flex-col gap-2 overflow-y-auto pr-1"
				onscroll={updateCustomScrollbar}
			>
				{#each weeks as week (week[0]?.date)}
					{@const open = week.some((day) => day.date === selectedDate)}
					<div
						class="grid min-h-24 shrink-0 grid-cols-7 gap-2 text-center text-note"
						style={`height: ${weekRowHeight};`}
					>
						{#each week as day (day.date)}
							{#if 'ghost' in day}
								<button
									type="button"
									onclick={() => selectAdjacentMonthDay(day.date)}
									class={[
										'adjacent-month-day flex min-h-24 flex-col items-start rounded-lg border border-dashed p-1.5 text-left transition-all hover:bg-muted/45 hover:text-muted-foreground',
										pendingAdjacentMonthDate === day.date
											? 'border-primary/35 bg-secondary/55 text-secondary-foreground'
											: 'border-border/35 bg-background/25 text-muted-foreground/55'
									]}
								>
									<p
										class="flex size-8 items-center justify-center rounded-full text-lg font-semibold leading-none tabular-nums"
									>
										{formatDayNumber(day.date)}
									</p>
								</button>
							{:else}
								{@const isToday = day.date === todayKey()}
								{@const events = eventsOf(day)}
								{@const shown = events.length > 3 ? 2 : 3}
								<button
									type="button"
									onclick={() => selectCurrentMonthDay(day.date)}
									aria-current={isToday ? 'date' : undefined}
									class={[
										'flex min-h-24 min-w-0 cursor-pointer flex-col items-stretch overflow-hidden rounded-lg border bg-background/45 p-1.5 text-left shadow-raised-sm transition-all hover:bg-muted',
										selectedDate === day.date
											? 'border-primary/35 bg-secondary text-secondary-foreground'
											: 'border-border/80',
										day.date < todayKey() && selectedDate !== day.date && 'text-muted-foreground'
									]}
								>
									<p
										class={[
											'flex size-8 shrink-0 items-center justify-center rounded-full text-lg font-semibold leading-none tabular-nums',
											isToday && 'bg-primary text-primary-foreground'
										]}
									>
										{formatDayNumber(day.date)}
									</p>
									<ul class="mt-1 min-h-0 space-y-0.5 text-base leading-tight">
										{#each events.slice(0, shown) as block (block.id)}
											<li class="flex min-w-0 items-center gap-1 rounded px-1 py-0.5">
												<span class="size-2 shrink-0 rounded-full bg-primary/70"></span>
												{#if chipTime(block.start, day.date)}
													<span class="shrink-0 tabular-nums text-muted-foreground"
														>{chipTime(block.start, day.date)}</span
													>
												{/if}
												<span class="truncate font-medium">{block.title}</span>
											</li>
										{/each}
										{#if events.length > shown}
											<li class="px-1 text-muted-foreground">+{events.length - shown} more</li>
										{/if}
									</ul>
								</button>
							{/if}
						{/each}
					</div>

					{#if open}
						<div
							bind:this={accordionRow}
							class="shrink-0 overflow-hidden rounded-xl border border-border/90 bg-card/95"
							style={`height: ${drawerHeight};`}
						>
							<!-- The day as the week draws it, so a day reads the same in both. -->
							{#key `${selectedDate}:${openBlockId}`}
								<CalendarWeekPanel
									day={selectedDate}
									initialDate={selectedDate}
									initialBlockId={openBlockId}
									onChanged={refreshDates}
									onLeaveDay={openDay}
									{onManageRoutine}
								/>
							{/key}
						</div>
					{/if}
				{/each}
			</div>

			{#if showCustomScrollbar}
				<div class="calendar-scrollbar" aria-hidden="true">
					<div
						class="calendar-scrollbar-thumb"
						style={`height: ${scrollbarThumbHeight}; transform: translate(-50%, ${scrollbarThumbTop});`}
					></div>
				</div>
			{/if}
		</div>
	</section>
</div>

<style>
	.date-nav-button {
		display: inline-flex;
		width: 2.5rem;
		height: 2.5rem;
		align-items: center;
		justify-content: center;
		border-radius: 999px;
		color: var(--foreground);
	}

	.date-nav-button:hover {
		background: var(--muted);
	}

	.calendar-scroll-area {
		scrollbar-width: none;
	}

	.calendar-scroll-area::-webkit-scrollbar {
		display: none;
	}

	.calendar-scrollbar {
		position: absolute;
		top: 0;
		right: -1.45rem;
		bottom: 0;
		width: 0.7rem;
		pointer-events: none;
	}

	.calendar-scrollbar::before {
		position: absolute;
		top: 0;
		bottom: 0;
		left: 50%;
		width: 1px;
		content: '';
		background: color-mix(in oklch, var(--border), transparent 28%);
		transform: translateX(-50%);
	}

	.calendar-scrollbar-thumb {
		position: absolute;
		top: 0;
		left: 50%;
		width: 2px;
		border-radius: 999px;
		background: color-mix(in oklch, var(--foreground), transparent 44%);
		box-shadow: 0 0 0 1px color-mix(in oklch, var(--card), transparent 15%);
		will-change: transform, height;
	}
</style>
