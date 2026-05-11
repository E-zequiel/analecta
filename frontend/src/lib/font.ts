import { get } from 'svelte/store';
import { port } from '$lib/stores/sidecar';

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
	fontSize: number = 16.33
): Promise<void> {
	document.documentElement.style.setProperty('--font-size-base', `${fontSize}px`);

	if (variant === 'custom' && customPath) {
		const family = await loadCustomFont(customPath);
		if (family) {
			document.documentElement.style.setProperty('--font-family', family);
			return;
		}
	}
	const family =
		variant === 'nerd' ? "'JetBrains Mono NF', monospace" : "'JetBrains Mono', monospace";
	document.documentElement.style.setProperty('--font-family', family);
}
