import { deleteAccount } from '#lib/server/accounts.ts';
import type { RequestHandler } from '@sveltejs/kit';

/** The account menu's "Delete account", once it has been confirmed there. */
export const POST: RequestHandler = (event) => deleteAccount(event);
