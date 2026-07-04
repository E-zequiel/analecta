<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { readTextFile, writeTextFile } from '$lib/platform';
	import { entries as entriesApi, type Entry } from '$lib/api/client';
	import { entryChangedTick } from '$lib/stores/sse';
	import { entryTitleIndex, ensureEntryTitleIndexLoaded } from '$lib/stores/entryTitles';
	import {
		editorSaving,
		editorSaved,
		editorShowPreview,
		editorIsDirty,
		editorActions,
	} from '$lib/stores/toolbar';
	import { createRenderer } from '$lib/markdown/renderer';
	import MarkdownEditor from '$lib/components/MarkdownEditor.svelte';
	import '$lib/markdown/tokyo-night.css';
	import '$lib/markdown/shiki-classes.css';

	const entryId = $derived(parseInt($page.params['id'] as string));

	let entry = $state<Entry | null>(null);
	let content = $state('');
	let originalContent = $state('');
	let showPreview = $state(false);
	let previewHtml = $state('');
	let saving = $state(false);
	let saved = $state(false);
	let error = $state('');

	let previewTimer: ReturnType<typeof setTimeout>;

	onMount(() => {
		ensureEntryTitleIndexLoaded();

		entriesApi
			.get(entryId)
			.then((e) => {
				entry = e;
				return readTextFile(e.file_path);
			})
			.then((source) => {
				content = source;
				originalContent = source;
			})
			.catch((err) => {
				error = err instanceof Error ? err.message : String(err);
			});

		editorActions.set({
			save,
			revert,
			togglePreview,
			goBack: () => goto(`/viewer/${entryId}`),
		});

		return () => {
			editorActions.set(null);
			editorSaving.set(false);
			editorSaved.set(false);
			editorShowPreview.set(false);
			editorIsDirty.set(false);
		};
	});

	$effect(() => {
		editorSaving.set(saving);
	});

	$effect(() => {
		editorSaved.set(saved);
	});

	$effect(() => {
		editorShowPreview.set(showPreview);
	});

	$effect(() => {
		editorIsDirty.set(content !== originalContent);
	});

	function resolveWikilinkTitle(title: string): number | null {
		return $entryTitleIndex.get(title.toLowerCase()) ?? null;
	}

	function handleChange(newContent: string) {
		content = newContent;
		saved = false;
		if (showPreview && entry) {
			clearTimeout(previewTimer);
			previewTimer = setTimeout(() => {
				previewHtml = createRenderer(entry!.file_path, resolveWikilinkTitle)(content);
			}, 400);
		}
	}

	async function save() {
		if (!entry || saving) return;
		saving = true;
		error = '';
		try {
			await writeTextFile(entry.file_path, content);
			await entriesApi.patch(entry.id, {
				fts: { title: entry.title, content },
			});
			originalContent = content;
			entryChangedTick.update((n) => n + 1);
			saved = true;
			setTimeout(() => {
				saved = false;
			}, 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			saving = false;
		}
	}

	function revert() {
		content = originalContent;
		saved = false;
	}

	function togglePreview() {
		showPreview = !showPreview;
		if (showPreview && entry) {
			previewHtml = createRenderer(entry.file_path, resolveWikilinkTitle)(content);
		}
	}
</script>

<div class="editor-page">
	{#if error}
		<div class="error-banner">{error}</div>
	{/if}

	{#if entry}
		<div class="panes" class:split={showPreview}>
			<div class="pane editor-pane">
				<MarkdownEditor value={content} onChange={handleChange} onSave={save} />
			</div>
			{#if showPreview}
				<div class="pane preview-pane">
					<div class="markdown-body preview-content">
						<!-- eslint-disable-next-line svelte/no-at-html-tags -- markdown-it output, not raw user/network HTML -->
						{@html previewHtml}
					</div>
				</div>
			{/if}
		</div>
	{:else if !error}
		<p class="hint">Loading…</p>
	{/if}
</div>

<style>
	.editor-page {
		display: flex;
		flex-direction: column;
		height: 100%;
	}

	.error-banner {
		padding: 0.5rem 1rem;
		font-size: 12px;
		color: var(--red);
		background: var(--bg-alt);
		border-bottom: 1px solid var(--border);
	}

	.hint {
		padding: 1rem;
		color: var(--fg-muted);
		font-size: 13px;
	}

	.panes {
		flex: 1;
		display: flex;
		overflow: hidden;
	}

	.pane {
		flex: 1;
		overflow: hidden;
	}

	.panes.split .pane {
		flex: 1;
	}

	.panes.split .editor-pane {
		border-right: 1px solid var(--border);
	}

	.preview-pane {
		overflow-y: auto;
	}

	.preview-content {
		padding: 1.5rem 2rem;
		max-width: 760px;
	}
</style>
