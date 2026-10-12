<script lang="ts">
	import Notice from '#lib/core/ui/Notice.svelte';
	import TextLink from '#lib/core/ui/TextLink.svelte';

	import { untrack } from 'svelte';
	import { apiRequest } from '#lib/core/api.ts';

	import { ChevronRight } from '@lucide/svelte';
	import { findInterface, interfaceEnabled, useInterfaces } from '#lib/shell/interfaces.ts';
	import type { Artifact } from '#lib/chat/types.ts';
	import ArtifactRenderer from '#lib/chat/cards/ArtifactRenderer.svelte';
	import ArtifactSnapshot from '#lib/chat/cards/ArtifactSnapshot.svelte';
	import { replace, reveal } from '#styles/motion.ts';
	import { artifactView } from '#lib/chat/cards/registry.ts';

	let {
		artifact,
		pending,
		blocked = false,
		errorMessage,
		siblings = [],
		interactionAt = 0,
		onUpdate,
		onAccept,
		onDeny,
		onRetry
	}: {
		artifact: Artifact;
		pending: boolean;
		blocked?: boolean;
		errorMessage: string;
		siblings?: Artifact[];
		/** Bumped by a set when the user works on any card in it, which keeps all of them alive. */
		interactionAt?: number;
		onUpdate: (payload: Record<string, unknown>) => void;
		onAccept: () => void;
		onDeny: (reason: string | null) => void;
		onRetry: () => void;
	} = $props();

	// A proposal is handled when it's shown: left alone for five minutes, it
	// expires. The clock restarts on every touch, so it never runs out while the
	// user is working on it, and each touch is reported to the server (at most
	// every few seconds, and once more after the last), which decides for real.
	const IDLE_EXPIRY_MS = 5 * 60 * 1000;
	const TOUCH_EVERY_MS = 15 * 1000;
	const open = $derived(artifact.status === 'draft' || artifact.status === 'ready');
	let lastInteraction = $state(untrack(() => serverInteraction()));
	let now = $state(Date.now());
	let expiredHere = $state(false);
	let lastSent = 0;
	let trailing: ReturnType<typeof setTimeout> | undefined;

	function serverInteraction() {
		const parsed = Date.parse(artifact.last_interacted_at ?? artifact.updated_at);
		return Math.min(Number.isNaN(parsed) ? Date.now() : parsed, Date.now());
	}

	// An edit from the agent puts the proposal back in front of the user.
	$effect(() => {
		void artifact.last_interacted_at;
		untrack(() => (lastInteraction = Math.max(lastInteraction, serverInteraction())));
	});

	$effect(() => {
		if (!open) return;
		const timer = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(timer);
	});

	$effect(() => {
		if (open && !pending && now - lastInteraction > IDLE_EXPIRY_MS) expiredHere = true;
	});

	$effect(() => () => clearTimeout(trailing));

	$effect(() => {
		if (interactionAt) untrack(touch);
	});

	async function sendTouch() {
		lastSent = Date.now();
		try {
			await apiRequest(`/api/chat/artifacts/${artifact.id}/touch`, { method: 'POST' });
		} catch {
			// The server says it expired, or the touch didn't land; either way the
			// countdown here still holds, and approving checks again.
		}
	}

	function touch() {
		if (!open || expiredHere) return;
		lastInteraction = Date.now();
		now = lastInteraction;
		clearTimeout(trailing);
		const wait = lastSent + TOUCH_EVERY_MS - Date.now();
		if (wait <= 0) void sendTouch();
		else trailing = setTimeout(() => void sendTouch(), wait);
	}

	const expired = $derived(artifact.status === 'expired' || (open && expiredHere));
	const accepted = $derived(artifact.status === 'completed');
	const settled = $derived(accepted || artifact.status === 'denied' || expired);
	// A decided proposal folds away, whether it was decided just now or loaded
	// that way; the header keeps the frame one click from view.
	let expanded = $state(untrack(() => !settled));
	$effect(() => {
		if (settled) untrack(() => (expanded = false));
	});
	let container: HTMLElement | undefined = $state();
	let pendingAutoScroll = false;
	const contentId = $props.id();

	function toggleExpanded() {
		const opening = !expanded;
		expanded = opening;
		pendingAutoScroll = opening;
	}

	function handleContentIntroEnd() {
		if (!pendingAutoScroll) return;
		pendingAutoScroll = false;
		container?.scrollIntoView({ behavior: 'smooth', block: 'end' });
	}

	const resultHref = $derived(
		typeof artifact.result_payload?.href === 'string' &&
			artifact.result_payload.href.startsWith('/') &&
			!artifact.result_payload.href.startsWith('//') &&
			!artifact.result_payload.href.includes('\\')
			? artifact.result_payload.href
			: null
	);
	const interfaces = useInterfaces();
	// An artifact that acts on an interface links into it once applied. It can
	// outlive the interface it was created for, and a link to a surface the user
	// turned off would go nowhere.
	const resultLink = $derived.by(() => {
		if (!resultHref) return null;
		const opens = artifactView(artifact)?.opens;
		if (!opens) return { href: resultHref, label: 'View result' };
		if (!interfaceEnabled(interfaces(), opens.interface)) return null;
		const name = findInterface(interfaces(), opens.interface)?.name ?? 'interface';
		// A result that links deeper into the same surface (a day, an event) goes there.
		const href = resultHref.startsWith(opens.link) ? resultHref : opens.link;
		return { href, label: `View in ${name}` };
	});
