import { auth } from '#lib/server/auth.ts';
import { svelteKitHandler } from 'better-auth/svelte-kit';
import { building } from '$app/env';
import { redirect } from '@sveltejs/kit';
import type { Handle, HandleFetch } from '@sveltejs/kit/hooks';

export const handle: Handle = async ({ event, resolve }) => {
	const path = event.url.pathname;

	// Skip auth and routing checks for SvelteKit assets, favicon, and system files
	if (path.startsWith('/_app') || path.startsWith('/favicon.ico') || path.startsWith('/static')) {
		return resolve(event);
	}

	// Vite's dev server can see Caddy's HTTPS request as HTTP, which makes
	// Better Auth's origin-based route detection miss its callback path. Route
	// auth requests explicitly so they never fall through to the FastAPI proxy.
	if (path.startsWith('/api/auth/')) {
		return auth.handler(event.request);
	}

	// 1. Fetch session from Better Auth
	const session = await auth.api.getSession({
		headers: event.request.headers
	});

	// 2. Populate event.locals so it's available in load functions/actions
	if (session) {
		event.locals.user = session.user;
		event.locals.session = session.session;
	} else {
		event.locals.user = null;
		event.locals.session = null;
	}

	// Global Route Protection Rules
	const isPublicPath =
		path === '/welcome' ||
		path === '/login' ||
		path === '/signup' ||
		path === '/forgot' ||
		path === '/demo' ||
		path === '/oauth/google/start' ||
		path.startsWith('/api/auth/');
	const isApplicationApi = path.startsWith('/api/') && !path.startsWith('/api/auth/');
	// The legal documents' own pages, linked from outside the app, and where the
	// links in Attune's emails land: open to everyone.
	const isLegalPath =
		path === '/terms' || path === '/privacy' || path === '/verified' || path === '/reset';

	if (!event.locals.user && !isPublicPath && !isLegalPath && !isApplicationApi) {
		throw redirect(302, '/welcome');
	}

	// A guest may still sign in to, or create, a real account.
	if (event.locals.user && !event.locals.user.isAnonymous && isPublicPath) {
		throw redirect(302, '/');
	}
	// A guest is already in a demo, so the landing page sends them back to it.
	if (event.locals.user?.isAnonymous && (path === '/welcome' || path === '/demo')) {
		throw redirect(302, '/');
	}

	// 3. Return the svelteKitHandler
	return svelteKitHandler({
		event,
		resolve: (requestEvent) =>
			resolve(requestEvent, {
				filterSerializedResponseHeaders: (name) => name === 'content-type'
			}),
		auth,
		building
	});
};

export const handleFetch: HandleFetch = async ({ event, request, fetch }) => {
	const url = new URL(request.url);
	if (url.pathname.startsWith('/api/')) {
		if (event.locals.session) {
			request.headers.set('Authorization', `Bearer ${event.locals.session.token}`);
		}
	}
	return fetch(request);
};
