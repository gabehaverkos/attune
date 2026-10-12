import { endDemo } from '#lib/server/accounts.ts';
import { auth } from '#lib/server/auth.ts';
import { fail, redirect } from '@sveltejs/kit';
import type { Actions } from './$types';

export const actions: Actions = {
	default: async (event) => {
		if (!event.locals.session) {
			return fail(401);
		}

		await endDemo(event);

		try {
			// Sign out the current session; a demo's went with it
			await auth.api.signOut({
				headers: event.request.headers
			});
		} catch (e) {
			console.error('[AUTH LOGOUT ERROR]', e);
		}

		throw redirect(302, '/welcome');
	}
};
