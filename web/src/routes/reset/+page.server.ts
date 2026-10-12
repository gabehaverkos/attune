import { auth } from '#lib/server/auth.ts';
import { allow } from '#lib/server/accounts.ts';
import { fail, redirect } from '@sveltejs/kit';
import type { Actions } from './$types';

/** Sets the new password the link in a reset email was opened to choose. */
export const actions: Actions = {
	default: async (event) => {
		if (!allow(`reset:${event.getClientAddress()}`, 10, 10 * 60_000)) {
			return fail(429, { message: 'Too many attempts. Try again in a few minutes.' });
		}
		const formData = await event.request.formData();
		const token = String(formData.get('token') ?? '');
		const password = String(formData.get('password') ?? '');

		if (password.length < 8 || password.length > 255) {
			return fail(400, { message: 'Password must be at least 8 characters long.' });
		}
		if (password !== formData.get('confirm_password')) {
			return fail(400, { message: "The passwords don't match." });
		}
		try {
			await auth.api.resetPassword({ body: { newPassword: password, token } });
		} catch {
			return fail(400, {
				message: 'This link has expired or was already used. Ask for a new one.'
			});
		}
		// The account's sessions have all ended, this browser's among them unless
		// it is in a demo of its own.
		if (!event.locals.user?.isAnonymous) {
			await auth.api.signOut({ headers: event.request.headers }).catch(() => {});
		}
		redirect(303, '/login?reset=1');
	}
};
