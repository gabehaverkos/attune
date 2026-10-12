<script lang="ts">
	import Button from '#lib/core/ui/Button.svelte';

	import { apiRequest } from '#lib/core/api.ts';
	import {
		evidenceLine,
		nodesById,
		type MemoryNode,
		type MemoryPanelData
	} from '#lib/memory/types.ts';
	import { ArrowUpRight, ChevronRight, PenLine, Trash2 } from '@lucide/svelte';

	let {
		panel,
		openBeliefId = $bindable(null),
		onPanel,
		onSelectSource
	}: {
		panel: MemoryPanelData;
		/** A node to show, from Activity: the outline opens down to it. */
		openBeliefId?: string | null;
		onPanel: (next: MemoryPanelData) => void;
		onSelectSource?: (threadId: string, messageId: string | null) => void;
	} = $props();

	/** Evidence shown before "and N more". */
	const EVIDENCE_SHOWN = 3;

	const inputId = $props.id();
	const nodes = $derived(nodesById(panel));
	// What is directly about the user, under the section each is filed in. Within
	// one, what holds the most comes first.
	const filed = $derived(
		panel.sections
			.map((section) => ({
				...section,
				ids: panel.top
					.filter((id) => nodes.get(id)?.section === section.key)
					.sort(
						(a, b) => (nodes.get(b)?.children.length ?? 0) - (nodes.get(a)?.children.length ?? 0)
					)
			}))
			.filter((section) => section.ids.length)
	);
	// A node can sit under several others, so what is open is a path, not a node.
	let expanded = $state<Record<string, boolean>>({});
	let editingKey = $state<string | null>(null);
	let confirmingKey = $state<string | null>(null);
	let draft = $state('');
	let busy = $state(false);
	let errorMessage = $state('');

	// Activity can show a node; open the outline down to it along its first parents.
	$effect(() => {
		if (!openBeliefId) return;
		const chain: string[] = [];
		let current: MemoryNode | undefined = nodes.get(openBeliefId);
		while (current && !chain.includes(current.id)) {
			chain.unshift(current.id);
			current = current.parents.length ? nodes.get(current.parents[0]) : undefined;
		}
		for (let index = 1; index <= chain.length; index++) {
			expanded[chain.slice(0, index).join('/')] = true;
		}
		openBeliefId = null;
	});

	function toggle(key: string) {
		expanded[key] = !expanded[key];
		editingKey = null;
		confirmingKey = null;
		errorMessage = '';
	}

	async function act(run: () => Promise<void>, failure: string) {
		if (busy) return;
		busy = true;
		errorMessage = '';
		try {
			await run();
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : failure;
		} finally {
			busy = false;
		}
	}

	function startCorrecting(key: string, node: MemoryNode) {
		editingKey = key;
		confirmingKey = null;
		// Already said to the user ("Your…", "You…") by the panel.
		draft = node.text;
	}

	function saveCorrection(node: MemoryNode) {
		const text = draft.trim();
		if (!text) return;
		void act(async () => {
			onPanel(
				await apiRequest<MemoryPanelData>(
					`/api/memory/nodes/${encodeURIComponent(node.id)}/correct`,
					{ method: 'POST', body: { text } }
				)
			);
			editingKey = null;
		}, 'Unable to correct that.');
	}

	function remove(node: MemoryNode) {
		void act(async () => {
			onPanel(
				await apiRequest<MemoryPanelData>(
					`/api/memory/nodes/${encodeURIComponent(node.id)}/remove`,
					{ method: 'POST' }
				)
			);
			confirmingKey = null;
		}, 'Unable to remove that.');
	}
</script>

