import type { LayoutGrid } from '@lucide/svelte';
import { type Component, getContext, setContext } from 'svelte';
import type { Artifact } from '#lib/chat/types.ts';

// === What an interface is ==============================================================

/** An interface is a user-facing capability in Attune. */
export interface UserInterface {
	key: string;
	name: string;
	description: string;
	enabled: boolean;
}

export interface InterfacesPayload {
	interfaces: UserInterface[];
}

export function findInterface(
	payload: InterfacesPayload | null | undefined,
	key: string
): UserInterface | undefined {
	return payload?.interfaces.find((entry) => entry.key === key);
}

export function interfaceEnabled(
	payload: InterfacesPayload | null | undefined,
	key: string
): boolean {
	return findInterface(payload, key)?.enabled ?? false;
}

export function enabledInterfaces(payload: InterfacesPayload | null | undefined): UserInterface[] {
	return payload?.interfaces.filter((entry) => entry.enabled) ?? [];
}

// === What one declares =================================================================

/**
 * What an interface declares about itself on the web, the counterpart of the
 * backend's `app/interfaces/descriptor.py`.
 *
 * Each interface declares an `InterfaceDescriptor` in its own `interface.ts`.
 * `shell/registry.ts` lists the interfaces, and everything generic (the launcher, the
 * layout, settings, prefetching) reads from there.
 */

export type Icon = typeof LayoutGrid;

export interface InterfaceViewProps {
	onclose: () => void;
	/** The query parameters of the link that opened it, if a link did. */
	link: URLSearchParams | null;
	/** Bumped when chat changes its data while it is open, so it reloads. */
	revision: number;
}

export interface InterfaceDescriptor {
	key: string;
	icon: Icon;
	/** What its rail button shows in place of the icon, where something live says more. */
	Mark?: Component;
	/** Its main surface, opened from the launcher or a link. */
	View: Component<InterfaceViewProps>;
	/** How a link into the app opens it. The parameters are cleared once read. */
	link?: {
		params: readonly string[];
		opens: (params: URLSearchParams) => boolean;
	};
	/**
	 * The URLs its own data lives at: warmed while it is enabled, and marked stale
	 * when something acts on it (an applied artifact, a settings change).
	 */
	data: readonly string[];
}

// === Which are on ======================================================================

const INTERFACES_KEY = Symbol('interfaces');

type InterfacesReader = () => InterfacesPayload;

type InterfacesShell = {
	read: InterfacesReader;
	/** Which interfaces are on, after something turned one on or off. */
	update: (payload: InterfacesPayload) => void;
	/** Data the user just changed from chat, so a closed interface can say it has news. */
	changed: (urls: readonly string[]) => void;
};

const NONE: InterfacesPayload = { interfaces: [] };

/**
 * The shell publishes the user's enabled interfaces so components deep in the
 * tree — artifact cards, mainly — can tell whether a surface they link to is
 * still reachable, without threading a prop through every layer.
 */
export function setInterfacesContext(shell: InterfacesShell): void {
	setContext(INTERFACES_KEY, shell);
}

function shell(): InterfacesShell | undefined {
	return getContext<InterfacesShell | undefined>(INTERFACES_KEY);
}

export function useInterfaces(): InterfacesReader {
	return shell()?.read ?? (() => NONE);
}

export function useInterfaceEnabled(key: string): () => boolean {
	const read = useInterfaces();
	return () => interfaceEnabled(read(), key);
}

export function useInterfacesShell(): Omit<InterfacesShell, 'read'> {
	return shell() ?? { update: () => undefined, changed: () => undefined };
}

// === Turning one on or off =============================================================

/** Turning an interface on or off, as the backend fills it in when drafted. */
export interface InterfaceTogglePayload {
	key: string | null;
	enabled: boolean;
	name: string | null;
	description: string | null;
}

export function interfaceTogglePayload(artifact: Artifact): InterfaceTogglePayload | null {
	const value = artifact.approved_payload ?? artifact.draft_payload;
	return value && typeof value.enabled === 'boolean'
		? (value as unknown as InterfaceTogglePayload)
		: null;
}

/** What happens to it, in a line. */
export function interfaceToggleSummary(payload: InterfaceTogglePayload): string {
	return payload.enabled
		? 'Adds it to Attune. The assistant can use it from your next message.'
		: 'Hides it from Attune. What you made in it stays, and comes back if you turn it on again.';
}

// === Onboarding ========================================================================

export type OnboardingState = {
	/** The memory rail has been pointed out, once it had something in it. */
	memory_introduced: boolean;
};

type Walkthrough = {
	readonly state: OnboardingState | null;
	update: (state: OnboardingState) => void;
};

const KEY = Symbol('onboarding');
export function setOnboardingContext(value: Walkthrough) {
	return setContext(KEY, value);
}
export function getOnboardingContext() {
	return getContext<Walkthrough>(KEY);
}
