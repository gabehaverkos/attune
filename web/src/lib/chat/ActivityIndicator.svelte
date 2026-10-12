<script lang="ts">
	/**
	 * What the assistant is doing, as the server reports it. A label stays up for a
	 * moment before the next replaces it, so quick steps read instead of flicker;
	 * this changes only what is displayed, never what happened.
	 */
	import { onDestroy } from 'svelte';

	const MINIMUM_VISIBLE_MS = 300;

	let { label }: { label: string | null } = $props();

	let shown = $state<string | null>(null);
	let shownAt = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		const next = label;
		clearTimeout(timer);
		if (next === shown) return;
		const wait = shown === null || next === null ? 0 : MINIMUM_VISIBLE_MS - (Date.now() - shownAt);
		const show = () => {
			shown = next;
			shownAt = Date.now();
		};
		if (wait <= 0) show();
		else timer = setTimeout(show, wait);
	});

	onDestroy(() => clearTimeout(timer));
</script>

{#if shown}
	<p class="activity py-2 text-base text-muted-foreground" role="status" aria-live="polite">
		{#key shown}<span class="inline-block animate-activity">{shown}</span>{/key}
	</p>
{/if}
