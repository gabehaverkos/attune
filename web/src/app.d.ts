// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		// interface Error {}
		interface Locals {
			user: (typeof import('#lib/server/auth.ts').auth.$Infer.Session)['user'] | null;
			session: (typeof import('#lib/server/auth.ts').auth.$Infer.Session)['session'] | null;
		}
		// interface PageData {}
		// interface PageState {}
		// interface Platform {}
	}
}

export {};
