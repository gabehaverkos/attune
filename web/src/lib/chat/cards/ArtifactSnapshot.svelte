<script lang="ts">
	import type { Artifact } from '#lib/chat/types.ts';
	import { artifactView } from '#lib/chat/cards/registry.ts';

	let {
		artifact,
		muted = true
	}: {
		artifact: Artifact;
		muted?: boolean;
	} = $props();
	const view = $derived(artifactView(artifact));
</script>

<div
	class={[
		'artifact-snapshot space-y-4',
		muted && 'rounded-xl border border-border/70 bg-muted/20 p-4 grayscale opacity-60'
	]}
>
	{#if view?.Snapshot}
		<view.Snapshot {artifact} />
	{:else}
		<p class="text-sm font-medium">{artifact.title}</p>
		<p class="text-sm text-muted-foreground">{artifact.description}</p>
	{/if}
</div>
