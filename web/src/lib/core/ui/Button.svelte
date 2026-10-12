<!--
	The app's buttons. `primary` confirms the main action, `secondary` offers the
	way out beside it, `destructive` does something that cannot be taken back. `md` is
	for forms and cards; `sm` for compact controls inside panels.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	type Variant = 'primary' | 'secondary' | 'ghost' | 'destructive';
	type Size = 'md' | 'sm';

	let {
		variant = 'primary',
		size = 'md',
		type = 'button',
		class: extra,
		children,
		...rest
	}: HTMLButtonAttributes & { variant?: Variant; size?: Size; children: Snippet } = $props();

	const sizes: Record<Size, string> = {
		md: 'h-10 rounded-md px-4 text-sm',
		sm: 'h-9 cursor-pointer rounded-lg px-3 text-caption'
	};
	const variants: Record<Variant, string> = {
		primary: 'bg-primary font-medium text-primary-foreground',
		secondary: 'border border-border hover:bg-muted',
		ghost: 'font-medium text-muted-foreground hover:bg-muted hover:text-foreground',
		destructive: 'bg-destructive font-medium text-destructive-foreground'
	};
</script>

<button
	{type}
	class={[
		sizes[size],
		variants[variant],
		'cursor-pointer transition-colors duration-(--motion-fast) focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50',
		extra
	]}
	{...rest}
>
	{@render children()}
</button>
