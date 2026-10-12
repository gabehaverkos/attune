<script lang="ts">
	import TextField from '#lib/core/ui/TextField.svelte';
	import Notice from '#lib/core/ui/Notice.svelte';
	import { enhance } from '$app/forms';
	import AuthCard from '#lib/auth/AuthCard.svelte';
	import { PRIMARY_BUTTON, SECONDARY_BUTTON } from '#lib/auth/buttons.ts';
	import GoogleIcon from '#lib/auth/GoogleIcon.svelte';
	import TermsConsent from '#lib/legal/TermsConsent.svelte';
	import { TERMS_FIELD } from '#lib/legal/terms.ts';
	let { form } = $props();
	let loading = $state(false);
	let agreed = $state(false);
	// The email and the agreement come first; the password is chosen on a second step.
	let step = $state<'email' | 'password'>('email');
	let email = $state('');
	let mismatch = $state(false);
</script>

<svelte:head>
	<title>Create an account · Attune</title>
</svelte:head>

<AuthCard current="signup">
	{#if mismatch}
		<Notice tone="error" class="text-center font-medium">The passwords don't match.</Notice>
	{:else if form?.message}
		<Notice tone="error" class="text-center font-medium">{form.message}</Notice>
	{/if}

	<div class="space-y-3">
		<form
			class="space-y-6"
			method="POST"
			use:enhance={({ formData, cancel }) => {
				if (step === 'email') {
					cancel();
					step = 'password';
					return;
				}
				mismatch = formData.get('password') !== formData.get('confirm_password');
				if (mismatch) {
					cancel();
					return;
				}
				loading = true;
				return async ({ update }) => {
					loading = false;
					await update();
				};
			}}
		>
			{#if step === 'email'}
				<TextField
					id="email"
					label="Email address"
					name="email"
					type="email"
					required
					autocomplete="email"
					placeholder="you@example.com"
					value={email}
					oninput={(e) => (email = e.currentTarget.value)}
				/>
				<TermsConsent bind:checked={agreed} />
				<button type="submit" disabled={!agreed} class={PRIMARY_BUTTON}>Continue</button>
			{:else}
				<input type="hidden" name="email" value={email} />
				<input type="hidden" name={TERMS_FIELD} value="on" />
				<p class="text-sm text-muted-foreground">
					Creating an account for <span class="font-medium text-foreground">{email}</span>.
					<button
						type="button"
						class="font-medium underline"
						onclick={() => {
							step = 'email';
							mismatch = false;
						}}>Change</button
					>
				</p>
				<div class="space-y-4">
					<TextField
						id="password"
						label="Password"
						name="password"
						type="password"
						required
						minlength={8}
						autocomplete="new-password"
						placeholder="••••••••"
					/>
					<TextField
						id="confirm-password"
						label="Confirm password"
						name="confirm_password"
						type="password"
						required
						autocomplete="new-password"
						placeholder="••••••••"
					/>
				</div>
				<button type="submit" disabled={loading} class={PRIMARY_BUTTON}>
					{loading ? 'Creating account…' : 'Create account'}
				</button>
			{/if}
		</form>

		{#if step === 'email'}
			<!-- Google opens the account from its own form, so it carries the agreement too. -->
			<form method="POST" action="/oauth/google/start">
				<input type="hidden" name="from" value="signup" />
				<input type="hidden" name="accept_terms" value={agreed ? 'on' : ''} />
				<button type="submit" disabled={!agreed} class={SECONDARY_BUTTON}>
					<GoogleIcon />
					Google
				</button>
			</form>
		{/if}
	</div>
</AuthCard>
