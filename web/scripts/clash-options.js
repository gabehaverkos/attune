// Answers the backend's clash-options test (backend/tests/test_promises.py) with
// what the browser's rule makes of the same cases: reads a JSON list of
// clashOptions argument lists on stdin, and writes the list of their results.
import { text } from 'node:stream/consumers';
import { createServer } from 'vite';

const cases = JSON.parse(await text(process.stdin));
const vite = await createServer({
	server: { middlewareMode: true, hmr: false, ws: false },
	appType: 'custom',
	logLevel: 'silent'
});
try {
	const { clashOptions } = await vite.ssrLoadModule('/src/lib/calendar/calendar.ts');
	process.stdout.write(JSON.stringify(cases.map((args) => clashOptions(...args))));
} finally {
	await vite.close();
}
