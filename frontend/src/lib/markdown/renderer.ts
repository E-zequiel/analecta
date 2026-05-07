import MarkdownIt from 'markdown-it';
import footnote from 'markdown-it-footnote';
import taskLists from 'markdown-it-task-lists';
import { convertFileSrc } from '@tauri-apps/api/core';

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

export function createRenderer(markdownFilePath: string): (source: string) => string {
	const md = new MarkdownIt({ html: false, linkify: true, typographer: true })
		.use(footnote)
		.use(taskLists, { enabled: false, label: true });

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

	return (source: string) => md.render(source);
}
