<script lang="ts">
	import { LogOut, Trash2 } from '@lucide/svelte';
	import { theme, setTheme, type ThemePreference } from '#lib/core/theme.svelte.ts';
	import SectionLabel from '#lib/core/ui/SectionLabel.svelte';
	import { resolve } from '$app/paths';
	import { useInterfacesShell } from '#lib/shell/interfaces.ts';
	import InterfaceSettingsWorkspace from './InterfaceSettingsWorkspace.svelte';

	type ShellUser = {
		name?: string | null;
		email?: string | null;
		image?: string | null;
		isAnonymous?: boolean | null;
	};

	let {
		user = null,
		pendingEmail = null,
		seededDemo = false
	}: {
		user?: ShellUser | null;
		pendingEmail?: string | null;
		/** This demo is the example person's account, which is not one to keep. */
		seededDemo?: boolean;
	} = $props();
	const interfacesShell = useInterfacesShell();

	let confirmingDelete = $state(false);
	/** A demo is deleted by logging out of it, so only a kept account is offered this. */
	const deletable = $derived(!!user && !user.isAnonymous);
</script>

<div class="p-5">
	<header class="flex items-center gap-3">
		<div
			class="flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border bg-background"
		>
			{#if user?.image}
				<img src={user.image} alt="" class="size-full object-cover" />
			{:else}
				<span class="text-body font-semibold text-muted-foreground">
					{user?.isAnonymous && !pendingEmail
						? 'D'
						: ((user?.isAnonymous ? pendingEmail : user?.email) ?? '?')
								.trim()
								.charAt(0)
								.toUpperCase()}
				</span>
			{/if}
		</div>
		<p class="min-w-0 truncate text-body text-foreground">
			{user?.isAnonymous ? pendingEmail || 'Demo account' : user?.email?.trim() || 'Account'}
		</p>
	</header>

	{#if user?.isAnonymous && pendingEmail}
		<section class="mt-5 border-t border-border/70 pt-4" aria-label="Verify your email">
			<SectionLabel>Verify your email</SectionLabel>
			<p class="mt-2 text-body text-muted-foreground">
				Open the link sent to {pendingEmail} to finish your account. Until then it works like a demo:
				it's deleted after a week unused, and logging out ends it.
			</p>
			<a
				href={resolve('/keep')}
				class="mt-3 block rounded-md border border-border px-3 py-2 text-center text-body font-medium text-foreground hover:bg-muted"
				>Send it again or use another email</a
			>
		</section>
	{:else if user?.isAnonymous && seededDemo}
		<section class="mt-5 border-t border-border/70 pt-4" aria-label="Example account">
			<SectionLabel>Example account</SectionLabel>
			<p class="mt-2 text-body text-muted-foreground">
				You're trying Attune as an example person, with their memory and calendar already filled in.
				To use it as yourself, start again with nothing in it or create an account.
			</p>
			<form method="POST" action="/demo/blank">
				<button
					type="submit"
					class="mt-3 block w-full cursor-pointer rounded-md bg-foreground px-3 py-2 text-center text-body font-medium text-background hover:bg-foreground/90"
					>Start with a blank demo</button
				>
			</form>
			<a
				href={resolve('/(auth)/signup')}
				class="mt-2 block rounded-md border border-border px-3 py-2 text-center text-body font-medium text-foreground hover:bg-muted"
				>Create an account</a
			>
		</section>
	{:else if user?.isAnonymous}
		<section class="mt-5 border-t border-border/70 pt-4" aria-label="Keep this account">
			<SectionLabel>Keep this account</SectionLabel>
			<p class="mt-2 text-body text-muted-foreground">
				Add a way to sign in and everything here stays. A demo you don't keep is deleted after a
				week unused, and logging out ends it.
			</p>
			<a
				href={resolve('/keep')}
				class="mt-3 block rounded-md bg-foreground px-3 py-2 text-center text-body font-medium text-background hover:bg-foreground/90"
				>Keep this account</a
			>
		</section>
	{/if}

	<InterfaceSettingsWorkspace onchange={(payload) => interfacesShell.update(payload)} />

	<section class="mt-5 border-t border-border/70 pt-4" aria-label="Appearance">
		<SectionLabel>Appearance</SectionLabel>
		<div class="mt-3 flex gap-2" role="group" aria-label="Color theme">
			{#each ['system', 'light', 'dark'] as preference (preference)}
				<button
					type="button"
					aria-pressed={theme.preference === preference}
					class="flex-1 rounded-md border border-border px-3 py-2 text-body capitalize hover:bg-muted aria-pressed:bg-accent aria-pressed:text-accent-foreground"
					onclick={() => setTheme(preference as ThemePreference)}
					>{preference === 'system' ? 'Device' : preference}</button
				>
			{/each}
		</div>
		<p class="mt-2 text-sm text-muted-foreground">
			Device follows your system appearance. Your choice is saved on this browser.
		</p>
	</section>

	<!--
		Logout lives at the bottom of the account surface rather than in the rail:
		it is rare and one-way, and does not deserve a permanent one-click target.
	-->
	<form method="POST" action="/logout" class="mt-5 border-t border-border/70 pt-4">
		<button
			type="submit"
			class="flex w-full cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-left text-body text-foreground transition-colors hover:bg-muted"
		>
			<LogOut class="size-4 text-muted-foreground" />
			<span>Log out</span>
		</button>
	</form>

	<!-- Asked twice: nothing brings an account back. -->
	{#if deletable && confirmingDelete}
		<form method="POST" action="/account/delete" class="mt-1 px-3 py-2">
			<p class="text-body text-muted-foreground">
				This deletes your account and everything in it: your conversations, what Attune remembers,
				and your calendar. It can't be undone.
			</p>
			<div class="mt-3 flex gap-2">
				<button
					type="submit"
					class="flex-1 cursor-pointer rounded-md bg-destructive px-3 py-2 text-body font-medium text-destructive-foreground hover:bg-destructive/90"
					>Delete everything</button
				>
				<button
					type="button"
					class="flex-1 cursor-pointer rounded-md border border-border px-3 py-2 text-body text-foreground hover:bg-muted"
					onclick={() => (confirmingDelete = false)}>Cancel</button
				>
			</div>
		</form>
	{:else if deletable}
		<button
			type="button"
			class="mt-1 flex w-full cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-left text-body text-foreground transition-colors hover:bg-muted"
			onclick={() => (confirmingDelete = true)}
		>
			<Trash2 class="size-4 text-muted-foreground" />
			<span>Delete your account</span>
		</button>
	{/if}
</div>
