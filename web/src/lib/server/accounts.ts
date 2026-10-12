import { BACKEND_URL } from '$app/env/private';
import { type Cookies, error, redirect, type RequestEvent } from '@sveltejs/kit';
import { auth } from '#lib/server/auth.ts';

// === Rate limits =======================================================================

/**
 * A per-address budget for the sign-in and sign-up forms.
 *
 * Better Auth limits requests that reach its own handler, but these forms call
 * its API directly, which skips that. One process serves the web app, so memory
 * is enough; a restart only forgets who was close to the limit.
 */
const windows = new Map<string, { started: number; count: number }>();

export function allow(key: string, limit: number, windowMs: number): boolean {
	const now = Date.now();
	const current = windows.get(key);
	if (!current || now - current.started > windowMs) {
		if (windows.size > 10_000) windows.clear();
		windows.set(key, { started: now, count: 1 });
		return true;
	}
	current.count += 1;
	return current.count <= limit;
}

// === Demo accounts =====================================================================

/** How a demo starts: with nothing in it, or with an example person's data already there. */
export type DemoKind = 'blank' | 'seeded';

/** The kind the form asked for. Anything else is a blank demo. */
export function demoKind(value: unknown): DemoKind {
	return value === 'seeded' ? 'seeded' : 'blank';
}

/** Marks this browser's demo as the example person's, for as long as a demo lasts. */
const SEEDED_DEMO_COOKIE = 'attune-seeded-demo';

/**
 * Give the new guest the example person's account, and say whether it took. A
 * demo that could not be seeded still opens, blank.
 */
async function seed(event: RequestEvent, token: string): Promise<boolean> {
	const headers = new Headers({ authorization: `Bearer ${token}` });
	// The device's timezone, once a page has noted it: the example's events keep
	// their clock times there.
	const timezone = event.cookies.get('user-timezone');
	if (timezone) headers.set('x-user-timezone', timezone);
	try {
		const response = await fetch(`${BACKEND_URL || 'http://127.0.0.1:8000'}/api/demo/seed`, {
			method: 'POST',
			headers
		});
		if (!response.ok) console.warn(`The demo could not be seeded (${response.status}).`);
		return response.ok;
	} catch (cause) {
		console.warn('The demo could not be seeded.', cause);
		return false;
	}
}

/**
 * Start a guest account and go straight to chat. `headers` are the request's
 * unless it has just left another account, whose session they would still carry.
 */
export async function startDemo(
	event: RequestEvent,
	kind: DemoKind = 'blank',
	headers: Headers = event.request.headers
): Promise<never> {
	if (!allow(`demo:${event.getClientAddress()}`, 5, 60 * 60_000)) {
		error(429, 'Too many demo accounts from here. Try again later.');
	}
	const guest = await auth.api.signInAnonymous({ headers });
	if (kind === 'seeded' && guest?.token && (await seed(event, guest.token))) {
		event.cookies.set(SEEDED_DEMO_COOKIE, '1', {
			path: '/',
			httpOnly: true,
			sameSite: 'lax',
			maxAge: 7 * 24 * 60 * 60
		});
	} else {
		event.cookies.delete(SEEDED_DEMO_COOKIE, { path: '/' });
	}
	redirect(303, '/');
}

/** Whether this guest's demo is the example person's account. */
export function seededDemo(cookies: Cookies, isAnonymous: boolean): boolean {
	if (!isAnonymous) {
		if (cookies.get(SEEDED_DEMO_COOKIE)) cookies.delete(SEEDED_DEMO_COOKIE, { path: '/' });
		return false;
	}
	return Boolean(cookies.get(SEEDED_DEMO_COOKIE));
}

/** Leave the demo this guest is in and start one with nothing in it. */
export async function restartBlank(event: RequestEvent): Promise<never> {
	await auth.api.signOut({ headers: event.request.headers });
	const headers = new Headers(event.request.headers);
	headers.delete('cookie');
	return startDemo(event, 'blank', headers);
}

// === Deleting an account ===============================================================

