<!--
	Where the link in a verification email lands. It says the email is verified
	and leaves the choice of going on: back to the window already open, or into
	Attune from here, where opening the link has signed in.
-->
<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { CircleCheck } from '@lucide/svelte';
	import { PRIMARY_BUTTON } from '#lib/auth/buttons.ts';

	// The codes Better Auth adds to the link's destination when it can't verify.
	const PROBLEMS: Record<string, string> = {
		TOKEN_EXPIRED: 'This link has expired. Sign in to get a new one.',
		INVALID_USER:
			'This browser is signed in to a different account. Sign out, then open the link again.',
		USER_NOT_FOUND:
			'This link has already been used. If you opened it before, your email is verified.'
	};
	const error = $derived(page.url.searchParams.get('error'));
	const problem = $derived(
		error ? (PROBLEMS[error] ?? "This link didn't work. Sign in to get a new one.") : null
	);
</script>

<svelte:head>
	<title>{problem ? 'Link not verified' : 'Email verified'} · Attune</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center bg-background px-4 py-12 sm:px-6 lg:px-8">
	<div
		class="w-full max-w-md space-y-6 rounded-2xl border border-border/80 bg-card p-8 text-center shadow-xl animate-fade-in"
	>
		{#if problem}
			<h1 class="text-2xl font-semibold tracking-tight text-foreground">Link not verified</h1>
			<p class="text-base text-muted-foreground">{problem}</p>
		{:else}
			<CircleCheck class="mx-auto size-14 text-foreground" strokeWidth={1.5} aria-hidden="true" />
			<h1 class="text-2xl font-semibold tracking-tight text-foreground">Email verified</h1>
			<p class="text-base text-muted-foreground">
				Your account is set up. You can close this tab and go back to Attune, or continue here.
			</p>
		{/if}
		<a href={resolve('/')} class={PRIMARY_BUTTON}>Continue to Attune</a>
	</div>
</div>
