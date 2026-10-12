import { agreedToTerms } from '#lib/legal/terms.ts';
import { demoKind, startDemo } from '#lib/server/accounts.ts';
import { redirect, type RequestHandler } from '@sveltejs/kit';

/** The landing page's demo choice, once the terms are agreed to. */
export const POST: RequestHandler = async (event) => {
	const form = await event.request.formData();
	if (!agreedToTerms(form)) redirect(303, '/welcome');
	return startDemo(event, demoKind(form.get('kind')));
};
