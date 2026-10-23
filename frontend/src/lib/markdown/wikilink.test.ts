// Unit tests for the wikilink markdown-it inline rule. The contract mirrors
// the backend's _WIKILINK_RE (backend/src/analecta/markdown/backlinks.py):
// [[Title]] / [[Title|Alias]], trimmed, rejected when empty or containing
// brackets, resolved through the caller-provided resolveTitle callback —
// resolved entries render as anchors, unresolved as a plain span.
import MarkdownIt from 'markdown-it';
import { describe, expect, test, vi } from 'vitest';
import wikilink from './wikilink';

function render(source: string, resolve: (title: string) => number | null): string {
	const md = new MarkdownIt().use(wikilink, { resolveTitle: resolve });
	return md.render(source);
}

describe('wikilink rule', () => {
	test('a resolved wikilink renders as an anchor with data-entry-id', () => {
		const html = render('[[Known]]', (t) => (t === 'Known' ? 7 : null));
		expect(html).toContain('<a class="wikilink" data-entry-id="7">Known</a>');
	});

	test('an unresolved wikilink renders as a plain span', () => {
		const html = render('[[Missing]]', () => null);
		expect(html).toContain('<span class="wikilink-unresolved">Missing</span>');
	});

	test('an alias overrides the displayed label, not the resolution title', () => {
		const resolveTitle = vi.fn((t: string) => (t === 'Known' ? 7 : null));
		const html = render('[[Known | Alias]]', resolveTitle);
		expect(html).toContain('<a class="wikilink" data-entry-id="7">Alias</a>');
		expect(resolveTitle).toHaveBeenCalledWith('Known');
	});

	test('an empty alias falls back to the title for the label', () => {
		const html = render('[[Known|]]', () => 7);
		expect(html).toContain('data-entry-id="7">Known</a>');
	});

	test('a title that trims to empty is rejected (plain text out)', () => {
		const html = render('[[   |alias]]', () => 7);
		expect(html).not.toContain('wikilink');
	});

	test('brackets inside the inner text are rejected', () => {
		expect(render('[[a[b]]', () => 7)).not.toContain('wikilink');
		expect(render('[[a]b]]', () => 7)).not.toContain('wikilink');
	});

	test('an unclosed wikilink stays plain text', () => {
		expect(render('before [[Title', () => 7)).not.toContain('wikilink');
	});

	test('the rule works mid-paragraph and leaves the rest intact', () => {
		const html = render('a [[Known]] b', () => 7);
		expect(html).toContain('<p>a <a class="wikilink" data-entry-id="7">Known</a> b</p>');
	});

	test('the label is HTML-escaped', () => {
		const html = render('[[a&b]]', () => 7);
		expect(html).toContain('<a class="wikilink" data-entry-id="7">a&amp;b</a>');
	});
});
