#!/usr/bin/env node
// Manual comparison tool: runs Defuddle's own extraction algorithm against a
// real page's raw HTML, offline, no browser — see docs/defuddle-decision.md
// for why Defuddle is kept as a dev-only diagnostic rather than a live
// pipeline component. Useful when a real extraction failure comes up in the
// Python pipeline: run this against the same HTML it fetched, and see
// whether Defuddle's algorithm recovers something worth hand-porting.
//
// Usage:
//   node scripts/defuddle-diagnostic.mjs <path-to-html-file> [source-url]

import { readFile } from 'node:fs/promises';
import { Defuddle } from 'defuddle/node';

const [, , htmlPath, sourceUrl] = process.argv;

if (!htmlPath) {
	console.error('Usage: node scripts/defuddle-diagnostic.mjs <path-to-html-file> [source-url]');
	process.exit(1);
}

const html = await readFile(htmlPath, 'utf-8');
const result = await Defuddle(html, sourceUrl, { useAsync: false, debug: true, markdown: true });

console.log(
	JSON.stringify(
		{
			title: result.title,
			author: result.author,
			description: result.description,
			published: result.published,
			wordCount: result.wordCount,
			extractorType: result.extractorType,
			debug: result.debug,
			content: result.content,
		},
		null,
		2
	)
);
