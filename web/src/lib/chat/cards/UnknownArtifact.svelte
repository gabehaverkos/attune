<script lang="ts">
	import Notice from '#lib/core/ui/Notice.svelte';
	import SectionLabel from '#lib/core/ui/SectionLabel.svelte';

	import type { Artifact } from '#lib/chat/types.ts';
	import ArtifactActions from '#lib/chat/cards/ArtifactActions.svelte';
	import ArtifactStatusBadge from '#lib/chat/cards/ArtifactStatusBadge.svelte';

	let {
		artifact,
		pending,
		errorMessage,
		onDeny
	}: {
		artifact: Artifact;
		pending: boolean;
		errorMessage: string;
		onDeny: (reason: string | null) => void;
	} = $props();
	const canDeny = $derived(artifact.status === 'draft' || artifact.status === 'ready');
</script>

<div class="space-y-4">
	<div class="flex items-start justify-between gap-3">
		<div>
			<SectionLabel>{artifact.type} · version {artifact.schema_version}</SectionLabel>
			<h2 class="mt-1 text-xl font-semibold">{artifact.title}</h2>
		</div>
		<ArtifactStatusBadge status={artifact.status} />
	</div>
	{#if artifact.description}<p class="text-sm text-muted-foreground">{artifact.description}</p>{/if}
	<Notice tone="info">This artifact version cannot be edited in the current application.</Notice>
	{#if errorMessage}<p class="text-sm text-destructive" role="alert">{errorMessage}</p>{/if}
	{#if canDeny}<ArtifactActions showDeny {pending} {onDeny} onRetry={() => {}} />{/if}
</div>
