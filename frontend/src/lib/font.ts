import { get } from 'svelte/store';
import { port } from '$lib/stores/sidecar';

type AccentKey = 'red' | 'yellow' | 'green' | 'cyan';

const ACCENT_MAP: Record<AccentKey, { dark: [string, string]; light: [string, string] }> = {
	red: { dark: ['#ff757f', '#db4b4b'], light: ['#9e1e52', '#77173e'] },
	yellow: { dark: ['#e0af68', '#c99a4b'], light: ['#7d5512', '#5e400e'] },
	green: { dark: ['#9ece6a', '#73a85a'], light: ['#3d5427', '#2e3f1d'] },
	cyan: { dark: ['#7dcfff', '#5aafc5'], light: ['#0f4b6e', '#08364f'] },
};

async function loadCustomFont(fontPath: string): Promise<string | null> {
	const p = get(port);
	if (p === null) return null;
	const res = await fetch(
		`http://localhost:${p}/api/v1/system/font?path=${encodeURIComponent(fontPath)}`
	);
	if (!res.ok) return null;
	const { data, mime } = (await res.json()) as { data: string; mime: string };
	const dataUrl = `data:${mime};base64,${data}`;
	const old = document.getElementById('__custom_font__');
	if (old) old.remove();
	const style = document.createElement('style');
	style.id = '__custom_font__';
	style.textContent = `@font-face { font-family: '__UserFont__'; src: url('${dataUrl}') format('truetype'); }`;
	document.head.appendChild(style);
	return "'__UserFont__', monospace";
}

export async function applyFont(
	variant: 'regular' | 'nerd' | 'custom',
	customPath: string | null,
	_uiFontSize: number = 16.0,
	readingFontSize: number = 17.0,
	theme: 'dark' | 'light' = 'dark',
	accentColor: AccentKey = 'yellow'
): Promise<void> {
	const root = document.documentElement;

	root.classList.toggle('theme-light', theme === 'light');

	root.style.setProperty('--font-text-size', `${readingFontSize}px`);

	const [accent, accentDark] = ACCENT_MAP[accentColor][theme];
	root.style.setProperty('--accent', accent);
	root.style.setProperty('--accent-dark', accentDark);

	if (variant === 'custom' && customPath) {
		const family = await loadCustomFont(customPath);
		if (family) {
			root.style.setProperty('--font-family', family);
			return;
		}
	}
	const family =
		variant === 'nerd'
			? "'Inconsolata NF', 'JetBrains Mono', monospace"
			: "'JetBrains Mono', monospace";
	root.style.setProperty('--font-family', family);
}
