<script lang="ts">
	import SectionLabel from '#lib/core/ui/SectionLabel.svelte';

	import { Archive, ArchiveRestore, ChevronDown, Plus, Trash2 } from '@lucide/svelte';
	import { groupSessions, sessionLabel, type Sessions } from '#lib/chat/sessions.svelte.ts';

	let {
		sessions,
		activeThreadId,
		empty
	}: {
		sessions: Sessions;
		activeThreadId: string;
		/** The visible session has no messages yet. */
		empty: boolean;
	} = $props();

	let showArchived = $state(false);

	// Archived sessions are hidden until asked for, so the list stays the set of
	// conversations still in play. An archived one still shows while it is the
	// session being read — opened through the toggle, or linked to from a memory
	// source — because the list should never hide what is on screen.
	const groups = $derived(
		groupSessions(
			sessions.threads.filter((t) => showArchived || !t.archived || t.id === activeThreadId)
		)
	);
	const archivedCount = $derived(sessions.threads.filter((t) => t.archived).length);
</script>

<aside
	class={[
		'sessions-panel fixed top-0 left-1/2 z-40 w-[min(34rem,calc(100%-2rem))]',
		sessions.open ? 'is-open' : 'is-closed'
	]}
>
	<div
		class="flex max-h-[min(55vh,34rem)] flex-col overflow-hidden rounded-b-xl border-x border-b border-border/70 bg-card shadow-xl"
		inert={!sessions.open}
	>
		<div class="min-h-0 space-y-4 overflow-y-auto px-4 pt-4 pb-4" style="scrollbar-width: thin;">
			{#each groups as group (group.key)}
				<div>
					<SectionLabel class="px-1 pb-2">
						{group.label}
					</SectionLabel>
					<div class="space-y-2">
						{#each group.threads as t (t.id)}
							<div
								role="button"
								tabindex="0"
								onclick={() => sessions.select(t.id)}
								onkeydown={(event) => event.key === 'Enter' && sessions.select(t.id)}
								class="group relative flex cursor-pointer select-none items-center justify-between rounded-xl border px-4 py-3 text-sm font-medium transition-all {activeThreadId ===
								t.id
									? 'border-primary/20 bg-accent/85 text-accent-foreground shadow-sm'
									: 'border-transparent bg-card/35 text-muted-foreground hover:border-border/40 hover:bg-muted/40 hover:text-foreground'}"
							>
								<span class={['w-full truncate pr-16 font-semibold', t.archived && 'italic']}
									>{sessionLabel(t.created_at)}</span
								>
								<div
									class="absolute right-2 flex items-center gap-1.5 bg-gradient-to-l from-card/90 py-1 pl-3 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
								>
									<button
										type="button"
										onclick={(event) => {
											event.stopPropagation();
											void sessions.archive(t.id, !t.archived);
										}}
										class="rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
										aria-label={t.archived ? 'Restore session' : 'Archive session'}
										title={t.archived ? 'Restore session' : 'Archive session'}
									>
										{#if t.archived}
											<ArchiveRestore class="size-4" />
										{:else}
											<Archive class="size-4" />
										{/if}
									</button>
									<button
										type="button"
										onclick={(event) => {
											event.stopPropagation();
											void sessions.delete(t.id);
										}}
										class="rounded p-1 text-destructive/80 transition hover:bg-destructive/10 hover:text-destructive"
										aria-label="Delete session"
										title="Delete session and what it taught memory"
									>
										<Trash2 class="size-4" />
									</button>
								</div>
							</div>
						{/each}
					</div>
				</div>
			{:else}
				<div class="flex items-center gap-3 px-1 py-2 text-muted-foreground/70">
					<span class="h-px flex-1 bg-border/70"></span>
					<span class="text-xs font-semibold uppercase tracking-wide">Empty</span>
					<span class="h-px flex-1 bg-border/70"></span>
				</div>
			{/each}

			<!--
				Only offered once there is something behind it: a switch for an empty
				shelf is just another thing to read past.
			-->
			{#if archivedCount > 0}
				<button
					type="button"
					onclick={() => (showArchived = !showArchived)}
					aria-pressed={showArchived}
					class="flex w-full cursor-pointer items-center gap-2 rounded-lg px-1 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
				>
					<Archive class="size-3.5 shrink-0" aria-hidden="true" />
					<span>
						{showArchived ? 'Hide' : 'Show'}
						{archivedCount} archived
					</span>
					<span class="h-px flex-1 bg-border/70"></span>
				</button>
			{/if}
		</div>
	</div>
	<div
		class="mx-auto flex h-10 w-56 items-stretch overflow-hidden rounded-b-xl border-x border-b border-border/70 bg-card shadow-sm"
	>
		<button
			type="button"
			onclick={() => (sessions.open = !sessions.open)}
			class="flex flex-1 items-center justify-center gap-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
			title={sessions.open ? 'Close sessions' : 'Open sessions'}
			aria-label={sessions.open ? 'Close sessions' : 'Open sessions'}
			aria-expanded={sessions.open}
		>
			<span>Sessions</span>
			<ChevronDown
				class={[
					'size-4 transition-transform duration-(--motion-page)',
					sessions.open && 'rotate-180'
				]}
			/>
		</button>
		<div class="w-px shrink-0 bg-border/70" aria-hidden="true"></div>
		<button
			type="button"
			onclick={() => void sessions.startNew()}
			disabled={sessions.loading || !sessions.canStartNew}
			class="flex flex-1 items-center justify-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-30"
			title={empty ? 'You are already in a fresh empty session' : 'Start a new session'}
			aria-label="Start a new session"
		>
			<span>New</span>
			<Plus class="size-4" />
		</button>
	</div>
</aside>
