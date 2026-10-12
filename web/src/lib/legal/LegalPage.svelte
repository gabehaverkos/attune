<!--
	A legal document on its own page, for linking to from outside the app (Google's
	consent screen asks for both). Inside the app they open as a dialog instead.
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import LegalText from './LegalText.svelte';
	import { LEGAL_TITLES, LEGAL_UPDATED, type LegalDocument } from './terms';

	let { document }: { document: LegalDocument } = $props();

	const show = (next: LegalDocument) => goto(resolve(next === 'terms' ? '/terms' : '/privacy'));
</script>

<svelte:head>
	<title>{LEGAL_TITLES[document]} · Attune</title>
</svelte:head>

<div class="min-h-screen bg-background px-4 py-12 sm:px-6">
	<article class="mx-auto max-w-2xl text-foreground">
		<a href={resolve('/')} class="text-sm text-muted-foreground hover:underline">Attune</a>
		<h1 class="mt-4 text-4xl font-semibold tracking-tight">{LEGAL_TITLES[document]}</h1>
		<p class="mt-2 text-sm text-muted-foreground">Last updated {LEGAL_UPDATED}</p>
		<LegalText {document} {show} />
	</article>
</div>
