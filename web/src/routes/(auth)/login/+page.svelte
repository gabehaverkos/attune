<script lang="ts">
	import TextField from '#lib/core/ui/TextField.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';
	import { enhance } from '$app/forms';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import AuthCard from '#lib/auth/AuthCard.svelte';
	import { PRIMARY_BUTTON, SECONDARY_BUTTON } from '#lib/auth/buttons.ts';
	import GoogleIcon from '#lib/auth/GoogleIcon.svelte';
	import LegalDialog from '#lib/legal/LegalDialog.svelte';
	import type { LegalDocument } from '#lib/legal/terms.ts';
	let { form } = $props();
	let loading = $state(false);
	let reading = $state<LegalDocument | null>(null);
</script>

<svelte:head>
	<title>Sign in · Attune</title>
</svelte:head>

<LegalDialog bind:document={reading} />

<AuthCard current="login">
	{#if form?.message}
		<Notice tone="error" class="text-center font-medium">{form.message}</Notice>
	{:else if page.url.searchParams.has('reset')}
		<Notice tone="info" class="text-center">Your password is changed. Sign in with it.</Notice>
	{/if}

	<div class="space-y-3">
		<form
			class="space-y-6"
			method="POST"
			use:enhance={() => {
				loading = true;
				return async ({ update }) => {
					loading = false;
					await update();
				};
			}}
		>
			<div class="space-y-4">
				<TextField
					id="email"
					label="Email address"
					name="email"
					type="email"
					required
					autocomplete="email"
					placeholder="you@example.com"
				/>
				<TextField
					id="password"
					label="Password"
					name="password"
					type="password"
					required
					autocomplete="current-password"
					placeholder="••••••••"
				/>
			</div>
			<button type="submit" disabled={loading} class={PRIMARY_BUTTON}>
				{loading ? 'Signing in…' : 'Sign in'}
			</button>
			<p class="text-center text-sm text-muted-foreground">
				<a href={resolve('/(auth)/forgot')} class="underline">Forgot your password?</a>
			</p>
		</form>

		<form method="POST" action="/oauth/google/start">
			<button type="submit" class={SECONDARY_BUTTON}>
				<GoogleIcon />
				Google
			</button>
			<!-- Google opens an account for someone new, so it says what they agree to. -->
			<p class="mt-2 text-center text-sm text-muted-foreground">
				New to Attune? Continuing with Google means you agree to the
				<button type="button" class="underline" onclick={() => (reading = 'terms')}>
					Terms of Use
				</button>
				and
				<button type="button" class="underline" onclick={() => (reading = 'privacy')}>
					Privacy Notice
				</button>.
			</p>
		</form>
	</div>
</AuthCard>
