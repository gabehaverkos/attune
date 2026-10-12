import { defineEnvVars } from '@sveltejs/kit/env';

/** A variable the app runs without: unset and empty both read as undefined. */
const optional = { schema: (value: string | undefined) => value || undefined };

/**
 * Every environment variable the web app reads, all at runtime.
 * Compose passes them; none is needed to build.
 */
export const variables = defineEnvVars({
	DATABASE_URL: optional,
	BACKEND_URL: optional,
	BETTER_AUTH_URL: optional,
	// Required to run: auth.ts stops the server without it, but the build has none.
	BETTER_AUTH_SECRET: optional,
	GOOGLE_CLIENT_ID: optional,
	GOOGLE_CLIENT_SECRET: optional,
	RESEND_API_KEY: optional
});
