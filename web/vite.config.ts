import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			adapter: adapter(),
			// What a page may load and run: its own files, and the inline scripts SvelteKit
			// stamps with each response's nonce (app.html's theme script carries it too).
			// Styles set on elements are everywhere (a block's place on the grid), and an
			// account's picture may come from Google.
			csp: {
				directives: {
					'default-src': ['self'],
					'script-src': ['self'],
					'style-src': ['self', 'unsafe-inline'],
					'style-src-attr': ['unsafe-inline'],
					'img-src': ['self', 'data:', 'https:'],
					'font-src': ['self', 'data:'],
					'connect-src': ['self'],
					'object-src': ['none'],
					'base-uri': ['self'],
					// Signing in with Google posts here and is sent on to Google.
					'form-action': ['self', 'https://accounts.google.com'],
					'frame-ancestors': ['none']
				}
			},
			preprocess: vitePreprocess(),
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			}
		})
	],
	server: {
		watch: {
			usePolling: process.env.CHOKIDAR_USEPOLLING === 'true',
			interval: 250
		},
		hmr: {
			protocol: process.env.VITE_HMR_PROTOCOL as 'ws' | 'wss' | undefined,
			clientPort: process.env.VITE_HMR_CLIENT_PORT
				? Number(process.env.VITE_HMR_CLIENT_PORT)
				: undefined
		}
	}
});
