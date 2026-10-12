<script lang="ts">
	import { onMount, untrack } from 'svelte';

	interface MessageTextSegment {
		text: string;
		bold: boolean;
	}

	/** Parse the assistant's supported inline emphasis without injecting HTML. */
	function messageTextSegments(content: string): MessageTextSegment[] {
		return content
			.trimStart()
			.split('**')
			.map((text, index) => ({ text, bold: index % 2 === 1 }))
			.filter((segment) => segment.text.length > 0);
	}

	let {
		content,
		streaming = true,
		live = undefined,
		ondone
	}: {
		content: string;
		/** More text may still arrive. */
		streaming?: boolean;
		/** Fade each word in as it arrives; by default, whether it was streaming when shown. */
		live?: boolean;
		/** Every word is on screen and no more will come. */
		ondone?: () => void;
	} = $props();
	// Decide once: historical replies render whole; live ones show each word as soon
	// as it has fully arrived, fading in, with no pacing.
	let fading = $state(false);
	const words = $derived(content.trimStart().match(/\S+\s*/g) ?? []);
	// Hold a partial final word until the next chunk (or completion).
	const available = $derived(words.length - (streaming && /\S$/.test(content) ? 1 : 0));
	const displayed = $derived(fading ? words.slice(0, available).join('') : content);
	const segments = $derived(messageTextSegments(displayed));

	onMount(() => {
		fading = live ?? streaming;
	});

	$effect(() => {
		if (!streaming) untrack(() => ondone?.());
	});

	function tokens(value: string): string[] {
		return value.match(/\S+\s*|\s+/g) ?? [];
	}
</script>

<p class="whitespace-pre-wrap text-foreground/95" aria-live="polite">
	{#each segments as segment, segmentIndex (segmentIndex)}
		{#if !fading}
			{#if segment.bold}<strong>{segment.text}</strong>{:else}{segment.text}{/if}
		{:else if segment.bold}<strong
				>{#each tokens(segment.text) as token, tokenIndex (tokenIndex)}<span
						class="relative animate-stream-token">{token}</span
					>{/each}</strong
			>{:else}{#each tokens(segment.text) as token, tokenIndex (tokenIndex)}<span
					class="relative animate-stream-token">{token}</span
				>{/each}{/if}
	{/each}
</p>
