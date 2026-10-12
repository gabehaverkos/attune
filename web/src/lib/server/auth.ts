import pg from 'pg';
import {
	BETTER_AUTH_SECRET,
	BETTER_AUTH_URL,
	DATABASE_URL,
	GOOGLE_CLIENT_ID,
	GOOGLE_CLIENT_SECRET,
	RESEND_API_KEY
} from '$app/env/private';
import { betterAuth } from 'better-auth';
import { anonymous } from 'better-auth/plugins';
import { sveltekitCookies } from 'better-auth/svelte-kit';
import { getRequestEvent } from '$app/server';
import { building } from '$app/env';
import { TERMS_VERSION } from '#lib/legal/terms.ts';

// === The database ======================================================================

const connectionString = DATABASE_URL;

const dbPool = new pg.Pool({
	connectionString
});

// === Mail ==============================================================================

/** Who Attune's emails come from: an address on the domain verified in Resend. */
const MAIL_FROM = 'Attune <verify@attune.haverkos.dev>';

/** One of Attune's emails: a sentence, and the link it asks the reader to open. */
type LinkMail = { subject: string; lead: string; action: string };

const VERIFICATION: LinkMail = {
	subject: 'Verify your email for Attune',
	lead: 'Open this link to verify your email and finish setting up your Attune account:',
	action: 'Verify my email'
};
const PASSWORD_RESET: LinkMail = {
	subject: 'Reset your Attune password',
	lead: 'Open this link to choose a new password for your Attune account. It works for an hour:',
	action: 'Choose a new password'
};

/**
 * Sends a link through Resend: to verify an address, or to reset a password.
 *
 * Without RESEND_API_KEY (development), the link is logged instead, so it can be
 * opened from the web container's output.
 */
async function sendLink(to: string, url: string, mail: LinkMail): Promise<void> {
	if (!RESEND_API_KEY) {
		console.info(`[mail] ${mail.action} for ${to}: ${url}`);
		return;
	}
	const response = await fetch('https://api.resend.com/emails', {
		method: 'POST',
		headers: {
			authorization: `Bearer ${RESEND_API_KEY}`,
			'content-type': 'application/json'
		},
		body: JSON.stringify({
			from: MAIL_FROM,
			to,
			subject: mail.subject,
			text: `${mail.lead}\n\n${url}\n\nIf you didn't ask for this, you can ignore this email.`,
			html: `<p>${mail.lead}</p><p><a href="${url}">${mail.action}</a></p><p>If you didn't ask for this, you can ignore this email.</p>`
		})
	});
	if (!response.ok) {
		throw new Error(`Resend refused the email (${response.status}): ${await response.text()}`);
	}
}

// === Sign-in ===========================================================================

/** A guest's placeholder address, until they keep the account with a real one. */
const GUEST_EMAIL_DOMAIN = 'guest.attune.invalid';

export function isGuestEmail(email: string) {
	return email.endsWith(`@${GUEST_EMAIL_DOMAIN}`);
}

/** The one place outside Attune a redirect may lead: Google's sign-in. */
export const GOOGLE_SIGN_IN = ['https://accounts.google.com'];

/** The email and whether Google vouches for it, from the ID token Google just issued. */
function googleIdentity(idToken: string): { email?: string; email_verified?: boolean } {
	try {
		return JSON.parse(Buffer.from(idToken.split('.')[1], 'base64url').toString('utf8'));
	} catch {
		return {};
	}
}

// The build imports this module without the runtime environment. At runtime a
// missing secret has to stop the server, not sign sessions with a known key.
if (!building && !BETTER_AUTH_SECRET) {
	throw new Error('Set BETTER_AUTH_SECRET to a long random value.');
}

