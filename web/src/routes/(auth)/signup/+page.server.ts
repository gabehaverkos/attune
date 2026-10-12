import { agreedToTerms } from '#lib/legal/terms.ts';
import { auth, isGuestEmail } from '#lib/server/auth.ts';
import { allow, keepWithEmail, startGuest } from '#lib/server/accounts.ts';
import { fail, redirect } from '@sveltejs/kit';
import type { Actions } from './$types';

/**
 * A new account opens straight away as a demo, with the email and password
 * attached. It becomes a full account once the link sent to the email is opened.
 */
export const actions: Actions = {
	default: async (event) => {
		if (!allow(`signup:${event.getClientAddress()}`, 5, 60 * 60_000)) {
			return fail(429, { message: 'Too many new accounts from here. Try again later.' });
		}
		const formData = await event.request.formData();
		const email = String(formData.get('email') ?? '')
			.trim()
			.toLowerCase();
		const password = String(formData.get('password') ?? '');

		if (!agreedToTerms(formData)) {
			return fail(400, { message: 'Agree to the Terms of Use and Privacy Notice to continue.' });
		}
		if (!email.includes('@') || email.length < 5 || isGuestEmail(email)) {
			return fail(400, { message: 'Please enter a valid email address.' });
		}
		if (password.length < 8 || password.length > 255) {
			return fail(400, { message: 'Password must be at least 8 characters long.' });
		}
		if (password !== formData.get('confirm_password')) {
			return fail(400, { message: "The passwords don't match." });
		}
		const ctx = await auth.$context;
		if (await ctx.internalAdapter.findUserByEmail(email)) {
			return fail(409, { message: 'That email already has an account. Sign in to it instead.' });
		}

		// A guest signing up keeps the demo they're in rather than starting another.
		const guest = event.locals.user?.isAnonymous
			? { userId: event.locals.user.id, headers: event.request.headers }
			: await startGuest(event.request);
		await keepWithEmail(guest.userId, guest.headers, event.cookies, email, password);
		redirect(303, '/');
	}
};
