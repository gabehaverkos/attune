<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';

	let {
		showDeny = false,
		showRetry = false,
		pending = false,
		onDeny,
		onRetry
	}: {
		showDeny?: boolean;
		showRetry?: boolean;
		pending?: boolean;
		onDeny: (reason: string | null) => void;
		onRetry: () => void;
	} = $props();
</script>

<div class="flex flex-wrap justify-end gap-2">
	{#if showDeny}<button
			type="button"
			class="h-10 rounded-md px-3 text-sm font-medium text-destructive hover:bg-destructive/10"
			onclick={() => onDeny(null)}
			disabled={pending}>Deny</button
		>{/if}
	{#if showRetry}<Button type="button" onclick={onRetry} disabled={pending}
			>{pending ? 'Retrying…' : 'Retry'}</Button
		>{/if}
</div>
