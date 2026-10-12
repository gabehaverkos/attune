<script lang="ts">
	import { tick, type Component, type Snippet } from 'svelte';
	import RailButton from '#lib/core/ui/RailButton.svelte';

	/**
	 * A bubble on the right-hand rail and the panel it opens to its left.
	 *
	 * The panel uses the native popover API rather than absolute positioning: the
	 * interface rail scrolls (`overflow-y-auto`) and the app canvas clips
	 * (`overflow-hidden`), so a positioned child would be cut off by its own
	 * ancestors. The top layer escapes both, and an `auto` popover brings light
	 * dismiss, Escape, and one-open-at-a-time with it — so two rails can never be
	 * open at once. A `sticky` panel gives up light dismiss deliberately and has
	 * the other two restored by hand.
	 */
	let {
		label,
		icon: Icon,
		fill = false,
		boundary,
		sticky = false,
		title,
		onOpenChange,
		hint,
		children
	}: {
		label: string;
		icon: Component<{ class?: string }>;
		/**
		 * Take the viewport's full height, and every horizontal pixel between the rail
		 * and `boundary`, instead of the fixed width. For a panel with more to say
		 * than a bubble's worth.
		 */
		fill?: boolean;
		/**
		 * Selector for the element a filled panel must not cover — the route's own
		 * column. Without one, a filled panel stops at the viewport edge.
		 */
		boundary?: string;
		/**
		 * Stay open until dismissed: a click outside leaves the panel where it is, and
		 * the page underneath stays usable. For a panel meant to be read alongside the
		 * route rather than glanced at. Costs the three things `auto` gave for free,
		 * each replaced below.
		 */
		sticky?: boolean;
		title?: string;
		/** Lets a panel start and stop work — polling, mainly — with its own visibility. */
		onOpenChange?: (open: boolean) => void;
		/**
		 * A note pointing at the bubble, shown beside it until the panel is opened or
		 * the caller takes it away. For introducing a rail the first time it matters.
		 */
		hint?: Snippet;
		children: Snippet;
	} = $props();

	const panelId = $props.id();
	const WIDTH = '28rem';
	const GAP = 8;
	const FILL_GAP = 20;
	// Below this a filled panel would be unreadable, so it crosses the boundary
	// rather than shrinking to a sliver on a narrow viewport.
	const MIN_FILL_WIDTH = 20 * 16;

	let trigger: HTMLButtonElement | undefined = $state();
	let panel: HTMLDivElement | undefined = $state();
	let open = $state(false);
	let hintNote: HTMLDivElement | undefined = $state();

	// The note sits in the top layer too, or the rail's own scrolling would clip it.
	$effect(() => {
		if (!hint || open) return;
		let cancelled = false;
		void tick().then(() => {
			if (cancelled || !hintNote?.isConnected || !trigger) return;
			hintNote.showPopover();
			placeHint();
		});
		return () => {
			cancelled = true;
			if (hintNote?.isConnected && hintNote.matches(':popover-open')) hintNote.hidePopover();
		};
	});

	function placeHint() {
		if (!trigger || !hintNote) return;
		const t = trigger.getBoundingClientRect();
		const h = hintNote.getBoundingClientRect();
		const left = t.left - GAP - h.width;
		hintNote.style.left = `${Math.max(GAP, Math.min(left, window.innerWidth - h.width - GAP))}px`;
		hintNote.style.top = `${Math.max(GAP, t.top + t.height / 2 - h.height / 2)}px`;
	}

	/** Stretch the panel before it is measured, so placement sees its real size. */
	function stretch(t: DOMRect, gap: number) {
		if (!panel) return;
		panel.style.maxHeight = 'none';
		panel.style.height = `${window.innerHeight - gap * 2}px`;
		const edge = boundary ? document.querySelector(boundary)?.getBoundingClientRect() : undefined;
		// A route without the boundary element gives the panel nothing to stop at, so
		// it keeps its fixed width rather than swallowing the viewport.
		if (!edge) {
			panel.style.width = WIDTH;
			return;
		}
		// The edge the panel grows away from, and the one it must not pass, each held
		// a gap clear so the panel never brushes the rail or the column.
		const near = t.left - gap;
		const far = edge.right + gap;
		panel.style.width = `${Math.max(MIN_FILL_WIDTH, Math.abs(near - far))}px`;
	}

	function place() {
		if (!trigger || !panel) return;
		// A filled panel meets the viewport edges, where a hairline gap reads as a
		// mistake; one hanging off its bubble only needs separating from it.
		const gap = fill ? FILL_GAP : GAP;
		const t = trigger.getBoundingClientRect();
		if (fill) stretch(t, gap);
		const p = panel.getBoundingClientRect();
		const wanted = t.left - gap - p.width;
		const maxLeft = window.innerWidth - p.width - gap;
		const maxTop = window.innerHeight - p.height - gap;
		panel.style.left = `${Math.max(gap, Math.min(wanted, maxLeft))}px`;
		// Hangs from the bubble's top edge, pulled up only to stay on screen. A
		// filled panel has no bubble to hang from: it spans the viewport.
		panel.style.top = fill ? `${gap}px` : `${Math.max(gap, Math.min(t.top, maxTop))}px`;
	}

	function handleBeforeToggle(event: Event) {
		// `toggle` is queued: the browser can paint the top layer before it runs.
		// Keep the panel measurable but invisible until its coordinates are ready.
		if ((event as ToggleEvent).newState === 'open' && panel) {
			panel.style.visibility = 'hidden';
		}
	}

	function handleToggle(event: Event) {
		open = (event as ToggleEvent).newState === 'open';
		if (open && panel) {
			// One rail open at a time. The top layer enforces this between `auto`
			// popovers by itself, but a `manual` one neither closes others nor is
			// closed by them, so whichever opens second says so explicitly.
			for (const other of document.querySelectorAll<HTMLElement>('[popover]:popover-open')) {
				if (other !== panel) other.hidePopover();
			}
			place();
			panel.style.visibility = 'visible';
		}
		onOpenChange?.(open);
	}

	// Escape comes free with `auto` and not at all with `manual`. Bound to the window
	// rather than the panel, which never takes focus on its own.
	function handleKeydown(event: KeyboardEvent) {
		if (sticky && open && event.key === 'Escape') panel?.hidePopover();
	}
</script>

<svelte:window
	onresize={() => {
		if (open) place();
		if (hint && !open) placeHint();
	}}
	onkeydown={handleKeydown}
/>

<div class="relative shrink-0">
	<RailButton bind:ref={trigger} popovertarget={panelId} aria-label={label} title={title ?? label}>
		<Icon class="size-5" />
	</RailButton>
</div>

<div
	bind:this={panel}
	id={panelId}
	popover={sticky ? 'manual' : 'auto'}
	onbeforetoggle={handleBeforeToggle}
	ontoggle={handleToggle}
	aria-label={label}
	style:width={fill ? undefined : WIDTH}
	class="@container fixed inset-auto m-0 max-w-[calc(100vw-1rem)] max-h-[min(80vh,44rem)] overflow-y-auto overscroll-contain rounded-xl border border-border bg-card p-0 text-foreground shadow-2xl"
>
	{@render children()}
</div>

{#if hint}
	<div
		bind:this={hintNote}
		popover="manual"
		role="note"
		class="fixed inset-auto m-0 w-64 rounded-xl border border-border bg-card p-4 text-body text-foreground shadow-xl"
	>
		{@render hint()}
	</div>
{/if}
