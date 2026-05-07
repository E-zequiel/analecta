<script lang="ts">
	import { onMount } from 'svelte';
	import { EditorState } from '@codemirror/state';
	import { EditorView, keymap } from '@codemirror/view';
	import { defaultKeymap, historyKeymap, history } from '@codemirror/commands';
	import { markdown } from '@codemirror/lang-markdown';
	import { tokyoNight } from '@uiw/codemirror-theme-tokyo-night';

	let {
		value,
		onChange,
		onSave
	}: {
		value: string;
		onChange: (content: string) => void;
		onSave: () => void;
	} = $props();

	let container: HTMLDivElement;
	let view: EditorView;
	let lastEmitted = value;

	onMount(() => {
		const state = EditorState.create({
			doc: value,
			extensions: [
				history(),
				keymap.of([
					...defaultKeymap,
					...historyKeymap,
					{ key: 'Mod-s', run: () => { onSave(); return true; } }
				]),
				markdown(),
				tokyoNight,
				EditorView.lineWrapping,
				EditorView.theme({
					'&': { height: '100%' },
					'&.cm-focused': { outline: 'none' },
					'.cm-scroller': {
						fontFamily: "'JetBrains Mono', monospace",
						fontSize: '14px',
						overflow: 'auto'
					},
					'.cm-content': { padding: '1rem 1.5rem', minHeight: '100%' }
				}),
				EditorView.updateListener.of((update) => {
					if (update.docChanged) {
						const content = update.state.doc.toString();
						lastEmitted = content;
						onChange(content);
					}
				})
			]
		});

		view = new EditorView({ state, parent: container });
		return () => view.destroy();
	});

	$effect(() => {
		if (view && value !== lastEmitted) {
			view.dispatch({
				changes: { from: 0, to: view.state.doc.length, insert: value }
			});
			lastEmitted = value;
		}
	});
</script>

<div bind:this={container} class="editor-wrap"></div>

<style>
	.editor-wrap {
		height: 100%;
		overflow: hidden;
	}
</style>
