<script lang="ts">
	import { interfaceDescriptor, interfaceIcon } from '#lib/shell/registry.ts';
	import type { UserInterface } from '#lib/shell/interfaces.ts';
	import RailButton from '#lib/core/ui/RailButton.svelte';

	let {
		interfaces,
		active,
		changed,
		onToggle
	}: {
		/** The enabled ones; they are turned on and off from the account panel. */
		interfaces: UserInterface[];
		/** The one open beside the conversation, if any. */
		active: string | null;
		/** Those whose data chat changed since they were last open. */
		changed: string[];
		onToggle: (key: string) => void;
	} = $props();
</script>

<nav aria-label="Tools" class="flex min-h-0 flex-col items-center gap-2 overflow-y-auto">
	{#each interfaces as surface (surface.key)}
		{@const Mark = interfaceDescriptor(surface.key)?.Mark}
		{@const Icon = interfaceIcon(surface.key)}
		{@const hasNews = changed.includes(surface.key)}
		<div class="relative shrink-0">
			<RailButton
				onclick={() => onToggle(surface.key)}
				aria-pressed={active === surface.key}
				aria-label="{surface.name}{hasNews ? ', changed since you last looked' : ''}"
				title={surface.name}
				class="aria-pressed:bg-accent"
			>
				{#if Mark}<Mark />{:else}<Icon class="size-5" />{/if}
			</RailButton>
			{#if hasNews}
				<span
					aria-hidden="true"
					class="pointer-events-none absolute right-0.5 top-0.5 size-2.5 rounded-full bg-primary ring-2 ring-background"
				></span>
			{/if}
		</div>
	{/each}
</nav>
