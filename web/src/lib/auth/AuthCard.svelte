<!--
	The card every signed-out page sits in: the title, the page's own option, and
	below it the other two ways in. The three are trying the demo, creating an
	account, and signing in.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import { resolve } from '$app/paths';
	import AuthDivider from './AuthDivider.svelte';
	import { SECONDARY_BUTTON } from './buttons';

	type Option = 'demo' | 'signup' | 'login';

	let { current, children }: { current: Option; children: Snippet } = $props();

	const OPTIONS = [
		{ key: 'demo', label: 'Try the demo', href: resolve('/(auth)/welcome') },
		{ key: 'signup', label: 'Create an account', href: resolve('/(auth)/signup') },
		{ key: 'login', label: 'Sign in', href: resolve('/(auth)/login') }
	] as const;
	const others = $derived(OPTIONS.filter((option) => option.key !== current));
</script>

<div class="flex min-h-screen items-center justify-center bg-background px-4 py-12 sm:px-6 lg:px-8">
	<div
		class="w-full max-w-md space-y-8 rounded-2xl border border-border/80 bg-card p-8 shadow-xl animate-fade-in"
	>
		<!-- A stand-in until Attune has its own wordmark. -->
		<h1 class="mt-6 text-center text-4xl font-semibold tracking-tight text-foreground">Attune</h1>

		{@render children()}

		<AuthDivider />

		<div class="grid grid-cols-2 gap-3">
			{#each others as option (option.key)}
				<a href={option.href} class={SECONDARY_BUTTON}>{option.label}</a>
			{/each}
		</div>
	</div>
</div>
