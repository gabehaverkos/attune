/**
 * Timestamps are timezone-aware in Postgres; a bare value is still read as UTC
 * so a naive one from another backend cannot silently shift by the offset.
 */
function parseTimestamp(value: string): Date {
	const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
	return new Date(normalized);
}

/** Mirrors app/memory/graph/recall/panel.py. */
type MemorySourceKind =
	| 'conversation'
	| 'statement'
	| 'onboarding'
	| 'correction'
	| 'review'
	| 'removal';

/** One time the user said what a node rests on. */
export type NodeEvidence = {
	source_id: string;
	kind: MemorySourceKind;
	said_at: string;
	/** Where to open the conversation; null for something typed. */
	thread_id: string | null;
	message_id: string | null;
	/** What they typed, for something typed. */
	text: string | null;
};

export type MemoryNode = {
	id: string;
	kind: 'thing' | 'belief';
	/** A belief's content, or a thing's name. */
	label: string;
	/** What a thing is; for a belief, the same as its label. */
	text: string;
	/** Written by memory to sum up what is below it. */
	derived: boolean;
	/** Where it is listed while it is directly about the user: a key of the panel's sections. */
	section: string;
	/** Empty when it is directly about the user. */
	parents: string[];
	children: string[];
	evidence: NodeEvidence[];
	learned_at: string;
};

type PendingStatement = {
	source_id: string;
	text: string;
	status: 'learning' | 'failed';
};

export type MemorySource = {
	id: string;
	kind: MemorySourceKind;
	status: 'learning' | 'waiting' | 'learned' | 'failed' | 'empty';
	created_at: string;
	statement: string | null;
	thread_id: string | null;
	error: string | null;
	/** Nodes resting on this source that are still there. */
	taught: string[];
	/** For a conversation: the user's messages in it not learned yet. */
	waiting: number;
	/** What it changed in memory, in order; `node_id` while that node is there. */
	changes: {
		label:
			| 'Added'
			| 'Also said'
			| 'Changed'
			| 'Took back'
			| 'Split out'
			| 'Merged into'
			| 'Linked'
			| 'Grouped under'
			| 'Forgot';
		text: string;
		node_id: string | null;
	}[];
};

export type MemoryPanelData = {
	working: boolean;
	failed: number;
	/** A tidy the user asked for is under way. */
	reviewing: boolean;
	/** How many more they may ask for today. */
	reviews_left: number;
	nodes: MemoryNode[];
	/** The nodes directly about the user, in order. */
	top: string[];
	/** What those are listed under, in order. */
	sections: { key: string; label: string }[];
	pending: PendingStatement[];
	sources: MemorySource[];
	observed_at: string;
};

const MOMENT = new Intl.DateTimeFormat(undefined, {
	month: 'short',
	day: 'numeric',
	hour: 'numeric',
	minute: '2-digit'
});

/** "Sep 18, 2:46 PM" in the viewer's own time. */
export function formatMoment(value: string | null): string {
	if (!value) return '';
	const date = parseTimestamp(value);
	return Number.isNaN(date.getTime()) ? '' : MOMENT.format(date);
}

const DAY = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });

/** Where a belief came from, in a few plain words: "Sep 28, 4:36 PM", or "You told Attune · Sep 24". */
export function evidenceLine(evidence: NodeEvidence): string {
	if (evidence.kind === 'conversation') return formatMoment(evidence.said_at);
	const date = parseTimestamp(evidence.said_at);
	const when = Number.isNaN(date.getTime()) ? '' : DAY.format(date);
	const where =
		evidence.kind === 'correction'
			? 'You corrected this'
			: evidence.kind === 'onboarding'
				? 'From setup'
				: 'You told Attune';
	return when ? `${where} · ${when}` : where;
}

const SOURCE_TITLES: Record<MemorySourceKind, string> = {
	conversation: 'Conversation',
	statement: 'Something you told Attune',
	onboarding: 'Setup introduction',
	correction: 'A correction you made',
	review: 'Attune tidied what it knows',
	removal: 'You had Attune forget something'
};

export function sourceTitle(source: MemorySource): string {
	return SOURCE_TITLES[source.kind];
}

/** Every node in the panel, by id. */
export function nodesById(panel: MemoryPanelData | null): Map<string, MemoryNode> {
	return new Map((panel?.nodes ?? []).map((node) => [node.id, node]));
}
