<!-- The small uppercase label over a card or a group in a list. -->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';

	let {
		class: extra,
		children,
		...rest
	}: HTMLAttributes<HTMLParagraphElement> & { children: Snippet } = $props();
</script>

<p class={['text-xs font-semibold uppercase tracking-wide text-muted-foreground', extra]} {...rest}>
	{@render children()}
</p>
