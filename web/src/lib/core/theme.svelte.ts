export type ThemePreference = 'system' | 'light' | 'dark';
const key = 'attune-theme';
export const theme = $state<{ preference: ThemePreference }>({ preference: 'system' });

function normalize(value: string | null): ThemePreference {
	return value === 'light' || value === 'dark' ? value : 'system';
}

let fading = false;

function apply() {
	// A change made mid-fade waits for it; the fade then heads to whatever the
	// preference is by then, so rapid toggling settles instead of jumping.
	if (fading) return;
	const root = document.documentElement;
	const dark =
		theme.preference === 'dark' ||
		(theme.preference === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
	if (root.classList.contains('dark') === dark) return;
	const swap = () => root.classList.toggle('dark', dark);
	if (
		!document.startViewTransition ||
		window.matchMedia('(prefers-reduced-motion: reduce)').matches
	) {
		swap();
		return;
	}
	// Cross-fade the whole page as one picture, so nothing snaps while its
	// neighbors fade; per-element color transitions are held off meanwhile.
	fading = true;
	root.classList.add('theme-switching');
	document.startViewTransition(swap).finished.finally(() => {
		root.classList.remove('theme-switching');
		fading = false;
		apply();
	});
}

export function setTheme(preference: ThemePreference) {
	theme.preference = preference;
	try {
		localStorage.setItem(key, preference);
	} catch {
		/* Storage may be blocked. */
	}
	apply();
}

/** Call once from the root layout; never read browser state during SSR. */
export function initializeTheme() {
	try {
		theme.preference = normalize(localStorage.getItem(key));
	} catch {
		/* Use device preference. */
	}
	apply();
	const media = window.matchMedia('(prefers-color-scheme: dark)');
	const sync = (event: StorageEvent) => {
		if (event.key === key || event.key === null) {
			theme.preference = normalize(event.newValue);
			apply();
		}
	};
	media.addEventListener('change', apply);
	window.addEventListener('storage', sync);
	return () => {
		media.removeEventListener('change', apply);
		window.removeEventListener('storage', sync);
	};
}
