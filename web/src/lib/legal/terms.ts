/**
 * The Terms of Use and Privacy Notice, versioned together by the date they last
 * changed. Each new account records the version it agreed to.
 */
export const TERMS_VERSION = '2026-10-08';

/** The author's site: where the name in account settings leads, and where questions about Attune go. */
export const AUTHOR_URL = 'https://haverkos.dev';

/** The checkbox every way of opening an account submits. */
export const TERMS_FIELD = 'accept_terms';

export function agreedToTerms(form: FormData): boolean {
	return form.get(TERMS_FIELD) === 'on';
}

export type LegalDocument = 'terms' | 'privacy';

export const LEGAL_TITLES: Record<LegalDocument, string> = {
	terms: 'Terms of Use',
	privacy: 'Privacy Notice'
};

/** When the documents last changed, as the reader sees it. */
export const LEGAL_UPDATED = new Date(`${TERMS_VERSION}T12:00:00`).toLocaleDateString('en-US', {
	year: 'numeric',
	month: 'long',
	day: 'numeric'
});
