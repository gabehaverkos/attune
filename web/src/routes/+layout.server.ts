import { apiRequest } from '#lib/core/api.ts';
import { pendingEmail, seededDemo } from '#lib/server/accounts.ts';
import type { InterfacesPayload, OnboardingState } from '#lib/shell/interfaces.ts';
import type { LayoutServerLoad } from './$types';

const NO_INTERFACES: InterfacesPayload = { interfaces: [] };

/**
 * The shell needs the enabled interfaces before its first paint, or the
 * launcher renders its tiles a frame late.
 *
 * This is a server load on purpose. A universal load would run again during
 * hydration and fetch a second time, because apiRequest stamps the request
 * with the runtime's timezone — UTC on the server, the device's in the browser
 * — so SvelteKit's inlined SSR response never matches the client's request.
 */
export const load: LayoutServerLoad = async ({ locals, cookies, fetch }) => {
	const base = {
		user: locals.user,
		pendingEmail: locals.user ? pendingEmail(cookies, !!locals.user.isAnonymous) : null,
		seededDemo: locals.user ? seededDemo(cookies, !!locals.user.isAnonymous) : false,
		timezone: cookies.get('user-timezone') ?? 'UTC'
	};
	if (!locals.user) return { ...base, interfaces: NO_INTERFACES, onboarding: null };
	// Failure must not lock an existing account out of chat. A later load retries.
	const onboarding = await apiRequest<OnboardingState>('/api/onboarding', {}, fetch).catch(
		(error) => {
			console.warn('Onboarding progress could not be loaded.', error);
			return null;
		}
	);
	try {
		return {
			...base,
			onboarding,
			interfaces: await apiRequest<InterfacesPayload>('/api/interfaces', {}, fetch)
		};
	} catch (error) {
		// A shell without its launcher still beats no shell at all, but a silent
		// empty launcher is indistinguishable from "nothing enabled".
		console.warn('Interfaces could not be loaded for the shell.', error);
		return { ...base, interfaces: NO_INTERFACES, onboarding };
	}
};
