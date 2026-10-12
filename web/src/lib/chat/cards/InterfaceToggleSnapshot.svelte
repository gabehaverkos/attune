<script lang="ts">
	import type { Artifact } from '#lib/chat/types.ts';
	import { interfaceTogglePayload, interfaceToggleSummary } from '#lib/shell/interfaces.ts';

	// How turning an interface on or off reads once decided, or in an earlier message.
	let { artifact }: { artifact: Artifact } = $props();

	const payload = $derived(interfaceTogglePayload(artifact));
</script>

<p class="text-sm font-medium">{artifact.title}</p>
{#if payload}<p class="text-sm text-muted-foreground">{interfaceToggleSummary(payload)}</p>{/if}
