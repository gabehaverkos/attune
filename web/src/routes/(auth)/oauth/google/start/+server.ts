import { agreedToTerms } from '#lib/legal/terms.ts';
import { auth, GOOGLE_SIGN_IN } from '#lib/server/auth.ts';
import { error, redirect, type RequestHandler } from '@sveltejs/kit';

export const POST: RequestHandler = async ({ request, url }) => {
	// Sign-up asks for the agreement with a checkbox; sign-in shows it as a notice.
	const form = await request.formData();
	if (form.get('from') === 'signup' && !agreedToTerms(form)) redirect(303, '/signup');
	const result = await auth.api.signInSocial({
		body: {
			provider: 'google',
			callbackURL: `${url.origin}/`,
			newUserCallbackURL: `${url.origin}/`,
			errorCallbackURL: `${url.origin}/login?oauth=error`,
			disableRedirect: true
		},
		headers: request.headers
	});
	if (!result.url) error(502, 'Unable to start Google sign-in.');
	redirect(303, result.url, { external: GOOGLE_SIGN_IN });
};
