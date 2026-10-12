<script lang="ts">
	import AuthCard from '#lib/auth/AuthCard.svelte';
	import { PRIMARY_BUTTON } from '#lib/auth/buttons.ts';
	import TermsConsent from '#lib/legal/TermsConsent.svelte';
	import { TERMS_FIELD } from '#lib/legal/terms.ts';

	const KINDS = [
		{
			value: 'blank',
			label: 'Start blank',
			about:
				'Attune knows nothing about you. Introduce yourself and watch it learn, the way a new account begins.'
		},
		{
			value: 'seeded',
			label: 'Start seeded',
			about:
				"Opens with an example person's memory and calendar already in place, so you can see what Attune does once it knows someone."
		}
	] as const;

	let agreed = $state(false);
	// The agreement comes first; how the demo starts is chosen on a second step.
	let step = $state<'terms' | 'kind'>('terms');
	let starting = $state<string | null>(null);
</script>

<svelte:head>
	<title>Attune</title>
</svelte:head>

<!-- Where anyone signed out lands: the demo first, an account for those who want one. -->
<AuthCard current="demo">
	<form
		method="POST"
		action="/demo"
		class="space-y-5"
		onsubmit={(event) => {
			if (step === 'terms') {
				event.preventDefault();
				step = 'kind';
				return;
			}
			starting = (event.submitter as HTMLButtonElement | null)?.value ?? 'blank';
		}}
	>
		{#if step === 'terms'}
			<TermsConsent bind:checked={agreed} />
			<button type="submit" disabled={!agreed} class={PRIMARY_BUTTON}>Try the demo</button>
		{:else}
			<input type="hidden" name={TERMS_FIELD} value="on" />
			<p class="text-sm text-muted-foreground">Choose how the demo starts.</p>
			<!-- Not disabled while starting: a disabled button would drop its kind from the form. -->
			<div class="space-y-3" class:pointer-events-none={starting !== null}>
				{#each KINDS as kind (kind.value)}
					<button
						type="submit"
						name="kind"
						value={kind.value}
						class="block w-full cursor-pointer rounded-lg border border-border bg-background px-4 py-3.5 text-left transition hover:bg-muted {starting !==
							null && starting !== kind.value
							? 'opacity-50'
							: ''}"
					>
						<span class="block text-base font-semibold text-foreground">
							{starting === kind.value ? 'Starting…' : kind.label}
						</span>
						<span class="mt-1 block text-sm text-muted-foreground">{kind.about}</span>
					</button>
				{/each}
			</div>
		{/if}
	</form>
</AuthCard>
