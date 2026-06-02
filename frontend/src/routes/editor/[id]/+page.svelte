<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { readTextFile, writeTextFile } from '$lib/platform';
	import { entries as entriesApi, type Entry } from '$lib/api/client';
	import { entryChangedTick } from '$lib/stores/sse';
	import { createRenderer } from '$lib/markdown/renderer';
	import MarkdownEditor from '$lib/components/MarkdownEditor.svelte';
	import '$lib/markdown/tokyo-night.css';

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

	onMount(async () => {
		try {
			const e = await entriesApi.get(entryId);
			entry = e;
			const source = await readTextFile(e.file_path);
			content = source;
			originalContent = source;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	});

	function extractHashtags(text: string): string[] {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- local algorithmic variable, not reactive state
		const tags = new Set<string>();
		for (const line of text.split('\n')) {
			// Skip markdown headings (# Heading) but not hashtag-only lines (#tag1 #tag2)
			if (/^#{1,6}(?:\s|$)/.test(line.trimStart())) continue;
			for (const m of line.matchAll(/#([a-zA-Z][a-zA-Z0-9_]*)/g)) {
				tags.add(m[1].toLowerCase());
			}
		}
		return [...tags];
	}

	function handleChange(newContent: string) {
		content = newContent;
		saved = false;
		if (showPreview && entry) {
			clearTimeout(previewTimer);
			previewTimer = setTimeout(() => {
				previewHtml = createRenderer(entry!.file_path)(content);
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
				tags: extractHashtags(content),
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
			previewHtml = createRenderer(entry.file_path)(content);
		}
	}
</script>

<div class="editor-page">
	<div class="toolbar">
		<button class="btn" onclick={() => goto(`/viewer/${entryId}`)}>← Back</button>
		<button class="btn" class:active={showPreview} onclick={togglePreview}>Preview</button>
		<button class="btn" onclick={save} disabled={saving}>
			{saving ? 'Saving…' : saved ? 'Saved ✓' : 'Save'}
		</button>
		<button class="btn" onclick={revert} disabled={content === originalContent}>Revert</button>
		{#if entry}
			<span class="entry-title">{entry.title}</span>
		{/if}
	</div>

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

	.toolbar {
		display: flex;
		align-items: center;
		gap: 0.25rem;
		padding: 0.5rem 1rem;
		border-bottom: 1px solid var(--border);
		background: var(--bg-dark);
		flex-shrink: 0;
	}

	.entry-title {
		margin-left: 0.5rem;
		font-size: 12px;
		color: var(--fg-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.btn {
		padding: 0.3rem 0.6rem;
		background: none;
		border: 1px solid transparent;
		border-radius: 4px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 12px;
		cursor: pointer;
		white-space: nowrap;
		transition:
			color 0.15s,
			background 0.15s,
			border-color 0.15s;
	}

	.btn:hover:not(:disabled) {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.btn.active {
		color: var(--accent);
		border-color: var(--accent-dark);
		background: var(--bg-highlight);
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
