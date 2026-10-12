<script lang="ts">
	import { ChevronRight } from '@lucide/svelte';
	import type { ArtifactMessageEvent } from '#lib/chat/types.ts';
	import ArtifactSnapshot from '#lib/chat/cards/ArtifactSnapshot.svelte';
	import { reveal } from '#styles/motion.ts';

	let { events }: { events: ArtifactMessageEvent[] } = $props();
	let open = $state(false);
	let container: HTMLElement | undefined = $state();
	const id = $props.id();

	function handleContentIntroEnd() {
		container?.scrollIntoView({ behavior: 'smooth', block: 'end' });
	}
</script>

<div
	bind:this={container}
	class="w-full rounded-lg border border-border/50 bg-muted/20 text-sm text-muted-foreground"
>
	<button
		type="button"
		class="flex w-full items-center gap-2 px-4 py-3 text-left font-medium hover:text-foreground"
		aria-expanded={open}
		aria-controls={id}
		onclick={() => (open = !open)}
	>
		<ChevronRight
			class={[
				'size-3.5 transition-transform duration-(--motion-page) motion-reduce:transition-none',
				open && 'rotate-90'
			]}
		/>
		Earlier proposal
	</button>
	<div {id}>
		{#if open}
			<div transition:reveal onintroend={handleContentIntroEnd}>
				<div class="space-y-3 px-4 pb-4">
					{#each events as event, index (index)}
						{#if event.snapshot}
							<ArtifactSnapshot artifact={event.snapshot} />
						{:else}
							<p>Snapshot unavailable for this earlier proposal.</p>
						{/if}
					{/each}
				</div>
			</div>
		{/if}
	</div>
</div>
