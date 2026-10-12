<script lang="ts">
	import TextField from '#lib/core/ui/TextField.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';
	import { enhance } from '$app/forms';
	import AuthCard from '#lib/auth/AuthCard.svelte';
	import { PRIMARY_BUTTON } from '#lib/auth/buttons.ts';
	let { form } = $props();
	let loading = $state(false);
</script>

<svelte:head>
	<title>Reset your password · Attune</title>
</svelte:head>

<AuthCard current="demo">
	{#if form?.sent}
		<Notice tone="info" class="text-center">
			If that email has an account, a link to choose a new password is on its way. It works for an
			hour.
		</Notice>
	{:else}
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
			<p class="text-sm text-muted-foreground">
				Enter your account's email and we'll send a link to choose a new password.
			</p>
			<TextField
				id="email"
				label="Email address"
				name="email"
				type="email"
				required
				autocomplete="email"
				placeholder="you@example.com"
			/>
			<button type="submit" disabled={loading} class={PRIMARY_BUTTON}>
				{loading ? 'Sending…' : 'Send the link'}
			</button>
		</form>
	{/if}
</AuthCard>
