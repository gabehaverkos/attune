import { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET } from '$app/env/private';
import { auth, GOOGLE_SIGN_IN, isGuestEmail } from '#lib/server/auth.ts';
import { allow, keepWithEmail } from '#lib/server/accounts.ts';
import { fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

/** What went wrong linking Google, from the error Better Auth redirects back with. */
const LINK_ERRORS: Record<string, string> = {
	account_already_linked_to_different_user:
		'That Google account already has an Attune account. Sign in with it instead; this demo stays here until it expires.'
};

export const load: PageServerLoad = async ({ locals, url }) => {
	if (!locals.user?.isAnonymous) redirect(303, '/');
	const error = url.searchParams.get('error');
	return {
		googleEnabled: Boolean(GOOGLE_CLIENT_ID && GOOGLE_CLIENT_SECRET),
		linkError: error ? (LINK_ERRORS[error] ?? 'Google sign-in did not finish. Try again.') : null
	};
};

/**
 * A guest keeps their account by giving it a way to sign in again. The account
 * itself stays the same, so everything made in the demo comes along.
 */
export const actions: Actions = {
	email: async (event) => {
		const user = event.locals.user;
		if (!user?.isAnonymous) redirect(303, '/');
		if (!allow(`keep:${event.getClientAddress()}`, 5, 10 * 60_000)) {
			return fail(429, { message: 'Too many attempts. Try again in a few minutes.' });
		}
		const formData = await event.request.formData();
		const email = String(formData.get('email') ?? '')
			.trim()
			.toLowerCase();
		const password = String(formData.get('password') ?? '');
		if (!email.includes('@') || email.length < 5 || isGuestEmail(email)) {
			return fail(400, { message: 'Please enter a valid email address.' });
		}
		if (password.length < 8 || password.length > 255) {
			return fail(400, { message: 'Password must be at least 8 characters long.' });
		}

		const ctx = await auth.$context;
		const owner = await ctx.internalAdapter.findUserByEmail(email);
		if (owner) {
			return fail(409, {
				message: 'That email already has an account. Sign in to it instead.',
				signIn: true
			});
		}
		await keepWithEmail(user.id, event.request.headers, event.cookies, email, password);
		return { sent: email };
	},

	google: async (event) => {
		if (!event.locals.user?.isAnonymous) redirect(303, '/');
		const origin = event.url.origin;
		const result = await auth.api.linkSocialAccount({
			body: {
				provider: 'google',
				callbackURL: `${origin}/`,
				errorCallbackURL: `${origin}/keep`,
				disableRedirect: true
			},
			headers: event.request.headers
		});
		redirect(303, result.url, { external: GOOGLE_SIGN_IN });
	}
};
