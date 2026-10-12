import { error } from '@sveltejs/kit';
import type { ChatThreadPage } from '#lib/chat/types.ts';
import { cachedFetch } from '#lib/core/api.ts';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ depends, fetch, parent, url }) => {
	// A link from memory asks for its session again even at the address already open.
	depends('chat:thread');
	try {
		const { timezone } = await parent();
		// A session named in the URL wins over the backend's own choice of active
		// session, so a link into a past session lands on it. The query form matches
		// the one the page itself uses, keeping both on the same cache entry.
		const requested = url.searchParams.get('thread');
		const path = requested
			? `/api/chat/thread?thread_id=${encodeURIComponent(requested)}`
			: '/api/chat/thread';
		const chatData = await cachedFetch<ChatThreadPage>(path, fetch, {
			headers: { 'X-User-Timezone': timezone }
		});
		return {
			timezone,
			// A message named in the URL is where a memory source wants the session
			// opened. It never changes which session is loaded, only where it lands.
			anchorMessageId: url.searchParams.get('message'),
			// The page hydrates the cache under the key this load actually read, so a
			// session opened by link never overwrites the default session's entry.
			cacheUrl: path,
			threads: chatData.threads,
			activeThreadId: chatData.active_thread_id,
			thread: chatData.thread,
			artifacts: chatData.artifacts,
			activeRun: chatData.active_run ?? null
		};
	} catch (err) {
		throw error(500, err instanceof Error ? err.message : 'Unable to load chat thread.');
	}
};
