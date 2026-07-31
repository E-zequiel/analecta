<script lang="ts">
	import { onMount } from 'svelte';
	import { Compartment, EditorState, type Text } from '@codemirror/state';
	import { EditorView, keymap } from '@codemirror/view';
	import { defaultKeymap, historyKeymap, history } from '@codemirror/commands';
	import { markdown } from '@codemirror/lang-markdown';
	import { tokyoNightInit } from '@uiw/codemirror-theme-tokyo-night';
	import { tokyoNightDay } from '@uiw/codemirror-theme-tokyo-night-day';

	const {
		value,
		onChange,
		onSave,
		initialScrollFraction = null,
	}: {
		value: string;
		onChange: (content: string) => void;
		onSave: () => void;
		initialScrollFraction?: number | null;
	} = $props();

	let container: HTMLDivElement;
	let view: EditorView;
	let lastEmitted: string;
	// True once the initial cursor/scroll placement has been applied (or there was none
	// requested) — guards against later external `value` updates (e.g. revert()) yanking
	// the cursor back to the reading-view handoff position.
	let cursorApplied = $state(false);
	const themeCompartment = new Compartment();

	// Maps a 0–1 scroll fraction from the reading view onto an approximate line in the
	// source doc. Approximate, not a real source-map: rendered line height varies (headings,
	// code blocks, images), so this is only accurate near the very top/bottom of an article.
	function targetPosForFraction(doc: Text): number | null {
		if (initialScrollFraction == null || doc.lines <= 1) return null;
		const clamped = Math.min(1, Math.max(0, initialScrollFraction));
		const lineNumber = Math.min(doc.lines, Math.round(clamped * (doc.lines - 1)) + 1);
		return doc.line(lineNumber).from;
	}

	function applyInitialCursor(doc: Text) {
		const pos = targetPosForFraction(doc);
		if (pos == null) return;
		cursorApplied = true;
		view.dispatch({ selection: { anchor: pos } });
		requestAnimationFrame(() => {
			view?.dispatch({ effects: EditorView.scrollIntoView(pos, { y: 'center' }) });
		});
	}

	const tokyoNightDark = tokyoNightInit({ settings: { foreground: '#a9b1d6' } });

	function pickTheme(isLight: boolean) {
		return isLight ? tokyoNightDay : tokyoNightDark;
	}

	onMount(() => {
		lastEmitted = value;
		if (initialScrollFraction == null) cursorApplied = true;
		const root = document.documentElement;
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
				themeCompartment.of(pickTheme(root.classList.contains('theme-light'))),
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

		if (!cursorApplied) applyInitialCursor(view.state.doc);

		const observer = new MutationObserver(() => {
			view.dispatch({
				effects: themeCompartment.reconfigure(pickTheme(root.classList.contains('theme-light'))),
			});
		});
		observer.observe(root, { attributeFilter: ['class'] });

		return () => {
			observer.disconnect();
			view.destroy();
		};
	});

	$effect(() => {
		if (view && value !== lastEmitted) {
			view.dispatch({
				changes: { from: 0, to: view.state.doc.length, insert: value },
			});
			lastEmitted = value;
			if (!cursorApplied) applyInitialCursor(view.state.doc);
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
