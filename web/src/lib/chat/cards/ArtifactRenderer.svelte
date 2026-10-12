<script lang="ts">
	import type { Artifact } from '#lib/chat/types.ts';
	import { artifactView } from '#lib/chat/cards/registry.ts';
	import UnknownArtifact from '#lib/chat/cards/UnknownArtifact.svelte';

	let {
		artifact,
		pending,
		errorMessage,
		siblings = [],
		onUpdate,
		onAccept,
		onDeny,
		onRetry
	}: {
		artifact: Artifact;
		pending: boolean;
		errorMessage: string;
		siblings?: Artifact[];
		onUpdate: (payload: Record<string, unknown>) => void;
		onAccept: () => void;
		onDeny: (reason: string | null) => void;
		onRetry: () => void;
	} = $props();

	const view = $derived(artifactView(artifact));
</script>

{#if view}
	<view.Component
		{artifact}
		{pending}
		{errorMessage}
		{siblings}
		{onUpdate}
		{onAccept}
		{onDeny}
		{onRetry}
	/>
{:else}
	<UnknownArtifact {artifact} {pending} {errorMessage} {onDeny} />
{/if}