export const auth = betterAuth({
	database: dbPool,
	baseURL: BETTER_AUTH_URL || 'http://localhost:5173',
	// Only the build sees the placeholder; it never signs a session.
	secret: building ? 'build-only-placeholder-for-module-analysis' : BETTER_AUTH_SECRET,
	// The backend's migrations make these tables, and may still be running when
	// this starts. Better Auth would check once, find none, and refuse from then on.
	advanced: { database: { validateSchema: false } },
	emailAndPassword: {
		enabled: true,
		requireEmailVerification: true,
		sendResetPassword: async ({ user, url }) => sendLink(user.email, url, PASSWORD_RESET),
		// Whoever knew the old password is signed out everywhere.
		revokeSessionsOnPasswordReset: true
	},
	emailVerification: {
		sendOnSignUp: true,
		sendOnSignIn: true,
		autoSignInAfterVerification: true,
		sendVerificationEmail: async ({ user, url }) => sendLink(user.email, url, VERIFICATION),
		// A guest who verified the email they gave has kept the account.
		afterEmailVerification: async (user) => {
			if ((user as { isAnonymous?: boolean }).isAnonymous) {
				await (await auth.$context).internalAdapter.updateUser(user.id, { isAnonymous: false });
			}
		}
	},
	databaseHooks: {
		user: {
			create: {
				// Every way of opening an account passes through agreeing to the terms
				// first: the checkbox for a demo or a sign-up, or the notice beside
				// Google sign-in, which opens an account for someone new.
				before: async (user) => ({
					data: { ...user, termsVersion: TERMS_VERSION, termsAcceptedAt: new Date() }
				})
			}
		},
		account: {
			create: {
				// A guest who linked Google has kept the account. Google's email
				// replaces the placeholder unless another account already uses it.
				after: async (account) => {
					if (account.providerId !== 'google' || !account.idToken) return;
					const ctx = await auth.$context;
					const user = await ctx.internalAdapter.findUserById(account.userId);
					if (!(user as { isAnonymous?: boolean } | null)?.isAnonymous) return;
					const { email, email_verified } = googleIdentity(account.idToken);
					const taken = email ? await ctx.internalAdapter.findUserByEmail(email) : null;
					await ctx.internalAdapter.updateUser(
						account.userId,
						email && email_verified && !taken
							? { isAnonymous: false, email: email.toLowerCase(), emailVerified: true }
							: { isAnonymous: false }
					);
				}
			}
		}
	},
	socialProviders:
		GOOGLE_CLIENT_ID && GOOGLE_CLIENT_SECRET
			? {
					google: {
						clientId: GOOGLE_CLIENT_ID,
						clientSecret: GOOGLE_CLIENT_SECRET,
						// Better Auth supplies the identity scopes: openid, email, profile.
						accessType: 'online',
						mapProfileToUser: (profile) => ({
							name: profile.given_name || profile.name
						})
					}
				}
			: {},
	plugins: [
		anonymous({
			emailDomainName: GUEST_EMAIL_DOMAIN,
			generateName: () => 'Guest',
			// A guest who signs in to another account leaves this one to expire.
			disableDeleteAnonymousUser: true,
			schema: { user: { fields: { isAnonymous: 'is_anonymous' } } }
		}),
		sveltekitCookies(getRequestEvent)
	],
	// Map Better Auth's default table and column names to match snake_case standard
	user: {
		modelName: 'auth_users',
		// A guest gives a real address this way: it replaces the placeholder only
		// once the link sent to it is opened, so no one can hold an address unverified.
		changeEmail: { enabled: true },
		additionalFields: {
			termsVersion: { type: 'string', required: false, input: false, fieldName: 'terms_version' },
			termsAcceptedAt: {
				type: 'date',
				required: false,
				input: false,
				fieldName: 'terms_accepted_at'
			}
		},
		fields: {
			emailVerified: 'email_verified',
			createdAt: 'created_at',
			updatedAt: 'updated_at'
		}
	},
	session: {
		modelName: 'auth_sessions',
		fields: {
			userId: 'user_id',
			expiresAt: 'expires_at',
			ipAddress: 'ip_address',
			userAgent: 'user_agent',
			createdAt: 'created_at',
			updatedAt: 'updated_at'
		}
	},
	account: {
		modelName: 'auth_accounts',
		accountLinking: {
			enabled: true,
			allowDifferentEmails: true
		},
		fields: {
			userId: 'user_id',
			accountId: 'account_id',
			providerId: 'provider_id',
			accessToken: 'access_token',
			refreshToken: 'refresh_token',
			idToken: 'id_token',
			accessTokenExpiresAt: 'access_token_expires_at',
			refreshTokenExpiresAt: 'refresh_token_expires_at',
			scope: 'scope',
			passwordExpired: 'password_expired',
			createdAt: 'created_at',
			updatedAt: 'updated_at'
		}
	},
	verification: {
		modelName: 'auth_verifications',
		fields: {
			expiresAt: 'expires_at',
			createdAt: 'created_at',
			updatedAt: 'updated_at'
		}
	}
});
