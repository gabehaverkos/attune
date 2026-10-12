<!-- A labelled text input, as on the sign-in and sign-up forms. -->
<script lang="ts">
	import type { HTMLInputAttributes } from 'svelte/elements';

	let {
		id,
		label,
		class: extra,
		...rest
	}: HTMLInputAttributes & { id: string; label: string } = $props();
</script>

<div>
	<label for={id} class="mb-1.5 block text-sm font-medium text-muted-foreground">{label}</label>
	<input
		{id}
		class={[
			'relative block w-full rounded-lg border border-border bg-background px-4 py-3 text-base text-foreground placeholder-muted-foreground/60 transition duration-(--motion-fast) focus:z-10 focus:border-primary focus:ring-1 focus:ring-primary focus:outline-none',
			extra
		]}
		{...rest}
	/>
</div>
