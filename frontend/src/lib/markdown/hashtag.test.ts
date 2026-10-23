// Unit tests for the hashtag markdown-it inline rule. The contract mirrors
// the backend's _HASHTAG_RE (backend/src/analecta/markdown/backlinks.py):
// identity is casefold-only and accent-sensitive, leading char is a letter
// (incl. Spanish áéíóúñü), continuation adds digits and _ - ' ~ ^, backtick
// is deliberately excluded (collides with inline code), and the rule only
// fires at a token boundary (start or after whitespace).
import MarkdownIt from 'markdown-it';
import { describe, expect, test } from 'vitest';
import hashtag from './hashtag';

function render(source: string): string {
	const md = new MarkdownIt().use(hashtag);
	return md.render(source);
}

function anchor(raw: string): string {
	return `<a class="hashtag" data-hashtag="${raw.toLowerCase()}">#${raw}</a>`;
}

describe('hashtag rule', () => {
	test('renders a simple tag as an anchor', () => {
		expect(render('#cafe')).toContain(anchor('cafe'));
	});

	test('keeps surrounding text and advances the position correctly', () => {
		expect(render('texto #cafe fin')).toContain(`texto ${anchor('cafe')} fin`);
	});

	test('parses two consecutive tags in one paragraph', () => {
		const html = render('#a #b');
		expect(html).toContain(anchor('a'));
		expect(html).toContain(anchor('b'));
	});

	test('identity is casefold-only: label casing preserved, data attribute lowercased', () => {
		expect(render('#Cafe')).toContain(anchor('Cafe'));
	});

	test('identity is accent-sensitive: café ≠ cafe', () => {
		expect(render('#café')).toContain(`<a class="hashtag" data-hashtag="café">#café</a>`);
		expect(render('#café')).not.toContain('data-hashtag="cafe"');
	});

	test('accented leading char (Spanish letters) is accepted', () => {
		expect(render('#árbol')).toContain(`<a class="hashtag" data-hashtag="árbol">#árbol</a>`);
	});

	test("continuation chars _ - ' ~ ^ and digits are accepted", () => {
		expect(render("#a_b-c'd~e^9")).toContain(anchor("a_b-c'd~e^9"));
	});

	test('trailing punctuation is not part of the tag', () => {
		const html = render('#tag.');
		expect(html).toContain(anchor('tag'));
		expect(html).toContain('.');
	});

	test('mid-word # is rejected (foo#bar stays plain text)', () => {
		const html = render('foo#bar');
		expect(html).not.toContain('data-hashtag');
		expect(html).toContain('foo#bar');
	});

	test('a bare # without a body is rejected', () => {
		expect(render('#')).not.toContain('data-hashtag');
		expect(render('# ')).not.toContain('data-hashtag');
	});

	test('a leading non-letter body is rejected', () => {
		expect(render('#9lives')).not.toContain('data-hashtag');
	});

	test('backtick is excluded from the body: the tag ends at it', () => {
		const html = render('#tag`code`');
		expect(html).toContain(anchor('tag'));
		// 'tag`code`' must not be swallowed whole — the body stops before `.
		expect(html).not.toContain(anchor('tag`code`'));
	});

	test('inside inline code spans the rule never fires', () => {
		const html = render('`#tag`');
		expect(html).not.toContain('data-hashtag');
		expect(html).toContain('#tag');
	});

	test('inside fenced code blocks the rule never fires', () => {
		const html = render('```\n#tag\n```');
		expect(html).not.toContain('data-hashtag');
		expect(html).toContain('#tag');
	});

	test('the label is HTML-escaped', () => {
		// '&' is not a body char, so the tag ends before it and the rest is
		// plain text — check both: anchor for 'a', escaped '&' outside.
		const html = render('#a & b');
		expect(html).toContain(anchor('a'));
		expect(html).toContain('&amp;');
	});
});
