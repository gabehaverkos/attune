<!-- The calendar's rail button: today's date, the way a desk calendar shows it. -->
<script lang="ts">
	import { onMount } from 'svelte';

	let today = $state(new Date());

	// Turn the page at midnight, for a tab left open overnight.
	onMount(() => {
		const timer = setInterval(() => {
			const now = new Date();
			if (now.getDate() !== today.getDate()) today = now;
		}, 60_000);
		return () => clearInterval(timer);
	});

	const weekday = $derived(today.toLocaleDateString(undefined, { weekday: 'short' }));
</script>

<span class="flex flex-col items-center leading-none" aria-hidden="true">
	<span class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{weekday}</span>
	<span class="mt-0.5 text-lg font-semibold tabular-nums">{today.getDate()}</span>
</span>
