import { auth } from '#lib/server/auth.ts';
import { allow } from '#lib/server/accounts.ts';
import { fail } from '@sveltejs/kit';
import type { Actions } from './$types';

/**
 * Sends a link to choose a new password. The answer is the same whether or not
 * the email has an account, so the form can't be used to find out who does.
 */
export const actions: Actions = {
	default: async (event) => {
		if (!allow(`forgot:${event.getClientAddress()}`, 5, 60 * 60_000)) {
			return fail(429, { message: 'Too many requests from here. Try again later.' });
		}
		const formData = await event.request.formData();
		const email = String(formData.get('email') ?? '')
			.trim()
			.toLowerCase();
		if (!email.includes('@') || email.length < 5) {
			return fail(400, { message: 'Please enter a valid email address.' });
		}
		try {
			await auth.api.requestPasswordReset({
				body: { email, redirectTo: '/reset' },
				headers: event.request.headers
			});
		} catch (e) {
			console.error('[AUTH RESET REQUEST ERROR]', e);
			return fail(500, { message: "The link couldn't be sent. Try again in a moment." });
		}
		return { sent: true };
	}
};
