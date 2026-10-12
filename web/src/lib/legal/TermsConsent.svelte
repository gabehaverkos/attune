<!--
	The box to tick before opening an account, as part of the form that opens it.
	The documents open over the form, so it keeps what was typed.
-->
<script lang="ts">
	import LegalDialog from './LegalDialog.svelte';
	import { TERMS_FIELD, type LegalDocument } from './terms';

	let { checked = $bindable(false) }: { checked?: boolean } = $props();
	let reading = $state<LegalDocument | null>(null);
</script>

<label for="accept-terms" class="flex cursor-pointer items-start gap-3 text-sm text-foreground">
	<input
		id="accept-terms"
		name={TERMS_FIELD}
		type="checkbox"
		required
		bind:checked
		class="mt-0.5 size-4 shrink-0 cursor-pointer accent-foreground"
	/>
	<span>
		I agree to the
		<button type="button" class="font-medium underline" onclick={() => (reading = 'terms')}
			>Terms of Use</button
		>
		and
		<button type="button" class="font-medium underline" onclick={() => (reading = 'privacy')}
			>Privacy Notice</button
		>.
	</span>
</label>

<LegalDialog bind:document={reading} />
