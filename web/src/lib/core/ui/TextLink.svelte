<!-- A link set in running text or beside a message: "View in Calendar", say. -->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAnchorAttributes } from 'svelte/elements';

	let { class: extra, children, ...rest }: HTMLAnchorAttributes & { children: Snippet } = $props();
</script>

<a class={['text-sm font-medium text-primary hover:underline', extra]} {...rest}>
	{@render children()}
</a>
