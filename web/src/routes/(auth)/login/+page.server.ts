import { auth } from '#lib/server/auth.ts';
import { allow } from '#lib/server/accounts.ts';
import { APIError } from 'better-auth/api';
import { fail, redirect } from '@sveltejs/kit';
import type { Actions } from './$types';

export const actions: Actions = {
	default: async (event) => {
		if (!allow(`login:${event.getClientAddress()}`, 10, 10 * 60_000)) {
			return fail(429, { message: 'Too many attempts. Try again in a few minutes.' });
		}
		const formData = await event.request.formData();
		const email = formData.get('email');
		const password = formData.get('password');

		// 1. Basic Validation
		if (typeof email !== 'string' || typeof password !== 'string') {
			return fail(400, {
				message: 'Email and password are required.'
			});
		}

		try {
			// 2. Call Better Auth to sign in with email/password
			await auth.api.signInEmail({
				body: {
					email: email.toLowerCase(),
					password: password,
					// Where the fresh link sent to an unverified email lands.
					callbackURL: '/verified'
				},
				headers: event.request.headers
			});
		} catch (e) {
			// Signing in with an unverified email sends a fresh link.
			if (e instanceof APIError && e.body?.code === 'EMAIL_NOT_VERIFIED') {
				return fail(403, {
					message: 'Verify your email first. We sent a new link to your inbox.'
				});
			}
			console.error('[AUTH LOGIN ERROR]', e);
			return fail(400, {
				message: 'Invalid email or password.'
			});
		}

		throw redirect(302, '/');
	}
};