{#snippet nodeRow(id: string, path: string[])}
	{@const node = nodes.get(id)}
	{#if node}
		{@const key = [...path, id].join('/')}
		{@const open = expanded[key] ?? false}
		{@const others = node.parents.filter((parent) => parent !== path[path.length - 1])}
		<li class="flex flex-col">
			<button
				type="button"
				aria-expanded={open}
				onclick={() => toggle(key)}
				class={[
					'flex min-h-10 w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-muted',
					open && 'bg-muted'
				]}
			>
				<span
					class={[
						'min-w-0 flex-1 text-body leading-snug',
						node.kind === 'thing' && 'font-medium',
						node.derived && 'text-muted-foreground'
					]}>{node.label}</span
				>
				{#if node.children.length}
					<span class="shrink-0 text-note text-muted-foreground">{node.children.length}</span>
				{/if}
				<ChevronRight
					class={[
						'size-3.5 shrink-0 text-muted-foreground transition-transform',
						open && 'rotate-90'
					]}
					aria-hidden="true"
				/>
			</button>

			{#if open}
				<div class="flex flex-col gap-2.5 px-2.5 pt-2 pb-3">
					{#if node.kind === 'thing'}
						<p class="text-sm text-foreground/80">{node.text}</p>
					{/if}
					{#if node.derived}
						<p class="text-sm text-muted-foreground">Attune's summary of what's below.</p>
					{/if}
					{#if node.evidence.length}
						<ul class="flex flex-row flex-wrap items-center gap-x-1.5 gap-y-1">
							{#each node.evidence.slice(0, EVIDENCE_SHOWN) as evidence, index (`${evidence.source_id}:${evidence.message_id}`)}
								<li
									class={evidence.thread_id && onSelectSource
										? [
												'flex items-center text-sm text-muted-foreground',
												index < Math.min(node.evidence.length, EVIDENCE_SHOWN) - 1 &&
													"after:content-[',']"
											]
										: 'w-full'}
								>
									{#if evidence.thread_id && onSelectSource}
										<button
											type="button"
											onclick={() =>
												onSelectSource(evidence.thread_id as string, evidence.message_id)}
											class="flex cursor-pointer items-center gap-1 text-sm text-muted-foreground underline underline-offset-2 hover:text-foreground"
										>
											{evidenceLine(evidence)}
											<ArrowUpRight class="size-3.5" aria-hidden="true" />
										</button>
									{:else}
										<p class="text-sm text-muted-foreground">{evidenceLine(evidence)}</p>
										{#if evidence.text}
											<p class="text-sm text-foreground/80">“{evidence.text}”</p>
										{/if}
									{/if}
								</li>
							{/each}
							{#if node.evidence.length > EVIDENCE_SHOWN}
								<li class="text-sm text-muted-foreground">
									and {node.evidence.length - EVIDENCE_SHOWN} more
								</li>
							{/if}
						</ul>
					{/if}
					{#if others.length}
						<p class="text-sm text-muted-foreground">
							Also under {others.map((parent) => nodes.get(parent)?.label ?? '').join(', ')}
						</p>
					{/if}

					{#if editingKey === key}
						<div class="flex flex-col gap-2">
							<label for={`${inputId}-correct`} class="text-sm font-semibold"
								>What's actually true?</label
							>
							<textarea
								id={`${inputId}-correct`}
								bind:value={draft}
								rows="3"
								maxlength="500"
								class="w-full resize-none rounded-lg border border-border bg-background p-2.5 text-body"
							></textarea>
							<div class="flex gap-2">
								<Button
									type="button"
									disabled={busy || !draft.trim()}
									onclick={() => saveCorrection(node)}
									size="sm"
									class="px-3.5"
								>
									{busy ? 'Saving…' : 'Save'}
								</Button>
								<button
									type="button"
									onclick={() => (editingKey = null)}
									class="h-9 cursor-pointer rounded-lg border border-border px-3 text-sm hover:bg-muted"
								>
									Cancel
								</button>
							</div>
						</div>
					{:else if confirmingKey === key}
						<div class="flex flex-col gap-2.5">
							<p class="text-body leading-snug">Forget this?</p>
							<div class="flex gap-2">
								<Button
									type="button"
									disabled={busy}
									onclick={() => remove(node)}
									variant="destructive"
									size="sm"
									class="px-3.5"
								>
									{busy ? 'Forgetting…' : 'Forget'}
								</Button>
								<button
									type="button"
									onclick={() => (confirmingKey = null)}
									class="h-9 cursor-pointer rounded-lg border border-border px-3 text-sm hover:bg-muted"
								>
									Keep it
								</button>
							</div>
						</div>
					{:else}
						<div class="flex gap-2">
							<button
								type="button"
								onclick={() => startCorrecting(key, node)}
								class="flex h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-border px-3 text-sm hover:bg-muted"
							>
								<PenLine class="size-3.5" aria-hidden="true" />Correct
							</button>
							<button
								type="button"
								onclick={() => {
									confirmingKey = key;
									editingKey = null;
								}}
								class="flex h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-border px-3 text-sm hover:bg-muted"
							>
								<Trash2 class="size-3.5" aria-hidden="true" />Forget
							</button>
						</div>
					{/if}
				</div>
				{#if node.children.length}
					<ul
						aria-label={`Under ${node.label}`}
						class="ml-2.5 flex flex-col gap-0.5 border-l border-border/70 pb-1 pl-2"
					>
						{#each node.children as child (child)}
							{#if !path.includes(child)}
								{@render nodeRow(child, [...path, id])}
							{/if}
						{/each}
					</ul>
				{/if}
			{/if}
		</li>
	{/if}
{/snippet}

<div class="flex flex-col gap-3.5">
	{#if panel.pending.length}
		<ul aria-label="Still being learned" class="flex flex-col gap-1">
			{#each panel.pending as item (item.source_id)}
				<li class="flex items-baseline gap-2 px-2.5 text-body text-muted-foreground">
					<span class="min-w-0 flex-1">{item.text}</span>
					<span class="shrink-0 text-note"
						>{item.status === 'failed' ? "Couldn't learn yet" : 'Learning…'}</span
					>
				</li>
			{/each}
		</ul>
	{/if}

	{#if panel.top.length}
		<div class="flex flex-col gap-4">
			{#each filed as section (section.key)}
				<section class="flex flex-col gap-1">
					<!-- Nothing filed yet is one list, and "Other" alone would say nothing. -->
					{#if filed.length > 1 || section.key !== 'other'}
						<h3 class="px-2.5 text-note font-medium tracking-wide text-muted-foreground uppercase">
							{section.label}
						</h3>
					{/if}
					<ul aria-label={section.label} class="flex flex-col gap-0.5">
						{#each section.ids as id (id)}
							{@render nodeRow(id, [])}
						{/each}
					</ul>
				</section>
			{/each}
		</div>
	{:else if !panel.pending.length}
		<p class="text-body text-muted-foreground">
			Nothing yet. What you tell Attune about yourself will show up here.
		</p>
	{/if}

	{#if errorMessage}
		<p class="text-body text-destructive" role="alert">{errorMessage}</p>
	{/if}
</div>
