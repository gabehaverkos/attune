<!--
	A legal document's text and the contact line every one ends with, as both the
	dialog and the document's own page show it. `show` follows a link to the other.
-->
<script lang="ts">
	import PrivacyNotice from './PrivacyNotice.svelte';
	import TermsOfUse from './TermsOfUse.svelte';
	import { AUTHOR_URL, type LegalDocument } from './terms';

	let { document, show }: { document: LegalDocument; show: (document: LegalDocument) => void } =
		$props();
</script>

<div class="legal text-base">
	{#if document === 'terms'}
		<TermsOfUse {show} />
	{:else}
		<PrivacyNotice {show} />
	{/if}
	<h2>Contact</h2>
	<!-- eslint-disable svelte/no-navigation-without-resolve -- an outside link -->
	<p>
		Attune is built and run by Gabe Haverkos. Questions, and requests about your data, can be sent
		through
		<a href={AUTHOR_URL} target="_blank" rel="noopener">Gabe's site</a>.
	</p>
</div>

<style>
	.legal :global(h2) {
		margin-top: 1.75rem;
		font-size: 1.125rem;
		font-weight: 600;
	}
	.legal :global(p),
	.legal :global(ul) {
		margin-top: 0.75rem;
		line-height: 1.65;
	}
	.legal :global(ul) {
		list-style: disc;
		padding-left: 1.25rem;
	}
	.legal :global(li + li) {
		margin-top: 0.35rem;
	}
	.legal :global(a),
	.legal :global(button) {
		text-decoration: underline;
		cursor: pointer;
	}
</style>
