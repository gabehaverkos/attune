<!--
	A boxed message inside a card or panel: something went wrong (`error`),
	something to know (`info`), or something that worked (`success`, which lays
	its contents out in a row, a message and an action).
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';

	type Tone = 'error' | 'info' | 'success';

	let {
		tone,
		class: extra,
		role = tone === 'error' ? 'alert' : undefined,
		children,
		...rest
	}: HTMLAttributes<HTMLDivElement> & { tone: Tone; children: Snippet } = $props();

	const tones: Record<Tone, string> = {
		error: 'border-destructive/30 bg-destructive/5 text-sm text-destructive',
		info: 'border-border bg-muted/35 text-sm',
		success: 'flex items-center justify-between gap-3 border-primary/20 bg-accent/40'
	};
</script>

<div {role} class={['rounded-lg border p-3', tones[tone], extra]} {...rest}>
	{@render children()}
</div>
