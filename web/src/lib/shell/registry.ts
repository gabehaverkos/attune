import { LayoutGrid } from '@lucide/svelte';
import { INTERFACE as CALENDAR } from '#lib/calendar/interface.ts';
import {
	type Icon,
	type InterfaceDescriptor,
	interfaceEnabled,
	type InterfacesPayload
} from '#lib/shell/interfaces.ts';
import type { ChatThreadPage } from '#lib/chat/types.ts';
import { cachedFetch, hydrateCache, prefetch } from '#lib/core/api.ts';

// === The interfaces ====================================================================

/**
 * Every interface Attune offers on the web. Each declares itself in its own
 * `interface.ts`; adding one means listing it here.
 *
 * Read when asked for, never as this module loads. The dev server reloads an
 * edited interface's modules in an order that can reach this one before the
 * interface has finished declaring itself; a list built at load then held an
 * empty slot, and every page failed until the server was restarted.
 */
export function offeredInterfaces(): readonly InterfaceDescriptor[] {
	return [CALENDAR].filter(Boolean);
}

export function interfaceDescriptor(key: string): InterfaceDescriptor | undefined {
	return offeredInterfaces().find((descriptor) => descriptor.key === key);
}

/** An interface the backend names but the web doesn't know still renders, with a generic mark. */
export function interfaceIcon(key: string): Icon {
	return interfaceDescriptor(key)?.icon ?? LayoutGrid;
}

// === Warming their data ================================================================

/**
 * Warm the caches the shell reads next.
 *
 * `interfaces` comes from the layout's server load, so enablement is already
 * known here without another request before interface-owned prefetches.
 */
export async function prefetchApplicationState(interfaces: InterfacesPayload): Promise<void> {
	hydrateCache({ '/api/interfaces': interfaces });

	// Only surfaces the user enabled are worth warming; a disabled one's API refuses.
	const interfacePromises = offeredInterfaces()
		.filter((descriptor) => interfaceEnabled(interfaces, descriptor.key))
		.flatMap((descriptor) => descriptor.data.map(prefetch));

	const chatPromise = cachedFetch<ChatThreadPage>('/api/chat/thread', fetch).then((workspace) =>
		Promise.allSettled(
			workspace.threads.map((thread) =>
				prefetch(`/api/chat/thread?thread_id=${encodeURIComponent(thread.id)}`)
			)
		)
	);

	await Promise.allSettled([chatPromise, ...interfacePromises]);
}
