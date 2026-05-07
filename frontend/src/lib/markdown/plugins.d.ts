declare module 'markdown-it-footnote' {
	import type MarkdownIt from 'markdown-it';
	function footnote(md: MarkdownIt): void;
	export = footnote;
}

declare module 'markdown-it-task-lists' {
	import type MarkdownIt from 'markdown-it';
	interface Options {
		enabled?: boolean;
		label?: boolean;
		labelAfter?: boolean;
	}
	function taskLists(md: MarkdownIt, options?: Options): void;
	export = taskLists;
}
