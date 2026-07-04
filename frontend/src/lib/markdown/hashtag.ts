import type MarkdownIt from 'markdown-it';

interface HashtagMeta {
	raw: string;
	normalized: string;
}

const HASHTAG_BODY_RE = /^[A-Za-z][A-Za-z0-9_]*/;

// Parses inline #hashtags the same way as the backend's _HASHTAG_RE
// (backlinks.py), but as a native markdown-it inline rule. Fenced blocks and
// inline-code spans are excluded for free by markdown-it's own tokenizer
// (their contents never reach the inline ruler), same as wikilink.ts.
export default function hashtag(md: MarkdownIt): void {
	md.inline.ruler.before('link', 'hashtag', (state, silent) => {
		const src = state.src;
		const start = state.pos;

		if (src.charCodeAt(start) !== 0x23 /* # */) return false;

		// Reject a '#' immediately preceded by a non-whitespace character
		// (mid-word, e.g. "foo#bar") — mirrors the backend's (?<!\S) lookbehind.
		const prevChar = start > 0 ? src[start - 1] : '';
		if (prevChar && !/\s/.test(prevChar)) return false;

		const match = HASHTAG_BODY_RE.exec(src.slice(start + 1));
		if (!match) return false;

		const raw = match[0];

		if (!silent) {
			const token = state.push('hashtag', '', 0);
			const meta: HashtagMeta = { raw, normalized: raw.toLowerCase() };
			token.meta = meta;
		}

		state.pos = start + 1 + raw.length;
		return true;
	});

	md.renderer.rules['hashtag'] = (tokens, idx) => {
		const meta = tokens[idx].meta as HashtagMeta;
		const label = md.utils.escapeHtml(meta.raw);
		return `<a class="hashtag" data-hashtag="${meta.normalized}">#${label}</a>`;
	};
}