</script>

<article
	bind:this={container}
	inert={blocked}
	class="overflow-hidden rounded-xl border border-border/80 bg-card shadow-card"
>
	{#if settled}
		<div transition:reveal class="flex items-center gap-3 bg-muted/20 pr-4">
			<button
				type="button"
				class="flex min-w-0 flex-1 items-center gap-2 px-4 py-3 text-left text-sm font-medium text-muted-foreground hover:text-foreground"
				aria-expanded={expanded}
				aria-controls={contentId}
				onclick={toggleExpanded}
			>
				<ChevronRight
					class={[
						'size-3.5 transition-transform duration-(--motion-page) motion-reduce:transition-none',
						expanded && 'rotate-90'
					]}
				/>
				{accepted ? 'Accepted proposal' : expired ? 'Artifact expired' : 'Denied proposal'}
			</button>
			{#if accepted && resultLink}
				<TextLink href={resultLink.href}>{resultLink.label}</TextLink>
			{/if}
		</div>
	{/if}
	<div
		id={contentId}
		onpointerdowncapture={touch}
		onpointermovecapture={(event) => {
			if (event.buttons) touch();
		}}
		onkeydowncapture={touch}
		oninputcapture={touch}
	>
		{#if !settled || expanded}
			<div transition:reveal onintroend={handleContentIntroEnd}>
				<div class="p-4">
					{#if open && artifact.error?.code === 'blocked'}
						<!-- It overlaps something as things stand; approving what clears it brings it back ready. -->
						<Notice tone="info" class="mb-4">Can't be approved yet. {artifact.error.message}</Notice
						>
					{/if}
					<div class="grid">
						{#key artifact.id}
							<div class="col-start-1 row-start-1 min-w-0" in:replace>
								{#if expired}
									<ArtifactSnapshot {artifact} />
								{:else if accepted || artifact.status === 'executing'}
									<ArtifactSnapshot {artifact} muted={false} />
								{:else}
									<ArtifactRenderer
										{artifact}
										{pending}
										{errorMessage}
										{siblings}
										{onUpdate}
										{onAccept}
										{onDeny}
										{onRetry}
									/>
								{/if}
							</div>
						{/key}
					</div>
					{#if artifact.status === 'executing'}
						<div
							class="mt-5 flex h-10 items-center justify-center"
							role="status"
							aria-label="Applying changes"
						>
							<div
								class="h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-primary/15"
								role="progressbar"
								aria-label="Applying artifact"
								aria-valuetext="Applying changes; waiting for completion"
							>
								<div
									class="progress-stripes h-full w-full animate-progress rounded-full bg-primary"
								></div>
							</div>
						</div>
					{/if}
				</div>
			</div>
		{/if}
	</div>
</article>
