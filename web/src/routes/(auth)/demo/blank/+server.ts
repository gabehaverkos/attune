import { restartBlank } from '#lib/server/accounts.ts';
import { redirect, type RequestHandler } from '@sveltejs/kit';

/**
 * From the example person's account to a demo with nothing in it: the guest
 * leaves that account, which expires like any other left behind, and starts a
 * new one. Only a guest has a demo to leave.
 */
export const POST: RequestHandler = (event) => {
	if (!event.locals.user?.isAnonymous) redirect(303, '/');
	return restartBlank(event);
};
