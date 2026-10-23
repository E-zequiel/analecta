// Unit tests for the Shiki style-to-class transformer. Contract: inline
// styles are stripped from the hast tree and replaced by a `__s_<hex>`
// class derived from cyrb53 — the same style string must always yield the
// same class, so the build-time instance (gen-shiki-css.mjs) and the
// runtime instance (renderer.ts) stay interchangeable.
import type { ShikiTransformer } from 'shiki/core';
import { describe, expect, test } from 'vitest';
import { createStyleToClassTransformer } from './shiki-style-to-class';

// hast element: the shape Shiki emits (pre/span).
function el(properties: Record<string, unknown>): {
	type: string;
	tagName: string;
	properties: Record<string, unknown>;
	children: unknown[];
} {
	return { type: 'element', tagName: 'pre', properties, children: [] };
}

type TestNode = ReturnType<typeof el>;

// The transformer hooks declare `this: ShikiTransformerContext`; the logic
// under test only uses that context for addClassToHast (pre) and nothing
// (span). Invoke each hook with a minimal stand-in for the context instead
// of spinning up Shiki's full highlighter pipeline.
function runPre(transformer: ShikiTransformer, node: TestNode): void {
	const ctx = {
		addClassToHast: (n: { properties: Record<string, unknown> }, cls: string | string[]) => {
			n.properties.class = cls;
		},
	};
	const pre = transformer.pre as unknown as (this: typeof ctx, hast: TestNode) => void;
	pre.call(ctx, node);
}

function runSpan(transformer: ShikiTransformer, node: TestNode): void {
	// Hook signature: (hast, line, col, lineElement, token) — only the hast
	// node is read here.
	const span = transformer.span as unknown as (
		this: null,
		hast: TestNode,
		line: number,
		col: number,
		lineElement: TestNode,
		token: null
	) => void;
	span.call(null, node, 0, 0, node, null);
}

describe('createStyleToClassTransformer', () => {
	test('pre: replaces a style with a __s_ class', () => {
		const transformer = createStyleToClassTransformer();
		const node = el({ style: 'color: #ff0000' });
		runPre(transformer, node);
		expect(node.properties.style).toBeUndefined();
		expect(node.properties.class).toMatch(/^__s_[0-9a-f]+$/);
	});

	test('pre: an absent or non-string style is left untouched', () => {
		const transformer = createStyleToClassTransformer();
		const noStyle = el({ class: 'keep' });
		runPre(transformer, noStyle);
		expect(noStyle.properties.class).toBe('keep');

		const numeric = el({ style: 42 });
		runPre(transformer, numeric);
		expect(numeric.properties.style).toBe(42);
	});

	test('span: sets the class on an element without one', () => {
		const transformer = createStyleToClassTransformer();
		const node = el({ style: 'color: red' });
		runSpan(transformer, node);
		expect(node.properties.style).toBeUndefined();
		expect(String(node.properties.class)).toMatch(/^__s_[0-9a-f]+$/);
	});

	test('span: appends to an existing string class', () => {
		const transformer = createStyleToClassTransformer();
		const node = el({ style: 'color: red', class: 'line' });
		runSpan(transformer, node);
		expect(String(node.properties.class)).toMatch(/^line __s_[0-9a-f]+$/);
	});

	test('span: appends to an existing array class', () => {
		const transformer = createStyleToClassTransformer();
		const node = el({ style: 'color: red', class: ['line', 'token'] });
		runSpan(transformer, node);
		expect(String(node.properties.class)).toMatch(/^line token __s_[0-9a-f]+$/);
	});

	test('span: an absent or non-string style is left untouched', () => {
		const transformer = createStyleToClassTransformer();
		const absent = el({});
		runSpan(transformer, absent);
		expect(absent.properties.class).toBeUndefined();

		const numeric = el({ style: 0, class: 'keep' });
		runSpan(transformer, numeric);
		expect(numeric.properties.style).toBe(0);
		expect(numeric.properties.class).toBe('keep');
	});

	test('the same style string always yields the same class (build/runtime parity)', () => {
		const transformer = createStyleToClassTransformer();
		const classes: unknown[] = [];
		for (const style of ['color: red', 'color: red', 'background: #1a1b26; color: red']) {
			const node = el({ style });
			runPre(transformer, node);
			classes.push(node.properties.class);
		}
		expect(classes[0]).toEqual(classes[1]);
		// A different style yields a different class.
		expect(classes[0]).not.toEqual(classes[2]);
	});
});
