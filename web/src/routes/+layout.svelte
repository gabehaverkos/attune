<script lang="ts">
	import { page } from '$app/state';
	import { afterNavigate, invalidateAll } from '$app/navigation';
	import { onMount } from 'svelte';
	import { initializeTheme } from '#lib/core/theme.svelte.ts';
	onMount(initializeTheme);
	import SystemRail from '#lib/shell/SystemRail.svelte';
	import InterfaceLauncher from '#lib/shell/InterfaceLauncher.svelte';
	import favicon from '#lib/assets/favicon.svg';
	import type { LayoutData } from './$types';
	import {
		enabledInterfaces,
		interfaceEnabled,
		type InterfacesPayload,
		type OnboardingState,
		setInterfacesContext,
		setOnboardingContext
	} from '#lib/shell/interfaces.ts';
	import {
		getQueryClient,
		hydrateCache,
		invalidateCache,
		removeServerStateCache
	} from '#lib/core/api.ts';
	import {
		interfaceDescriptor,
		offeredInterfaces,
		prefetchApplicationState
	} from '#lib/shell/registry.ts';
	import { QueryClientProvider } from '@tanstack/svelte-query';
	import '../styles/app.css';

	let { children, data }: { children: import('svelte').Snippet; data: LayoutData } = $props();
	const user = $derived(data.user);
	const queryClient = getQueryClient();

	// Seeded from the layout load so the launcher is correct on first paint, then
	// kept current by settings changes without a round trip.
	let interfacesOverride = $state<InterfacesPayload | null>(null);
	const interfaces = $derived(interfacesOverride ?? data.interfaces);
	const availableInterfaces = $derived(enabledInterfaces(interfaces));

	// Interfaces whose data changed from chat since the user last had them open.
	let changedInterfaces = $state<string[]>([]);
	// Bumped when that happens to the one that is open, which reloads instead.
	let activeRevision = $state(0);
	function markChanged(urls: readonly string[]) {
		const touched = offeredInterfaces()
			.filter((descriptor) => descriptor.data.some((url) => urls.includes(url)))
			.map((descriptor) => descriptor.key);
		if (activeInterface && touched.includes(activeInterface)) activeRevision += 1;
		const unseen = touched.filter((key) => key !== activeInterface);
		changedInterfaces = [...new Set([...changedInterfaces, ...unseen])];
	}

	setInterfacesContext({
		read: () => interfaces,
		update: updateInterfaces,
		changed: markChanged
	});

	const currentPath = $derived(page.url.pathname);
	const isChatRoute = $derived(currentPath === '/');
	let onboardingOverride = $state<OnboardingState | null>(null);
	const onboarding = $derived(onboardingOverride ?? data.onboarding);
	function updateInterfaces(next: InterfacesPayload) {
		interfacesOverride = next;
		if (activeInterface && !interfaceEnabled(next, activeInterface)) closeInterface();
	}
	setOnboardingContext({
		get state() {
			return onboarding;
		},
		update: (next) => {
			onboardingOverride = next;
		}
	});

	// Seed the server-state cache with the interfaces the page load already
	// resolved, so nothing refetches what the layout paid for.
	//
	// The first seed happens during setup rather than in an effect: the account
	// rail mounts the tools settings eagerly, inside its closed popover, and
	// a child's onMount runs before this component's effects. Seeding from an
	// effect alone lands one fetch too late, so every page load paid for
	// /api/interfaces twice.
	//
	// An empty payload is the load's failure fallback, never a real answer: the
	// backend always names its interfaces, enabled or not. Leaving it unseeded
	// lets the settings panel fetch for itself and recover.
	const seedInterfaces = () => {
		if (data.interfaces.interfaces.length) hydrateCache({ '/api/interfaces': data.interfaces });
	};
	seedInterfaces();
	// Later navigations can reload the layout's data; keep the cache with it.
	$effect(seedInterfaces);

	// Track the user ID to only clear the client cache on actual login/logout transitions
	// and avoid wiping the SSR/hydration cache on initial page loads or refreshes.
	let previousUserId = $state<string | undefined>();
	let userTrackingInitialized = $state(false);

	$effect(() => {
		if (!userTrackingInitialized) {
			previousUserId = user?.id;
			userTrackingInitialized = true;
			return;
		}
		if (user?.id !== previousUserId) {
			removeServerStateCache();
			onboardingOverride = null;
			changedInterfaces = [];
			interfacesOverride = null;
			previousUserId = user?.id;
		}
	});

	// The interface whose view is open, and the link that opened it, if one did.
	let activeInterface = $state<string | null>(null);
	let activeLink = $state<URLSearchParams | null>(null);
	const activeDescriptor = $derived(
		activeInterface && interfaceEnabled(interfaces, activeInterface)
			? interfaceDescriptor(activeInterface)
			: undefined
	);

	function openInterface(key: string, link: URLSearchParams | null = null) {
		activeInterface = key;
		activeLink = link;
		changedInterfaces = changedInterfaces.filter((changed) => changed !== key);
	}

	function closeInterface() {
		activeInterface = null;
		activeLink = null;
	}

	// Client-side navigation to / (e.g. clicking "View in Calendar" while already
	// on the chat route) doesn't remount this layout, so onMount alone would miss it.
	// afterNavigate fires on the initial load too, so it covers both cases.
	afterNavigate(() => {
		if (!user) return;

		const url = new URL(window.location.href);
		if (url.pathname !== '/') return;
		for (const descriptor of offeredInterfaces()) {
			if (!descriptor.link?.opens(url.searchParams)) continue;
			// A link into an interface is still just a link; it cannot open a
			// surface the user has turned off. The
			// parameters are cleared either way so a stale deep link does not linger
			// in the address bar.
			const link = new URLSearchParams(url.searchParams);
			if (interfaceEnabled(interfaces, descriptor.key)) {
				openInterface(descriptor.key, link);
			}
			for (const param of descriptor.link.params) url.searchParams.delete(param);
			window.history.replaceState({}, '', url);
			return;
		}
	});

	// The link in a verification email may be opened in another tab. Coming back
	// to this one picks up that the account is now kept.
	$effect(() => {
		if (!user?.isAnonymous || !data.pendingEmail) return;
		const recheck = () => {
			if (document.visibilityState === 'visible') void invalidateAll();
		};
		document.addEventListener('visibilitychange', recheck);
		return () => document.removeEventListener('visibilitychange', recheck);
	});

	onMount(() => {
		const deviceTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
		const storedTimezone = document.cookie
			.split('; ')
			.find((entry) => entry.startsWith('user-timezone='))
			?.slice('user-timezone='.length);
		if (storedTimezone !== deviceTimezone) {
			document.cookie = `user-timezone=${deviceTimezone}; Path=/; SameSite=Lax`;
			// Capture this on auth pages too, before the first authenticated load.
			// Returning users may arrive directly with a missing or changed cookie:
			// refresh their data without remounting the shell and replaying its fade.
			if (user) {
				invalidateCache('/api');
				void invalidateAll().catch((error) => {
					console.warn('Timezone data could not be refreshed.', error);
				});
			}
		}
		if (!user) return;

		// Let the requested page render and hydrate its query before warming the
		// remaining workspaces.
		const prefetchTimer = window.setTimeout(() => {
			void prefetchApplicationState(interfaces);
		}, 250);
		// Memory needs no warming: each turn reads what the graph holds now.
		return () => window.clearTimeout(prefetchTimer);
	});
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<QueryClientProvider client={queryClient}>
	<!-- These pages stand on their own, signed in or not: a guest can still sign
	     in or sign up, and the verified page is opened from an email. -->
	{#if !user || ['/login', '/signup', '/forgot', '/reset', '/verified'].includes(currentPath)}
		{@render children()}
	{:else}
		<div class="app-viewport">
			<!--
				Two rails of equal width with the route between them. Both asides are
				sized from --rail-width, which is what centres the conversation on the
				viewport. One grid row, so source order places the columns.
			-->
			<div
				class="app-canvas grid grid-rows-[minmax(0,1fr)] overflow-hidden text-foreground animate-fade-in"
				style:grid-template-columns={isChatRoute
					? 'auto minmax(0, 1fr) auto'
					: 'minmax(0, 1fr) auto'}
			>
				{#if isChatRoute}
					<aside
						class="flex w-[var(--rail-width)] min-h-0 flex-col py-3 pl-4 pr-2 sm:pl-6 lg:pl-8 motion-safe:transition-opacity"
					>
						<InterfaceLauncher
							interfaces={availableInterfaces}
							active={activeInterface}
							changed={changedInterfaces}
							onToggle={(key) => (activeInterface === key ? closeInterface() : openInterface(key))}
						/>
					</aside>
				{/if}

				<div class="relative min-h-0 overflow-hidden bg-background">
					{#key currentPath}
						<div
							class={[
								'h-full w-full overflow-hidden pt-2 sm:pt-3 lg:pt-3',
								isChatRoute ? 'pb-4 sm:pb-5 lg:pb-6' : 'px-4 pb-2 sm:px-5 sm:pb-3 lg:px-6 lg:pb-3'
							]}
						>
							{@render children()}
						</div>
					{/key}
				</div>

				<aside
					class="flex w-[var(--rail-width)] min-h-0 flex-col py-3 pl-2 pr-4 sm:pr-6 lg:pr-8 motion-safe:transition-opacity"
				>
					<SystemRail
						{user}
						pendingEmail={data.pendingEmail}
						seededDemo={data.seededDemo}
						covered={!!activeDescriptor}
					/>
				</aside>
			</div>
		</div>
		{#if activeDescriptor}
			<activeDescriptor.View link={activeLink} onclose={closeInterface} revision={activeRevision} />
		{/if}
	{/if}
</QueryClientProvider>
