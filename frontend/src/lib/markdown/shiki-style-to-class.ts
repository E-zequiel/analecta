import type { ShikiTransformer } from 'shiki/core';

// cyrb53: public-domain 53-bit hash.
// Identical to the one in @shikijs/transformers — same input always yields
// the same class name, making build-time (gen-shiki-css.mjs) and runtime
// (renderer.ts) instances interchangeable.
function cyrb53(str: string, seed = 0): number {
	let h1 = 0xdeadbeef ^ seed,
		h2 = 0x41c6ce57 ^ seed;
	for (let i = 0, ch: number; i < str.length; i++) {
		ch = str.charCodeAt(i);
		h1 = Math.imul(h1 ^ ch, 2654435761);
		h2 = Math.imul(h2 ^ ch, 1597334677);
	}
	h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507);
	h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
	h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507);
	h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
	return 4294967296 * (2097151 & h2) + (h1 >>> 0);
}

function styleToClass(style: string): string {
	return `__s_${cyrb53(style).toString(16)}`;
}

export function createStyleToClassTransformer(): ShikiTransformer {
	return {
		name: 'style-to-class',
		pre(t) {
			const style = t.properties.style;
			if (!style || typeof style !== 'string') return;
			delete t.properties.style;
			this.addClassToHast(t, styleToClass(style));
		},
		span(t) {
			const style = t.properties.style;
			if (!style || typeof style !== 'string') return;
			delete t.properties.style;
			const cls = styleToClass(style);
			const existing = t.properties.class;
			const existingStr = Array.isArray(existing)
				? existing.join(' ')
				: existing != null
					? String(existing)
					: '';
			t.properties.class = existingStr ? `${existingStr} ${cls}` : cls;
		},
	};
}
