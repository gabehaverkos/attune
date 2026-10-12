<!--
	A round button on one of the side rails. `ref` is the element, for a popover
	that anchors to it.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	let {
		ref = $bindable(),
		class: extra,
		children,
		...rest
	}: HTMLButtonAttributes & {
		ref?: HTMLButtonElement;
		children: Snippet;
	} = $props();
</script>

<button
	bind:this={ref}
	type="button"
	class={[
		'inline-flex size-14 shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-full border border-border/80 bg-card/75 text-foreground shadow-raised transition-colors hover:bg-muted active:scale-98',
		extra
	]}
	{...rest}
>
	{@render children()}
</button>
