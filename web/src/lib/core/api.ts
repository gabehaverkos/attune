import { browser } from '$app/env';
import { isCancelledError, QueryClient, type QueryKey } from '@tanstack/svelte-query';

// === Requests ==========================================================================

export class ApiError extends Error {
	readonly status: number;
	readonly detail: unknown;

	constructor(status: number, message: string, detail?: unknown) {
		super(message);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
	}
}

export type ApiRequestOptions = Omit<RequestInit, 'body'> & {
	body?: unknown;
	invalidate?: string[];
};

function deviceTimezone(): string {
	if (typeof Intl === 'undefined') return 'UTC';
	return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

function isNativeBody(value: unknown): value is BodyInit {
	return (
		typeof value === 'string' ||
		(typeof FormData !== 'undefined' && value instanceof FormData) ||
		(typeof URLSearchParams !== 'undefined' && value instanceof URLSearchParams) ||
		(typeof Blob !== 'undefined' && value instanceof Blob) ||
		(typeof ArrayBuffer !== 'undefined' && value instanceof ArrayBuffer) ||
		(typeof ReadableStream !== 'undefined' && value instanceof ReadableStream)
	);
}

async function parseBody(response: Response): Promise<unknown> {
	if (response.status === 204) return undefined;
	const text = await response.text();
	if (!text) return undefined;
	const contentType = response.headers.get('content-type') ?? '';
	if (contentType.includes('json')) {
		try {
			return JSON.parse(text);
		} catch {
			return text;
		}
	}
	return text;
}

export async function apiRequest<T = void>(
	path: string,
	options: ApiRequestOptions = {},
	requestFetch: typeof fetch = fetch
): Promise<T> {
	const { body, invalidate, headers: suppliedHeaders, ...init } = options;
	const headers = new Headers(suppliedHeaders);
	if (!headers.has('X-User-Timezone')) {
		headers.set('X-User-Timezone', deviceTimezone());
	}

	let requestBody: BodyInit | null | undefined;
	if (body !== undefined && body !== null) {
		if (isNativeBody(body)) {
			requestBody = body;
		} else {
			headers.set('content-type', 'application/json');
			requestBody = JSON.stringify(body);
		}
	} else {
		requestBody = body as null | undefined;
	}

	const response = await requestFetch(path, { ...init, headers, body: requestBody });
	const payload = await parseBody(response);
	if (!response.ok) {
		const detail =
			typeof payload === 'object' && payload !== null && 'detail' in payload
				? (payload as { detail: unknown }).detail
				: payload;
		// A detail is a sentence, or `{ code, message }` when the caller needs the code too.
		const described =
			typeof detail === 'object' && detail !== null && 'message' in detail
				? (detail as { message: unknown }).message
				: detail;
		const message =
			typeof described === 'string' ? described : `Request failed (${response.status})`;
		throw new ApiError(response.status, message, detail);
	}

	if (invalidate?.length) {
		for (const url of invalidate) invalidateCache(url);
	}
	return payload as T;
}

// === The query client ==================================================================

function createQueryClient() {
	return new QueryClient({
		defaultOptions: {
			queries: {
				staleTime: 15 * 60_000,
				gcTime: 30 * 60_000,
				retry: 1,
				refetchOnWindowFocus: false,
				refetchOnReconnect: false
			}
		}
	});
}

let browserClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
	if (!browser) return createQueryClient();
	browserClient ??= createQueryClient();
	return browserClient;
}

// === The cache =========================================================================

const ROOT_KEY = 'server-state';

/**
 * A response's cache key, read from its URL alone: the path under /api, then its
 * query parameters. Keys nest the way URLs do, so `/api/calendar` is a prefix of
 * `/api/calendar/range?…` and `/api/chat/thread` of each thread's own page.
 */
function queryKeyForUrl(url: string): QueryKey {
	const parsed = new URL(url, 'http://server-state.local');
	const segments = parsed.pathname
		.replace(/^\/api\/?/, '')
		.split('/')
		.filter(Boolean);
	const params = Object.fromEntries(
		[...parsed.searchParams].sort(([a], [b]) => a.localeCompare(b))
	);
	return Object.keys(params).length ? [ROOT_KEY, ...segments, params] : [ROOT_KEY, ...segments];
}

export function hydrateCache(entries: Record<string, unknown>) {
	if (!browser) return;
	const client = getQueryClient();
	for (const [url, value] of Object.entries(entries)) {
		client.setQueryData(queryKeyForUrl(url), value);
	}
}

export async function cachedFetch<T>(
	url: string,
	loadFetch: typeof fetch,
	init?: RequestInit
): Promise<T> {
	if (!browser) return apiRequest<T>(url, init, loadFetch);

	const query = {
		queryKey: queryKeyForUrl(url),
		queryFn: () => apiRequest<T>(url, init, loadFetch)
	};
	try {
		return await getQueryClient().fetchQuery(query);
	} catch (error) {
		// Clearing the cache when the account changes cancels what was loading for
		// the old one. Load it again, for the account that is signed in now.
		if (isCancelledError(error)) return getQueryClient().fetchQuery(query);
		throw error;
	}
}

/** Warm a response the user is likely to ask for next. */
export async function prefetch(url: string): Promise<void> {
	if (!browser) return;
	await getQueryClient().prefetchQuery({
		queryKey: queryKeyForUrl(url),
		queryFn: () => apiRequest(url)
	});
}

/** Mark a URL's cached response, and everything nested beneath it, stale. */
export function invalidateCache(url: string) {
	if (!browser) return;
	void getQueryClient().invalidateQueries({
		queryKey: queryKeyForUrl(url),
		refetchType: 'none'
	});
}

export function removeServerStateCache() {
	if (!browser) return;
	getQueryClient().removeQueries({ queryKey: [ROOT_KEY] });
}

// === Shared types ======================================================================

export type EntityId = string;
export type ISODate = string;
export type ISODateTime = string;
