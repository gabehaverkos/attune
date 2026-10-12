<script lang="ts">
	import { apiRequest, cachedFetch, hydrateCache } from '#lib/core/api.ts';
	import { offeredInterfaces, interfaceIcon } from '#lib/shell/registry.ts';
	import {
		type InterfacesPayload,
		useInterfaces,
		type UserInterface
	} from '#lib/shell/interfaces.ts';
	import { onMount } from 'svelte';
	import SectionLabel from '#lib/core/ui/SectionLabel.svelte';

	let { onchange }: { onchange?: (payload: InterfacesPayload) => void } = $props();

	let payload = $state<InterfacesPayload | null>(null);
	let loading = $state(true);
	let errorMessage = $state('');
	let busyKey = $state('');

	// The shell's copy once it has one, so a change made elsewhere (a card in chat)
	// shows here too; until then, what this panel loaded for itself.
	const shared = useInterfaces();
	const interfaces = $derived((shared().interfaces.length ? shared() : payload)?.interfaces ?? []);

	function apply(next: InterfacesPayload) {
		payload = next;
		hydrateCache({ '/api/interfaces': next });
		onchange?.(next);
	}

	async function load() {
		loading = true;
		errorMessage = '';
		try {
			apply(await cachedFetch<InterfacesPayload>('/api/interfaces', fetch));
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Unable to load interfaces.';
		} finally {
			loading = false;
		}
	}

	async function toggleInterface(surface: UserInterface) {
		busyKey = surface.key;
		errorMessage = '';
		try {
			apply(
				await apiRequest<InterfacesPayload>(`/api/interfaces/${surface.key}`, {
					method: 'PUT',
					body: { enabled: !surface.enabled },
					// A change here can hide an interface's data or bring it back.
					invalidate: offeredInterfaces().flatMap((descriptor) => descriptor.data)
				})
			);
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'That change could not be saved.';
		} finally {
			busyKey = '';
		}
	}

	onMount(() => void load());
</script>

<!-- A section of the account panel, where the user picks the tools on the left rail. -->
<section class="mt-5 border-t border-border/70 pt-4" aria-label="Tools">
	<div>
		<SectionLabel>Tools</SectionLabel>
		<p class="mt-2 text-body text-muted-foreground">
			Turn on the tools you want beside the conversation. Turning one off hides it; what you made in
			it stays.
		</p>

		{#if loading}
			<p class="mt-4 text-body text-muted-foreground">Loading interfaces…</p>
		{:else}
			{#each interfaces as surface (surface.key)}
				{@const Icon = interfaceIcon(surface.key)}
				<section
					class="mt-3 rounded-xl border border-border bg-background/45 p-4"
					aria-label="{surface.name} interface"
				>
					<!-- The switch stays on the heading row; only the description reflows. -->
					<div class="flex items-start justify-between gap-3">
						<div class="flex min-w-0 items-start gap-3">
							<div class="shrink-0 rounded-lg border border-border bg-background p-2.5">
								<Icon class="size-5" />
							</div>
							<div class="min-w-0">
								<h2 class="font-semibold text-foreground">{surface.name}</h2>
								<p class="mt-1 text-body text-muted-foreground">{surface.description}</p>
							</div>
						</div>
						<button
							type="button"
							role="switch"
							aria-checked={surface.enabled}
							aria-label="{surface.enabled ? 'Disable' : 'Enable'} {surface.name}"
							disabled={busyKey === surface.key}
							onclick={() => void toggleInterface(surface)}
							class={[
								'relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50',
								surface.enabled ? 'bg-foreground' : 'bg-muted-foreground/30'
							]}
						>
							<span
								class={[
									'absolute top-0.5 size-5 rounded-full bg-background transition-all',
									surface.enabled ? 'left-[1.375rem]' : 'left-0.5'
								]}
							></span>
						</button>
					</div>
				</section>
			{/each}
		{/if}

		{#if errorMessage}
			<p class="mt-4 text-body text-destructive" role="alert">{errorMessage}</p>
		{/if}
	</div>
</section>
