<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';

	import type { ArtifactViewProps } from '#lib/chat/cards/registry.ts';
	import { interfaceIcon } from '#lib/shell/registry.ts';
	import { interfaceTogglePayload, interfaceToggleSummary } from '#lib/shell/interfaces.ts';

	// Turning an interface on or off: nothing to edit, so a compact card saying
	// what it is and what changes, with one button to do it.
	let { artifact, pending, errorMessage, onAccept, onDeny }: ArtifactViewProps = $props();

	const payload = $derived(interfaceTogglePayload(artifact));
	const editable = $derived(artifact.status === 'draft' || artifact.status === 'ready');
	const Icon = $derived(interfaceIcon(payload?.key ?? ''));
	const name = $derived(payload?.name ?? 'this interface');
</script>

<div class="space-y-4" aria-busy={pending || artifact.status === 'executing'}>
	{#if !payload}
		<Notice tone="error">This proposal contains data that cannot be displayed safely.</Notice>
	{:else}
		<div class="flex items-start gap-3 rounded-xl border border-border bg-muted/35 p-4">
			<div class="shrink-0 rounded-lg border border-border bg-background p-2.5">
				<Icon class="size-5" />
			</div>
			<div class="min-w-0">
				<p class="font-semibold">{name}</p>
				{#if payload.description}<p class="mt-1 text-sm text-muted-foreground">
						{payload.description}
					</p>{/if}
				<p class="mt-3 border-t border-border pt-3 text-sm">{interfaceToggleSummary(payload)}</p>
			</div>
		</div>
		{#if artifact.status === 'failed'}
			<Notice tone="error">{artifact.error?.message ?? `${name} could not be changed.`}</Notice>
		{/if}
		{#if errorMessage}<p class="text-sm text-destructive" role="alert">{errorMessage}</p>{/if}
		{#if editable}
			<div class="flex flex-wrap items-center justify-center gap-2">
				<button
					type="button"
					class="h-10 rounded-md px-3 text-sm font-medium text-muted-foreground hover:bg-muted"
					onclick={() => onDeny(null)}
					disabled={pending}>Not now</button
				>
				{#if artifact.status === 'ready'}
					<Button type="button" onclick={onAccept} disabled={pending}
						>{pending
							? payload.enabled
								? 'Turning on…'
								: 'Turning off…'
							: `Turn ${payload.enabled ? 'on' : 'off'} ${name}`}</Button
					>
				{/if}
			</div>
		{/if}
	{/if}
</div>
