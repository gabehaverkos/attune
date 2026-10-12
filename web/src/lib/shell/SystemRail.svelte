<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { sourceLinks } from '#lib/chat/sessions.svelte.ts';
	import { apiRequest } from '#lib/core/api.ts';
	import { getOnboardingContext, type OnboardingState } from '#lib/shell/interfaces.ts';
	import MemoryPanel from '#lib/memory/MemoryPanel.svelte';
	import AccountSettingsWorkspace from './AccountSettingsWorkspace.svelte';
	import RailPopover from './RailPopover.svelte';
	import type { MemoryPanelData } from '#lib/memory/types.ts';
	import { Brain, UserRound } from '@lucide/svelte';

	type ShellUser = {
		name?: string | null;
		email?: string | null;
		image?: string | null;
		isAnonymous?: boolean | null;
	};

	let {
		user,
		pendingEmail = null,
		seededDemo = false,
		covered = false
	}: {
		user: ShellUser | null;
		pendingEmail?: string | null;
		seededDemo?: boolean;
		/** An interface is open over the page, and over this rail with it. */
		covered?: boolean;
	} = $props();

	const displayName = $derived(
		user?.isAnonymous ? pendingEmail || 'Demo account' : user?.email?.trim() || 'Account'
	);

	let panel = $state<MemoryPanelData | null>(null);
	let memoryOpen = $state(false);

	// Memory is always on the rail, and is pointed out once: the first time it has
	// learned something. Opening it empty before then does not count as having
	// been shown that.
	const walkthrough = getOnboardingContext();
	const introduced = $derived(walkthrough?.state?.memory_introduced ?? true);
	const learnedSomething = $derived(!!panel?.nodes.length);
	// The note is in the top layer, above an open interface, where it would point
	// at a bubble that cannot be seen. It waits until the interface is closed.
	const pointOut = $derived(!introduced && learnedSomething && !covered);

	async function markIntroduced() {
		if (introduced || !learnedSomething) return;
		try {
			walkthrough.update(
				await apiRequest<OnboardingState>('/api/onboarding/memory-introduced', { method: 'POST' })
			);
		} catch {
			// Only the note depends on it; it shows again next time.
		}
	}

	// Watched closely while the panel is open, loosely while it is shut, so what
	// Attune learns shows up without polling for its own sake.
	const OPEN_INTERVAL = 5000;
	const WORKING_INTERVAL = 15_000;
	const IDLE_INTERVAL = 60_000;

	const pollInterval = $derived(
		memoryOpen ? OPEN_INTERVAL : panel?.working ? WORKING_INTERVAL : IDLE_INTERVAL
	);

	async function refresh() {
		try {
			panel = await apiRequest<MemoryPanelData>('/api/memory/panel');
		} catch {
			// The rail is chrome; a failed poll should never take the shell down.
		}
	}

	$effect(() => {
		const interval = pollInterval;
		void refresh();
		const timer = setInterval(refresh, interval);
		return () => clearInterval(timer);
	});

	/** The rails live in the top layer, where nothing underneath can dismiss them. */
	function closeRail() {
		document.querySelector<HTMLElement>('[popover]:popover-open')?.hidePopover();
	}

	/**
	 * The panel deliberately stays open through this. It stops at the chat column
	 * rather than covering it, so the fact and the message that taught it can be
	 * read side by side — which is the whole point of following a source.
	 *
	 * The address may be the one already open: the same link followed again, or
	 * followed after picking another session from the list, which leaves the
	 * address as it was. So the session is read again either way.
	 */
	function openSource(threadId: string, messageId: string | null) {
		const anchor = messageId ? `&message=${encodeURIComponent(messageId)}` : '';
		void goto(resolve(`/?thread=${encodeURIComponent(threadId)}${anchor}`), {
			invalidate: ['chat:thread']
		}).then(() => (sourceLinks.followed += 1));
	}
</script>

<!--
	The mirror of the interface rail. That side holds your tools; this side holds
	your account and Attune's memory.
-->
<nav aria-label="You" class="flex min-h-0 flex-col items-center gap-2 overflow-y-auto">
	<RailPopover label="Account" title={displayName} icon={UserRound}>
		<AccountSettingsWorkspace {user} {pendingEmail} {seededDemo} />
	</RailPopover>

	<!--
		Memory has an index and a fact list to show, so it fills the height and
		takes the empty space between the rail and the conversation, stopping at the
		chat column rather than covering the reading it belongs beside.
	-->
	<RailPopover
		label="Memory"
		title="Memory"
		icon={Brain}
		fill
		sticky
		boundary=".chat-column"
		onOpenChange={(open) => {
			memoryOpen = open;
			if (open) void markIntroduced();
		}}
		hint={pointOut ? memoryHint : undefined}
	>
		<MemoryPanel
			{panel}
			onPanel={(next) => (panel = next)}
			onRefresh={refresh}
			onSelectSource={openSource}
			onClose={closeRail}
		/>
	</RailPopover>
</nav>

{#snippet memoryHint()}
	<p>Attune remembered what you told it. Open Memory to review or correct it.</p>
	<button
		type="button"
		onclick={() => void markIntroduced()}
		class="mt-3 text-body font-medium text-foreground underline-offset-4 hover:underline"
		>Got it</button
	>
{/snippet}
