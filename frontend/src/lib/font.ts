type AccentKey = 'red' | 'yellow' | 'green' | 'cyan';

const ACCENT_MAP: Record<AccentKey, { dark: [string, string]; light: [string, string] }> = {
	red: { dark: ['#ff757f', '#db4b4b'], light: ['#b5202b', '#7f161e'] },
	yellow: { dark: ['#e0af68', '#c99a4b'], light: ['#785814', '#543e0e'] },
	green: { dark: ['#9ece6a', '#73a85a'], light: ['#2e693e', '#204a2b'] },
	cyan: { dark: ['#7dcfff', '#5aafc5'], light: ['#00619b', '#00446c'] },
};

export function applyFont(
	variant: 'regular' | 'bricolage',
	uiFontSize: number = 17.0,
	readingFontSize: number = 17.0,
	theme: 'dark' | 'light' = 'dark',
	accentColor: AccentKey = 'yellow'
): void {
	const root = document.documentElement;

	root.classList.toggle('theme-light', theme === 'light');

	root.style.setProperty('--font-ui-size', `${uiFontSize}px`);
	root.style.setProperty('--font-text-size', `${readingFontSize}px`);

	const [accent, accentDark] = ACCENT_MAP[accentColor][theme];
	root.style.setProperty('--accent', accent);
	root.style.setProperty('--accent-dark', accentDark);

	const family =
		variant === 'bricolage'
			? "'Bricolage Grotesque', 'Symbols Nerd Font', sans-serif"
			: "'JetBrains Mono', 'Symbols Nerd Font Mono', monospace";
	root.style.setProperty('--font-family', family);
}
