import { BACKEND_URL } from '$app/env/private';
import { auth } from '#lib/server/auth.ts';
import { error, type RequestHandler } from '@sveltejs/kit';

const proxy: RequestHandler = async ({ fetch, params, request, url, getClientAddress }) => {
	const session = await auth.api.getSession({ headers: request.headers });
	if (!session) {
		error(401, 'Not authenticated');
	}

	const backendUrl = BACKEND_URL || 'http://127.0.0.1:8000';
	const target = `${backendUrl}/api/${params.path ?? ''}${url.search}`;
	const headers = new Headers(request.headers);

	// Resolve the browser cookie with Better Auth, then expose only the verified
	// session token to FastAPI.
	headers.set('authorization', `Bearer ${session.session.token}`);
	headers.delete('cookie');
	// The daily chat budget is kept per visitor address. Set here, never taken from
	// the browser.
	headers.set('x-attune-client-address', getClientAddress());
	headers.delete('host');
	headers.delete('content-length');

	const response = await fetch(target, {
		method: request.method,
		headers,
		body:
			request.method === 'GET' || request.method === 'HEAD'
				? undefined
				: await request.arrayBuffer(),
		redirect: 'manual'
	});

	return new Response(response.body, {
		status: response.status,
		statusText: response.statusText,
		headers: response.headers
	});
};

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
