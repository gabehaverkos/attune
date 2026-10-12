<script lang="ts">
	import TextField from '#lib/core/ui/TextField.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';
	import { enhance } from '$app/forms';
	import { resolve } from '$app/paths';
	let { data, form } = $props();
	let loading = $state(false);
</script>

<svelte:head>
	<title>Keep your account · Attune</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center bg-background px-4 py-12 sm:px-6 lg:px-8">
	<div
		class="w-full max-w-md space-y-8 rounded-2xl border border-border/80 bg-card p-8 shadow-xl animate-fade-in"
	>
		<div>
			<h2 class="mt-6 text-center text-4xl font-semibold tracking-tight text-foreground">
				Keep your account
			</h2>
			<p class="mt-2 text-center text-base text-muted-foreground">
				Everything from the demo stays: your conversations, what Attune remembers, and your
				calendar.
			</p>
		</div>

		{#if form?.sent}
			<Notice tone="info" class="text-center">
				Check {form.sent} for a link. Opening it keeps your account. Didn't get it? Send it again below.
			</Notice>
		{:else if form?.message}
			<Notice tone="error" class="text-center font-medium">
				{form.message}
				{#if 'signIn' in form}<a href={resolve('/(auth)/login')} class="underline">Sign in</a>{/if}
			</Notice>
		{:else if data.linkError}
			<Notice tone="error" class="text-center font-medium">
				{data.linkError}
				<a href={resolve('/(auth)/login')} class="underline">Sign in</a>
			</Notice>
		{/if}

		{#if data.googleEnabled}
			<form method="POST" action="?/google">
				<button
					type="submit"
					class="flex w-full items-center justify-center gap-3 rounded-lg border border-border bg-background px-4 py-3.5 text-base font-semibold text-foreground transition hover:bg-muted"
				>
					<svg class="size-5" viewBox="0 0 24 24" aria-hidden="true">
						<path
							fill="#4285F4"
							d="M21.6 12.23c0-.71-.06-1.4-.18-2.07H12v3.92h5.38a4.6 4.6 0 0 1-2 3.02v2.54h3.24c1.9-1.75 2.98-4.33 2.98-7.41Z"
						/>
						<path
							fill="#34A853"
							d="M12 22c2.7 0 4.97-.9 6.62-2.36l-3.24-2.54c-.9.6-2.05.96-3.38.96-2.61 0-4.82-1.76-5.61-4.13H3.04v2.62A10 10 0 0 0 12 22Z"
						/>
						<path
							fill="#FBBC05"
							d="M6.39 13.93A6.02 6.02 0 0 1 6.07 12c0-.67.12-1.32.32-1.93V7.45H3.04A10 10 0 0 0 2 12c0 1.61.39 3.14 1.04 4.55l3.35-2.62Z"
						/>
						<path
							fill="#EA4335"
							d="M12 5.94c1.47 0 2.79.51 3.83 1.5l2.87-2.88A9.63 9.63 0 0 0 12 2a10 10 0 0 0-8.96 5.45l3.35 2.62C7.18 7.7 9.39 5.94 12 5.94Z"
						/>
					</svg>
					Continue with Google
				</button>
			</form>

			<div class="flex items-center gap-3 text-sm text-muted-foreground">
				<div class="h-px flex-1 bg-border"></div>
				<span>or use email</span>
				<div class="h-px flex-1 bg-border"></div>
			</div>
		{/if}

		<form
			class="space-y-6"
			method="POST"
			action="?/email"
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
					autocomplete="new-password"
					placeholder="••••••••"
				/>
			</div>
			<button
				type="submit"
				disabled={loading}
				class="flex w-full justify-center rounded-lg bg-foreground px-4 py-3.5 text-base font-semibold text-background transition hover:bg-foreground/90 disabled:opacity-50"
			>
				{loading ? 'Sending link...' : 'Send verification link'}
			</button>
		</form>

		<p class="text-center text-base text-muted-foreground">
			<a href={resolve('/')} class="font-medium text-foreground hover:underline">Back to Attune</a>
		</p>
	</div>
</div>
