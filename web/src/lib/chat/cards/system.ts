/**
 * The system's own artifact types, as chat shows them: turning an interface on or
 * off, and forgetting everything Attune knows.
 */
import type { ArtifactView } from '#lib/chat/cards/registry.ts';
import InterfaceToggleArtifact from '#lib/chat/cards/InterfaceToggleArtifact.svelte';
import InterfaceToggleSnapshot from '#lib/chat/cards/InterfaceToggleSnapshot.svelte';
import MemoryWipeArtifact from '#lib/chat/cards/MemoryWipeArtifact.svelte';

export const ARTIFACTS: readonly ArtifactView[] = [
	{
		type: 'interface_toggle',
		schemaVersion: 1,
		Component: InterfaceToggleArtifact,
		Snapshot: InterfaceToggleSnapshot,
		changesInterfaces: true
	},
	{
		type: 'memory_wipe',
		schemaVersion: 1,
		Component: MemoryWipeArtifact
	}
];
