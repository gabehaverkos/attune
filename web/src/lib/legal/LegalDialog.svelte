<!--
	The Terms of Use or Privacy Notice, over the page that links to it, so a form
	being filled in stays as it is. Each links to the other in place. This is how
	the app shows them; their own pages (`/terms`, `/privacy`) are for linking from
	outside it.
-->
<script lang="ts">
	import { X } from '@lucide/svelte';
	import LegalText from './LegalText.svelte';
	import { LEGAL_TITLES, LEGAL_UPDATED, type LegalDocument } from './terms';

	let { document = $bindable(null) }: { document?: LegalDocument | null } = $props();

	let dialog: HTMLDialogElement;
	let body = $state<HTMLElement>();

	$effect(() => {
		if (document && !dialog.open) dialog.showModal();
		else if (!document && dialog.open) dialog.close();
	});

	function show(next: LegalDocument) {
		document = next;
		body?.scrollTo({ top: 0 });
	}
</script>

<dialog
	bind:this={dialog}
	aria-labelledby="legal-title"
	class="legal-dialog m-auto w-[calc(100%-2rem)] max-w-2xl rounded-2xl border border-border bg-card p-0 text-foreground shadow-xl"
	onclose={() => (document = null)}
	onclick={(event) => event.target === dialog && dialog.close()}
>
	{#if document}
		<div class="flex max-h-[85vh] flex-col">
			<header class="flex items-start justify-between gap-4 border-b border-border px-6 py-5">
				<div>
					<h2 id="legal-title" class="text-2xl font-semibold tracking-tight">
						{LEGAL_TITLES[document]}
					</h2>
					<p class="mt-1 text-sm text-muted-foreground">Last updated {LEGAL_UPDATED}</p>
				</div>
				<button
					type="button"
					class="rounded-lg p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
					aria-label="Close"
					onclick={() => dialog.close()}
				>
					<X class="size-5" />
				</button>
			</header>
			<div bind:this={body} class="overflow-y-auto px-6 pb-6">
				<LegalText {document} {show} />
			</div>
		</div>
	{/if}
</dialog>

<style>
	.legal-dialog::backdrop {
		background: rgb(0 0 0 / 0.4);
	}
</style>
