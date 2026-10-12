import { cubicInOut } from 'svelte/easing';
import { slide, type TransitionConfig } from 'svelte/transition';
import { tick } from 'svelte';

// === Transitions =======================================================================

/**
 * The Svelte transitions the app uses; its CSS animations are in `motion.css`.
 */

// Set around a view swapping its content wholesale (switching sessions, say), so
// what leaves disappears at once instead of animating out: a switch should feel
// immediate, not like a change within the same content.
let skipTransitions = false;

export function setSkipTransitions(value: boolean) {
	skipTransitions = value;
}

function reducedMotion(): boolean {
	return (
		skipTransitions ||
		(typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
	);
}

function duration(node: Element, token: string, fallback: number): number {
	const value = parseFloat(getComputedStyle(node).getPropertyValue(token));
	return Number.isFinite(value) ? value : fallback;
}

/** Something opening in place: it slides open and fades in. */
export function reveal(node: HTMLElement): TransitionConfig {
	if (reducedMotion()) {
		return { duration: 0 };
	}
	const reveal = slide(node, {
		duration: duration(node, '--motion-reveal', 320),
		easing: cubicInOut
	});
	return {
		...reveal,
		css: (t, u) => `${reveal.css?.(t, u) ?? ''}; opacity: ${t};`
	};
}

/**
 * Something new arriving in the conversation, such as a proposal card: its space
 * opens first, then it fades in slowly over the open space, so it settles in
 * rather than popping into view.
 */
export function appear(node: HTMLElement): TransitionConfig {
	if (reducedMotion()) {
		return { duration: 0 };
	}
	const open = slide(node, { duration: 1 });
	return {
		duration: duration(node, '--motion-appear', 900),
		css: (t) => {
			const opened = cubicInOut(Math.min(1, t / 0.35));
			const shown = cubicInOut(Math.max(0, (t - 0.25) / 0.75));
			return `${open.css?.(opened, 1 - opened) ?? ''}; opacity: ${shown};`;
		}
	};
}

/** Something taking another's place: it rises into view. */
export const replace: (node: Element) => TransitionConfig = (node) => ({
	duration: reducedMotion() ? 0 : duration(node, '--motion-replace', 240),
	easing: cubicInOut,
	css: (t) => `opacity: ${t}; transform: translateY(${(1 - t) * 8}px);`
});

// === Flight ============================================================================

/**
 * A sent message's flight from where it was typed into its place in the thread:
 * a copy travels on a spring while the real one waits, hidden, at its destination.
 * The scroll container it lands in marks itself with `data-flight-viewport`.
 */
export interface FlightOrigin {
	left: number;
	top: number;
	fontSize: number;
}

/** Read before clearing the input (or removing a queued message). */
export function captureFlightOrigin(
	node: HTMLElement | null | undefined
): FlightOrigin | undefined {
	if (!node || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
	const rect = node.getBoundingClientRect();
	const style = getComputedStyle(node);
	return {
		left: rect.left + parseFloat(style.paddingLeft),
		top: rect.top + parseFloat(style.paddingTop),
		fontSize: parseFloat(style.fontSize)
	};
}

// Exact critically damped spring step: independent of frame rate, with continuous
// velocity even when a streaming response changes the destination mid-flight.
function advance(position: number, velocity: number, target: number, dt: number) {
	const frequency = 20;
	const offset = position - target;
	const spring = velocity + frequency * offset;
	const decay = Math.exp(-frequency * dt);
	return {
		position: target + (offset + spring * dt) * decay,
		velocity: (velocity - frequency * spring * dt) * decay
	};
}

/** One visual bubble, outside the scroll clip, tracking its live layout destination. */
export function flight(node: HTMLElement, origin: FlightOrigin | undefined) {
	const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
	if (!origin || reducedMotion.matches) return;

	let disposed = false;
	let frame = 0;
	let copy: HTMLElement | undefined;
	const originalVisibility = node.style.visibility;
	const originalTransition = node.style.transition;
	// transition-all also transitions visibility: without this both bubbles appear for 150ms.
	node.style.transition = 'none';
	node.style.visibility = 'hidden';

	function finish() {
		if (disposed) return;
		disposed = true;
		cancelAnimationFrame(frame);
		copy?.remove();
		node.style.visibility = originalVisibility;
		node.style.transition = originalTransition;
		window.removeEventListener('resize', finish);
		viewport?.removeEventListener('wheel', finish);
		viewport?.removeEventListener('touchstart', finish);
		viewport?.removeEventListener('keydown', finish);
		reducedMotion.removeEventListener('change', finish);
	}

	const viewport = node.closest('[data-flight-viewport]');
	window.addEventListener('resize', finish, { once: true });
	viewport?.addEventListener('wheel', finish, { once: true, passive: true });
	viewport?.addEventListener('touchstart', finish, { once: true, passive: true });
	viewport?.addEventListener('keydown', finish, { once: true });
	reducedMotion.addEventListener('change', finish, { once: true });

	void tick().then(() => {
		if (disposed) return;
		frame = requestAnimationFrame(() => {
			if (disposed) return;
			const target = node.getBoundingClientRect();
			if (!target.width || !target.height) return finish();
			const text = node.querySelector('p') ?? node;
			const textRect = text.getBoundingClientRect();
			const style = getComputedStyle(node);
			// Keep the final width and horizontal position throughout: only travel vertically.
			let y = origin.top - (textRect.top - target.top);
			let vy = 0;
			const started = performance.now();
			let previous = started;

			copy = node.cloneNode(true) as HTMLElement;
			copy.setAttribute('aria-hidden', 'true');
			Object.assign(copy.style, {
				position: 'fixed',
				left: `${target.left}px`,
				top: '0',
				width: `${target.width}px`,
				height: `${target.height}px`,
				maxWidth: 'none',
				margin: '0',
				font: style.font,
				visibility: 'visible',
				pointerEvents: 'none',
				zIndex: '50',
				transformOrigin: 'top left',
				transition: 'none',
				willChange: 'transform'
			});
			document.body.append(copy);

			function animate(now: number) {
				if (disposed || !copy) return;
				const destination = node.getBoundingClientRect();
				// Don't hand off a stale-sized copy after a responsive layout change.
				if (
					Math.abs(destination.width - target.width) > 1 ||
					Math.abs(destination.left - target.left) > 1
				)
					return finish();
				const dt = Math.max(0, (now - previous) / 1000);
				previous = now;
				const vertical = advance(y, vy, destination.top, dt);
				y = vertical.position;
				vy = vertical.velocity;
				const elapsed = (now - started) / 1000;
				copy.style.transform = `translate3d(0, ${y}px, 0)`;

				const settled = Math.abs(y - destination.top) < 0.5 && Math.abs(vy) < 8;
				if ((elapsed > 0.25 && settled) || elapsed > 1.2) return finish();
				frame = requestAnimationFrame(animate);
			}
			animate(started);
		});
	});

	return { destroy: finish };
}
