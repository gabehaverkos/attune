<!--
	Where the link in a password reset email lands, carrying its token. A link
	that has expired or been used arrives without one.
-->
<script lang="ts">
	import TextField from '#lib/core/ui/TextField.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { PRIMARY_BUTTON } from '#lib/auth/buttons.ts';
	let { form } = $props();
	let loading = $state(false);

	const token = $derived(page.url.searchParams.get('token'));
</script>

<svelte:head>
	<title>Choose a new password · Attune</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center bg-background px-4 py-12 sm:px-6 lg:px-8">
	<div
		class="w-full max-w-md space-y-6 rounded-2xl border border-border/80 bg-card p-8 shadow-xl animate-fade-in"
	>
		{#if !token}
			<h1 class="text-center text-2xl font-semibold tracking-tight text-foreground">
				Link not working
			</h1>
			<p class="text-center text-base text-muted-foreground">
				This link has expired or was already used. Ask for a new one.
			</p>
			<a href={resolve('/(auth)/forgot')} class={PRIMARY_BUTTON}>Send a new link</a>
		{:else}
			<h1 class="text-center text-2xl font-semibold tracking-tight text-foreground">
				Choose a new password
			</h1>
			{#if form?.message}
				<Notice tone="error" class="text-center font-medium">{form.message}</Notice>
			{/if}
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
				<input type="hidden" name="token" value={token} />
				<div class="space-y-4">
					<TextField
						id="password"
						label="New password"
						name="password"
						type="password"
						required
						minlength={8}
						autocomplete="new-password"
						placeholder="••••••••"
					/>
					<TextField
						id="confirm_password"
						label="Confirm password"
						name="confirm_password"
						type="password"
						required
						autocomplete="new-password"
						placeholder="••••••••"
					/>
				</div>
				<button type="submit" disabled={loading} class={PRIMARY_BUTTON}>
					{loading ? 'Saving…' : 'Save password'}
				</button>
			</form>
		{/if}
	</div>
</div>
