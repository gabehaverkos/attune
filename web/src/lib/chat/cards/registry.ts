/**
 * How each artifact type is shown, found the way the backend finds them: the
 * system's own in `system.ts`, and each interface's in its own `interface.ts`
 * (`lib/<key>/interface.ts`).
 */
import type { Component } from 'svelte';
import type { Artifact } from '#lib/chat/types.ts';
import { interfaceDescriptor, offeredInterfaces } from '#lib/shell/registry.ts';

export interface ArtifactViewProps {
	artifact: Artifact;
	pending: boolean;
	errorMessage: string;
	/** The other proposals in the same set, when the assistant made several at once. */
	siblings?: Artifact[];
	onUpdate: (payload: Record<string, unknown>) => void;
	onAccept: () => void;
	onDeny: (reason: string | null) => void;
	onRetry: () => void;
}

export interface ArtifactView {
	type: string;
	schemaVersion: number;
	Component: Component<ArtifactViewProps>;
	/** How it reads once applied, or in the history of an earlier message. */
	Snapshot?: Component<{ artifact: Artifact }>;
	/** The interface it acts on, and the link that opens it there once applied. */
	opens?: { interface: string; link: string };
	/** It turns interfaces on or off, so once applied the shell reloads which are on. */
	changesInterfaces?: boolean;
}

const modules = import.meta.glob<{ ARTIFACTS: readonly ArtifactView[] }>(
	['./system.ts', '/src/lib/*/interface.ts'],
	{ eager: true }
);
/** Read when asked for, as the interfaces are (`shell/registry.ts`), and for the same reason. */
function views(): ArtifactView[] {
	return Object.values(modules).flatMap((module) => module.ARTIFACTS ?? []);
}

/**
 * The URLs whose data an artifact changes once applied: those of the interface it
 * acts on, or every interface's when it turns them on or off.
 */
export function artifactData(artifact: Artifact): readonly string[] {
	const view = artifactView(artifact);
	if (view?.changesInterfaces) return offeredInterfaces().flatMap((descriptor) => descriptor.data);
	const key = view?.opens?.interface;
	return (key && interfaceDescriptor(key)?.data) || [];
}

export function artifactView(artifact: Artifact): ArtifactView | undefined {
	return views().find(
		(view) => view.type === artifact.type && view.schemaVersion === artifact.schema_version
	);
}