/** Have the backend delete the account that is asking, and say whether it did. */
async function remove(event: RequestEvent): Promise<boolean> {
	try {
		const response = await fetch(`${BACKEND_URL || 'http://127.0.0.1:8000'}/api/account`, {
			method: 'DELETE',
			headers: { authorization: `Bearer ${event.locals.session?.token}` }
		});
		if (!response.ok) console.warn(`The account could not be deleted (${response.status}).`);
		return response.ok;
	} catch (cause) {
		console.warn('The account could not be deleted.', cause);
		return false;
	}
}

/**
 * A guest logging out has no way back in, so the demo goes with them. One that
 * could not be deleted is left to expire like any other.
 */
export async function endDemo(event: RequestEvent): Promise<void> {
	if (!event.locals.user?.isAnonymous) return;
	await remove(event);
	event.cookies.delete(SEEDED_DEMO_COOKIE, { path: '/' });
	event.cookies.delete(PENDING_EMAIL_COOKIE, { path: '/' });
}

/**
 * Delete the account that is asking, with everything in it, and leave for the
 * landing page. The backend deletes the user and all that hangs from it,
 * sessions included, so the cookie is cleared after and may already be dead.
 */
export async function deleteAccount(event: RequestEvent): Promise<never> {
	if (!event.locals.session) redirect(303, '/welcome');
	if (!(await remove(event))) error(502, 'Your account could not be deleted. Nothing was removed.');
	try {
		await auth.api.signOut({ headers: event.request.headers });
	} catch {
		// The session went with the account.
	}
	event.cookies.delete(SEEDED_DEMO_COOKIE, { path: '/' });
	event.cookies.delete(PENDING_EMAIL_COOKIE, { path: '/' });
	redirect(303, '/welcome');
}

// === Keeping a demo account ============================================================

/** The address a guest asked to keep their account with, until its link is opened. */
const PENDING_EMAIL_COOKIE = 'attune-pending-email';

/**
 * Give a guest's account an email and password. The password is set now; the
 * address only once the link sent to it is opened, which also keeps the account
 * (afterEmailVerification). Until then the account stays a demo.
 */
export async function keepWithEmail(
	userId: string,
	headers: Headers,
	cookies: Cookies,
	email: string,
	password: string
): Promise<void> {
	const ctx = await auth.$context;
	const hash = await ctx.password.hash(password);
	const accounts = await ctx.internalAdapter.findAccounts(userId);
	if (accounts.some((account) => account.providerId === 'credential')) {
		await ctx.internalAdapter.updatePassword(userId, hash);
	} else {
		await ctx.internalAdapter.linkAccount({
			userId,
			providerId: 'credential',
			accountId: userId,
			password: hash
		});
	}
	await auth.api.changeEmail({ body: { newEmail: email, callbackURL: '/verified' }, headers });
	cookies.set(PENDING_EMAIL_COOKIE, email, {
		path: '/',
		httpOnly: true,
		sameSite: 'lax',
		maxAge: 7 * 24 * 60 * 60
	});
}

/** The address waiting to be verified, for a guest who gave one. */
export function pendingEmail(cookies: Cookies, isAnonymous: boolean): string | null {
	if (!isAnonymous) {
		if (cookies.get(PENDING_EMAIL_COOKIE)) cookies.delete(PENDING_EMAIL_COOKIE, { path: '/' });
		return null;
	}
	return cookies.get(PENDING_EMAIL_COOKIE) ?? null;
}

/**
 * Start a guest account in this request, returning request headers that carry
 * its new session, so later calls in the same request act as it.
 */
export async function startGuest(request: Request): Promise<{ userId: string; headers: Headers }> {
	const { headers: responseHeaders, response } = await auth.api.signInAnonymous({
		headers: request.headers,
		returnHeaders: true
	});
	if (!response) throw new Error('No guest account was created.');
	const session = responseHeaders
		.getSetCookie()
		.map((cookie) => cookie.split(';')[0])
		.join('; ');
	const headers = new Headers(request.headers);
	headers.set('cookie', session);
	return { userId: response.user.id, headers };
}
