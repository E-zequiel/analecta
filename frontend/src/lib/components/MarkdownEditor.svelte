<script lang="ts">
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { Compartment, EditorState } from '@codemirror/state';
	import { EditorView, keymap } from '@codemirror/view';
	import { defaultKeymap, historyKeymap, history } from '@codemirror/commands';
	import { markdown } from '@codemirror/lang-markdown';
	import { tokyoNightInit } from '@uiw/codemirror-theme-tokyo-night';
	import { tokyoNightDay } from '@uiw/codemirror-theme-tokyo-night-day';
	import { currentTheme } from '$lib/stores/ui';

	const {
		value,
		onChange,
		onSave,
	}: {
		value: string;
		onChange: (content: string) => void;
		onSave: () => void;
	} = $props();

	let container: HTMLDivElement;
	let view: EditorView;
	let lastEmitted: string;
	const themeCompartment = new Compartment();

	const tokyoNightDark = tokyoNightInit({ settings: { foreground: '#a9b1d6' } });

	function pickTheme(t: 'dark' | 'light') {
		return t === 'light' ? tokyoNightDay : tokyoNightDark;
	}

	onMount(() => {
		lastEmitted = value;
		const state = EditorState.create({
			doc: value,
			extensions: [
				history(),
				keymap.of([
					...defaultKeymap,
					...historyKeymap,
					{
						key: 'Mod-s',
						run: () => {
							onSave();
							return true;
						},
					},
				]),
				markdown(),
				themeCompartment.of(pickTheme(get(currentTheme))),
				EditorView.lineWrapping,
				EditorView.theme({
					'&': { height: '100%' },
					'&.cm-focused': { outline: 'none' },
					'.cm-scroller': {
						fontFamily: "'JetBrains Mono', monospace",
						fontSize: 'var(--font-text-size)',
						overflow: 'auto',
					},
					'.cm-content': { padding: '1rem 1.5rem', minHeight: '100%' },
				}),
				EditorView.updateListener.of((update) => {
					if (update.docChanged) {
						const content = update.state.doc.toString();
						lastEmitted = content;
						onChange(content);
					}
				}),
			],
		});

		view = new EditorView({ state, parent: container });
		return () => view.destroy();
	});

	$effect(() => {
		if (!view) return;
		view.dispatch({ effects: themeCompartment.reconfigure(pickTheme($currentTheme)) });
	});

	$effect(() => {
		if (view && value !== lastEmitted) {
			view.dispatch({
				changes: { from: 0, to: view.state.doc.length, insert: value },
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
