import type MarkdownIt from 'markdown-it';

export type ResolveWikilinkTitle = (title: string) => number | null;

export interface WikilinkOptions {
	resolveTitle: ResolveWikilinkTitle;
}

interface WikilinkMeta {
	title: string;
	alias: string | null;
	entryId: number | null;
}

// Parses [[Title]] / [[Title|Alias]] the same way as the backend's
// _WIKILINK_RE (backlinks.py), but as a native markdown-it inline rule
// (char-by-char on state.src/state.pos) since markdown-it has no regex-rule
// escape hatch for inline tokens.
export default function wikilink(md: MarkdownIt, options: WikilinkOptions): void {
	const { resolveTitle } = options;

	md.inline.ruler.before('link', 'wikilink', (state, silent) => {
		const src = state.src;
		const start = state.pos;

		if (src.charCodeAt(start) !== 0x5b || src.charCodeAt(start + 1) !== 0x5b) {
			return false;
		}

		const closeIdx = src.indexOf(']]', start + 2);
		if (closeIdx === -1) return false;

		const inner = src.slice(start + 2, closeIdx);
		if (inner.length === 0 || inner.includes('[') || inner.includes(']')) return false;

		const pipeIdx = inner.indexOf('|');
		const title = (pipeIdx === -1 ? inner : inner.slice(0, pipeIdx)).trim();
		if (title.length === 0) return false;
		const alias = pipeIdx === -1 ? '' : inner.slice(pipeIdx + 1).trim();

		if (!silent) {
			const token = state.push('wikilink', '', 0);
			const meta: WikilinkMeta = {
				title,
				alias: alias || null,
				entryId: resolveTitle(title),
			};
			token.meta = meta;
		}

		state.pos = closeIdx + 2;
		return true;
	});

	md.renderer.rules['wikilink'] = (tokens, idx) => {
		const meta = tokens[idx].meta as WikilinkMeta;
		const label = md.utils.escapeHtml(meta.alias ?? meta.title);
		if (meta.entryId !== null) {
			return `<a class="wikilink" data-entry-id="${meta.entryId}">${label}</a>`;
		}
		return `<span class="wikilink-unresolved">${label}</span>`;
	};
}
