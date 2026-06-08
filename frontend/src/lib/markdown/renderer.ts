import MarkdownIt from 'markdown-it';
import footnote from 'markdown-it-footnote';
import taskLists from 'markdown-it-task-lists';
import { fromHighlighter } from '@shikijs/markdown-it/core';
import { createHighlighterCoreSync } from 'shiki/core';
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript';
import tokyoNight from '@shikijs/themes/tokyo-night';
import langPython from '@shikijs/langs/python';
import langBash from '@shikijs/langs/bash';
import langRust from '@shikijs/langs/rust';
import langTypeScript from '@shikijs/langs/typescript';
import langJavaScript from '@shikijs/langs/javascript';
import langHtml from '@shikijs/langs/html';
import langCss from '@shikijs/langs/css';
import langGo from '@shikijs/langs/go';
import langJava from '@shikijs/langs/java';
import langC from '@shikijs/langs/c';
import langSql from '@shikijs/langs/sql';
import langYaml from '@shikijs/langs/yaml';
import { createStyleToClassTransformer } from './shiki-style-to-class.js';
import { convertFileSrc } from '$lib/platform';

const highlighter = createHighlighterCoreSync({
	themes: [tokyoNight],
	langs: [
		langPython,
		langBash,
		langRust,
		langTypeScript,
		langJavaScript,
		langHtml,
		langCss,
		langGo,
		langJava,
		langC,
		langSql,
		langYaml,
	],
	engine: createJavaScriptRegexEngine(),
});

function resolveImagePath(markdownFilePath: string, relativeSrc: string): string {
	const dir = markdownFilePath.substring(0, markdownFilePath.lastIndexOf('/'));
	const parts = `${dir}/${relativeSrc}`.split('/');
	const resolved: string[] = [];
	for (const part of parts) {
		if (part === '..') resolved.pop();
		else if (part !== '' || resolved.length === 0) resolved.push(part);
	}
	return resolved.join('/');
}

function stripFrontmatter(source: string): string {
	if (!source.startsWith('---\n') && !source.startsWith('---\r\n')) return source;
	const end = source.indexOf('\n---', 4);
	if (end === -1) return source;
	const afterClose = source.indexOf('\n', end + 1);
	return afterClose === -1 ? '' : source.slice(afterClose + 1);
}

export function createRenderer(markdownFilePath: string): (source: string) => string {
	const md = new MarkdownIt({ html: false, linkify: true, typographer: true })
		.use(footnote)
		.use(taskLists, { enabled: false, label: true })
		.use(
			fromHighlighter(highlighter, {
				theme: 'tokyo-night',
				transformers: [createStyleToClassTransformer()],
			})
		);

	const defaultImage = md.renderer.rules['image']!;

	md.renderer.rules['image'] = (tokens, idx, options, env, self) => {
		const token = tokens[idx];
		const src = token.attrGet('src') ?? '';
		if (src && !src.startsWith('http') && !src.startsWith('asset:')) {
			const absolute = resolveImagePath(markdownFilePath, src);
			token.attrSet('src', convertFileSrc(absolute));
		}
		return defaultImage(tokens, idx, options, env, self);
	};

	return (source: string) => md.render(stripFrontmatter(source));
}
