<script lang="ts">
	import { untrack, type Snippet } from 'svelte';
	import StreamingMessageText from '#lib/chat/StreamingMessageText.svelte';
	import { appear, reveal } from '#styles/motion.ts';

	// A reply's text with its cards where they were made: each card sits after the
	// words written before the tool call that made it, and before the words after.
	// While a reply plays out, each card waits for the words before it, and the
	// words after it wait for the card.
	let {
		content,
		streaming,
		placements,
		cards
	}: {
		content: string;
		/** More text may still arrive. */
		streaming: boolean;
		/** The cards to show, and how far into the text each was made. */
		placements: { id: string; offset: number | null | undefined }[];
		cards?: Snippet<[string[]]>;
	} = $props();

	const live = untrack(() => streaming);
	// Which parts have played all their words. A part can finish while it is the
	// last one and only later have a card placed after it, so each is remembered.
	let finished = $state<boolean[]>([]);
	// The part playing now: everything before it has finished.
	const played = $derived.by(() => {
		const next = parts.findIndex((_, index) => !finished[index]);
		return next === -1 ? parts.length : next;
	});

	const parts = $derived.by(() => {
		const placed = placements.map(({ id, offset }) => ({
			id,
			point: Math.min(offset ?? content.length, content.length)
		}));
		const points = [...new Set(placed.map(({ point }) => point))].sort(
			(left, right) => left - right
		);
		const result: { text: string; ids: string[] }[] = [];
		let from = 0;
		for (const point of points) {
			const ids = placed.filter((card) => card.point === point).map((card) => card.id);
			result.push({ text: content.slice(from, point).trim(), ids });
			from = point;
		}
		result.push({ text: content.slice(from).trim(), ids: [] });
		return result;
	});
</script>

{#each parts as part, index (index)}
	{#if !live || index <= played}
		<!-- Rendered even when empty: with no words to play, it is done at once. -->
		<div class={['max-w-full text-xl leading-relaxed transition-all w-full', part.text && 'py-3']}>
			<StreamingMessageText
				content={part.text}
				streaming={streaming && index === parts.length - 1}
				{live}
				ondone={() => (finished[index] = true)}
			/>
		</div>
		{#if part.ids.length && cards && (!live || index < played)}
			<div class="my-4 w-full" in:appear|global out:reveal|global>
				{@render cards(part.ids)}
			</div>
		{/if}
	{/if}
{/each}
