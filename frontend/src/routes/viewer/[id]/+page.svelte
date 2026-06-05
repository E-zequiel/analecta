<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { openUrl, confirm, readTextFile } from '$lib/platform';
	import { ChevronDown, ChevronRight } from '@lucide/svelte';
	import {
		entries as entriesApi,
		tags as tagsApi,
		config as configApi,
		type Entry,
		type Tag,
	} from '$lib/api/client';
	import { createRenderer } from '$lib/markdown/renderer';
	import '$lib/markdown/tokyo-night.css';
	import { lastViewedId } from '$lib/stores/ui';
	import { ensureEntryTab, closeTab } from '$lib/stores/tabs';
	import { entryChangedTick, lastChangedEntry } from '$lib/stores/sse';
	import { showContextMenu } from '$lib/stores/contextMenu';
	import { viewerEntry, viewerFontSize, viewerTagsOpen, viewerActions } from '$lib/stores/toolbar';

	const entryId = $derived(parseInt($page.params['id'] as string));

	$effect(() => {
		if (!isNaN(entryId)) lastViewedId.set(entryId);
	});

	let entry = $state<Entry | null>(null);
	let html = $state('');
	let source = $state('');
	let propertiesOpen = $state(false);

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric',
		});
	}

	function parseFrontmatter(src: string): {
		author?: string;
		published?: string;
		description?: string;
	} {
		const match = src.match(/^---\n([\s\S]*?)\n---/);
		if (!match) return {};
		const result: { author?: string; published?: string; description?: string } = {};
		for (const line of match[1].split('\n')) {
			const m = line.match(/^(author|published|description):\s*(.*)/);
			if (!m) continue;
			const val = m[2].replace(/^["']|["']$/g, '').trim();
			if (val) result[m[1] as 'author' | 'published' | 'description'] = val;
		}
		return result;
	}

	const frontmatter = $derived(parseFrontmatter(source));

	const wordCount = $derived(
		source
			.replace(/^---[\s\S]*?---\n?/, '')
			.replace(/[#*`[\]_~>]/g, ' ')
			.trim()
			.split(/\s+/)
			.filter(Boolean).length
	);
	const readTime = $derived(
		wordCount > 0 ? `~${Math.max(1, Math.round(wordCount / 200))} min` : ''
	);
	const charCount = $derived(source.replace(/^---[\s\S]*?---\n?/, '').trim().length);

	let contentEl = $state<HTMLElement | null>(null);
	let readingFontSize = $state(17);

	$effect(() => {
		function handleKeydown(e: KeyboardEvent) {
			if (!contentEl) return;
			if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
			if (e.target instanceof HTMLElement && e.target.closest('.tags-dialog')) return;
			const step = 120;
			if (e.key === 'ArrowDown') {
				contentEl.scrollBy(0, step);
				e.preventDefault();
			} else if (e.key === 'ArrowUp') {
				contentEl.scrollBy(0, -step);
				e.preventDefault();
			} else if (e.key === 'PageDown') {
				contentEl.scrollBy(0, contentEl.clientHeight * 0.85);
				e.preventDefault();
			} else if (e.key === 'PageUp') {
				contentEl.scrollBy(0, -contentEl.clientHeight * 0.85);
				e.preventDefault();
			} else if (e.key === 'Home') {
				contentEl.scrollTo(0, 0);
				e.preventDefault();
			} else if (e.key === 'End') {
				contentEl.scrollTo(0, contentEl.scrollHeight);
				e.preventDefault();
			}
		}
		window.addEventListener('keydown', handleKeydown);
		return () => window.removeEventListener('keydown', handleKeydown);
	});

	let error = $state('');

	let newTagInput = $state('');
	let allTags = $state<Tag[]>([]);
	let tagsContainerEl = $state<HTMLElement | null>(null);
	let tagAddInputEl = $state<HTMLInputElement | null>(null);
	let showAllSuggestions = $state(false);

	const tagSuggestions = $derived(
		newTagInput.length > 0
			? allTags
					.map((t) => t.name)
					.filter(
						(n) => !entry?.tags.includes(n) && n.toLowerCase().includes(newTagInput.toLowerCase())
					)
					.slice(0, 6)
			: showAllSuggestions
				? allTags
						.map((t) => t.name)
						.filter((n) => !entry?.tags.includes(n))
						.slice(0, 6)
				: []
	);

	$effect(() => {
		if ($viewerTagsOpen) {
			setTimeout(() => tagAddInputEl?.focus(), 0);
		} else {
			showAllSuggestions = false;
		}
	});

	function navigateSuggestion(direction: 1 | -1, from: HTMLElement) {
		if (!tagsContainerEl) return;
		const items = Array.from(tagsContainerEl.querySelectorAll<HTMLElement>('.suggestion-item'));
		const idx = items.indexOf(from);
		if (direction === -1 && idx <= 0) {
			tagAddInputEl?.focus();
		} else if (direction === 1 && idx === items.length - 1) {
			tagAddInputEl?.focus();
		} else {
			items[idx + direction]?.focus();
		}
	}

	$effect(() => {
		if ($viewerTagsOpen) fetchAllTags();
	});

	// Sync entry state when an external patch (e.g. context menu) updates this entry.
	$effect(() => {
		const changed = $lastChangedEntry;
		if (changed && changed.id === untrack(() => entryId)) {
			entry = changed;
		}
	});

	// Config is stable across entries — fetch once on mount.
	onMount(() => {
		configApi
			.get()
			.then((cfg) => {
				readingFontSize = cfg.reading_font_size;
			})
			.catch(() => {});

		viewerActions.set({
			setStatus,
			toggleFlag,
			adjustFont: adjustFontSize,
			copyUrl,
			deleteEntry,
			goBack: () => closeTab(`viewer-${entryId}`),
			goToEditor: () => entry && goto(`/editor/${entry.id}`),
		});

		return () => {
			viewerActions.set(null);
			viewerEntry.set(null);
			viewerFontSize.set(17);
			viewerTagsOpen.set(false);
		};
	});

	$effect(() => {
		viewerEntry.set(entry);
	});

	$effect(() => {
		viewerFontSize.set(readingFontSize);
	});

	// Re-fetch entry whenever the route param changes (same-component navigation).
	$effect(() => {
		const id = entryId;
		if (isNaN(id)) return;

		entry = null;
		html = '';
		error = '';
		viewerTagsOpen.set(false);

		let cancelled = false;

		entriesApi
			.get(id)
			.then((e) => {
				if (cancelled) return Promise.reject('cancelled');
				entry = e;
				ensureEntryTab(id, e.title, e.source_type);
				return readTextFile(e.file_path);
			})
			.then((src) => {
				if (cancelled) return;
				source = src;
				html = createRenderer(entry!.file_path)(src);
			})
			.catch((err) => {
				if (!cancelled && err !== 'cancelled') {
					error = err instanceof Error ? err.message : String(err);
				}
			});

		return () => {
			cancelled = true;
		};
	});

	async function setStatus(status: string) {
		if (!entry) return;
		const newStatus = entry.status === status ? 'unread' : status;
		if (newStatus === entry.status) return;
		const updated = await entriesApi.patch(entry.id, { status: newStatus });
		entry = updated;
		lastChangedEntry.set(updated);
		entryChangedTick.update((n) => n + 1);
	}

	async function toggleFlag(flag: string) {
		if (!entry) return;
		const current = entry.flags ?? [];
		let newFlags: string[];
		if (flag === 'archive') {
			// Archiving strips all other flags (bookmark, gem)
			newFlags = current.includes('archive') ? [] : ['archive'];
		} else {
			newFlags = current.includes(flag)
				? current.filter((f) => f !== flag)
				: [...current.filter((f) => f !== 'archive'), flag];
		}
		const updated = await entriesApi.patch(entry.id, { flags: newFlags });
		entry = updated;
		lastChangedEntry.set(updated);
		entryChangedTick.update((n) => n + 1);
	}

	async function copyUrl() {
		if (!entry) return;
		await navigator.clipboard.writeText(`analecta://open?id=${entry.id}`);
	}

	async function deleteEntry() {
		if (!entry) return;
		const ok = await confirm(`Delete "${entry.title}"?`, 'Confirm Delete');
		if (!ok) return;
		await entriesApi.delete(entry.id);
		entryChangedTick.update((n) => n + 1);
		closeTab(`viewer-${entry.id}`);
	}

	function adjustFontSize(delta: number) {
		readingFontSize = Math.max(12, Math.min(24, readingFontSize + delta));
		document.documentElement.style.setProperty('--font-text-size', `${readingFontSize}px`);
		configApi.update({ reading_font_size: readingFontSize }).catch(() => {});
	}

	async function fetchAllTags() {
		try {
			allTags = await tagsApi.list();
		} catch {
			// sidecar may not be ready
		}
	}

	async function addTag(name: string) {
		if (!entry || !name.trim()) return;
		const trimmed = name.trim();
		newTagInput = '';
		showAllSuggestions = false;
		if (!entry.tags.includes(trimmed)) {
			const addedEntry = await entriesApi.patch(entry.id, { tags: [...entry.tags, trimmed] });
			entry = addedEntry;
			lastChangedEntry.set(addedEntry);
			entryChangedTick.update((n) => n + 1);
		}
		setTimeout(() => tagAddInputEl?.focus(), 0);
	}

	async function removeTag(name: string) {
		if (!entry) return;
		const removedEntry = await entriesApi.patch(entry.id, {
			tags: entry.tags.filter((t) => t !== name),
		});
		entry = removedEntry;
		lastChangedEntry.set(removedEntry);
		entryChangedTick.update((n) => n + 1);
	}

	function handleRightClick(e: MouseEvent) {
		if (!entry) return;
		showContextMenu(e, {
			id: entry.id,
			title: entry.title,
			url: entry.url,
			file_path: entry.file_path,
			status: entry.status,
			flags: entry.flags,
		});
	}

	async function handleContentClick(e: MouseEvent) {
		const link = (e.target as HTMLElement).closest('a');
		if (!link) return;
		const href = link.getAttribute('href') ?? '';
		e.preventDefault();
		if (href.startsWith('http://') || href.startsWith('https://')) {
			await openUrl(href);
		}
	}
</script>

{#if $viewerTagsOpen && entry}
	<div
		class="tags-backdrop"
		onclick={() => viewerTagsOpen.set(false)}
		onkeydown={(e) => {
			if (e.key === 'Escape') viewerTagsOpen.set(false);
		}}
		role="button"
		tabindex="-1"
	>
		<div
			class="tags-dialog"
			role="dialog"
			aria-modal="true"
			tabindex="-1"
			bind:this={tagsContainerEl}
			onclick={(e) => e.stopPropagation()}
			onkeydown={(e) => {
				if (e.key === 'Escape') viewerTagsOpen.set(false);
			}}
		>
			{#if entry.tags.length > 0}
				<div class="tag-chips">
					{#each entry.tags as tag (tag)}
						<span class="chip">
							<span class="chip-label">{tag}</span>
							<button class="chip-remove" onclick={() => removeTag(tag)} title="Remove">×</button>
						</span>
					{/each}
				</div>
			{/if}
			<input
				class="tag-add-input"
				type="text"
				placeholder="Add tag…"
				bind:value={newTagInput}
				bind:this={tagAddInputEl}
				onkeydown={(e) => {
					if (e.key === 'Enter') {
						e.preventDefault();
						addTag(newTagInput);
					} else if (e.key === 'ArrowDown') {
						e.preventDefault();
						showAllSuggestions = true;
						setTimeout(
							() => tagsContainerEl?.querySelector<HTMLElement>('.suggestion-item')?.focus(),
							0
						);
					} else if (e.key === 'Escape') viewerTagsOpen.set(false);
				}}
			/>
			{#if tagSuggestions.length > 0}
				<div class="tag-suggestions">
					{#each tagSuggestions as s (s)}
						<button
							class="suggestion-item"
							onclick={() => addTag(s)}
							onkeydown={(e) => {
								if (e.key === 'ArrowDown') {
									e.preventDefault();
									navigateSuggestion(1, e.currentTarget);
								} else if (e.key === 'ArrowUp') {
									e.preventDefault();
									navigateSuggestion(-1, e.currentTarget);
								} else if (e.key === 'Escape') viewerTagsOpen.set(false);
							}}>{s}</button
						>
					{/each}
				</div>
			{/if}
		</div>
	</div>
{/if}

<div class="viewer">
	{#if error}
		<div class="error-banner">{error}</div>
	{:else if entry && html}
		<button class="props-bar" onclick={() => (propertiesOpen = !propertiesOpen)}>
			<span class="status-badge">{entry.status}</span>
			<span class="props-url">{entry.url}</span>
			<span class="props-meta">{formatDate(entry.created_at)}</span>
			{#if readTime}<span class="props-meta">{readTime}</span>{/if}
			{#if propertiesOpen}<ChevronDown size={13} />{:else}<ChevronRight size={13} />{/if}
		</button>
		{#if propertiesOpen}
			<div class="props-expanded">
				<div class="props-grid">
					<!-- Col1 (2fr): Author · Type · URL · Description -->
					<div class="props-col">
						{#if frontmatter.author}
							<div class="props-field">
								<div class="props-label">Author</div>
								<span class="props-value">{frontmatter.author}</span>
							</div>
						{/if}
						<div class="props-field">
							<div class="props-label">Type</div>
							<span class="props-value">{entry.source_type}</span>
						</div>
						<div class="props-field">
							<div class="props-label">URL</div>
							<span class="props-url-value">{entry.url}</span>
						</div>
						{#if frontmatter.description}
							<div class="props-field">
								<div class="props-label">Description</div>
								<span class="props-value">{frontmatter.description}</span>
							</div>
						{/if}
					</div>
					<!-- Col2 (1fr): Published · Saved · Words · Characters -->
					<div class="props-col">
						{#if frontmatter.published}
							<div class="props-field">
								<div class="props-label">Published</div>
								<span class="props-value">{frontmatter.published}</span>
							</div>
						{/if}
						<div class="props-field">
							<div class="props-label">Saved</div>
							<span class="props-value">{formatDate(entry.created_at)}</span>
						</div>
						<div class="props-field">
							<div class="props-label">Words</div>
							<span class="props-value">{wordCount.toLocaleString()}</span>
						</div>
						<div class="props-field">
							<div class="props-label">Characters</div>
							<span class="props-value">{charCount.toLocaleString()}</span>
						</div>
					</div>
					<!-- Col3 (1fr): Tags -->
					<div class="props-col">
						{#if entry.tags.length > 0}
							<div class="props-field">
								<div class="props-label">Tags</div>
								<div class="props-tags">
									{#each entry.tags as tag (tag)}
										<span class="prop-tag">#{tag}</span>
									{/each}
								</div>
							</div>
						{/if}
					</div>
				</div>
			</div>
		{/if}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="content" bind:this={contentEl} oncontextmenu={handleRightClick}>
			<div class="content-inner">
				<h1 class="entry-title">{entry.title}</h1>
				<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
				<div class="markdown-body" onclick={handleContentClick}>
					<!-- eslint-disable-next-line svelte/no-at-html-tags -- markdown-it output, not raw user/network HTML -->
					{@html html}
				</div>
			</div>
		</div>
	{:else if !error}
		<p class="hint">Loading…</p>
	{/if}
</div>

<style>
	.viewer {
		display: flex;
		flex-direction: column;
		height: 100%;
	}

	.error-banner {
		padding: 1rem;
		color: var(--red);
		font-size: 13px;
	}

	.hint {
		padding: 1rem;
		color: var(--fg-muted);
		font-size: 13px;
	}

	.content {
		flex: 1;
		overflow-y: auto;
		min-width: 0;
	}

	.content-inner {
		max-width: 720px;
		margin: 0 auto;
		padding: 2rem;
	}

	.entry-title {
		font-size: 1.4rem;
		font-weight: 700;
		color: var(--red);
		margin: 0 0 1rem;
		line-height: 1.3;
	}

	.props-bar {
		height: 33px;
		display: flex;
		align-items: center;
		width: 100%;
		padding: 0 16px;
		gap: 12px;
		cursor: pointer;
		background: var(--bg-dark);
		border: none;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
		transition: background 0.12s;
		color: inherit;
		font-family: inherit;
	}

	.props-bar:hover {
		background: var(--bg-highlight);
	}

	.status-badge {
		font-size: 0.6rem;
		font-weight: 700;
		padding: 2px 7px;
		border-radius: 3px;
		background: color-mix(in srgb, var(--accent) 13%, transparent);
		color: var(--accent);
		letter-spacing: 0.08em;
		text-transform: uppercase;
		flex-shrink: 0;
	}

	.props-url {
		font-size: 0.75rem;
		color: var(--fg-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex: 1;
		text-align: left;
	}

	.props-meta {
		font-size: 0.68rem;
		color: var(--fg-muted);
		flex-shrink: 0;
	}

	.props-expanded {
		padding: 0 16px 14px;
		border-bottom: 1px solid var(--border);
		background: var(--bg-dark);
		flex-shrink: 0;
	}

	.props-grid {
		display: grid;
		grid-template-columns: 2fr 1fr 1fr;
		gap: 0 32px;
		padding-top: 12px;
		align-items: start;
	}

	.props-col {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.props-field {
		min-width: 0;
	}

	.props-label {
		font-size: 0.6rem;
		letter-spacing: 0.1em;
		font-weight: 700;
		color: var(--fg-muted);
		text-transform: uppercase;
		margin-bottom: 5px;
	}

	.props-value {
		font-size: 0.75rem;
		color: var(--fg);
	}

	.props-url-value {
		font-size: 0.75rem;
		color: var(--accent);
		word-break: break-all;
	}

	.props-tags {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}

	.prop-tag {
		font-size: 0.65rem;
		color: var(--fg-muted);
		padding: 1px 6px;
		border-radius: 3px;
		background: var(--bg-alt);
		border: 1px solid var(--border);
	}

	.tags-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.55);
		display: flex;
		align-items: flex-start;
		justify-content: center;
		padding-top: 28vh;
		z-index: 200;
	}

	.tags-dialog {
		width: 320px;
		max-width: 90vw;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow: hidden;
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
		padding: 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.tag-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}

	.chip {
		display: inline-flex;
		align-items: center;
		gap: 3px;
		padding: 2px 4px 2px 6px;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 4px;
		font-size: var(--font-size-sublabel);
		color: var(--fg);
	}

	.chip-remove {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 14px;
		height: 14px;
		padding: 0;
		background: none;
		border: none;
		border-radius: 2px;
		color: var(--fg-muted);
		cursor: pointer;
		font-size: 0.9rem;
		line-height: 1;
		transition: color 0.12s;
	}

	.chip-remove:hover {
		color: var(--accent);
	}

	.tag-add-input {
		width: 100%;
		padding: 4px 6px;
		background: var(--bg-highlight);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg);
		font-family: inherit;
		font-size: 0.8rem;
		outline: none;
		box-sizing: border-box;
	}

	.tag-add-input:focus {
		border-color: var(--accent-dark);
	}

	.tag-suggestions {
		display: flex;
		flex-direction: column;
		max-height: 120px;
		overflow-y: auto;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--bg);
	}

	.suggestion-item {
		padding: 3px 6px;
		background: none;
		border: none;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: var(--font-size-label);
		cursor: pointer;
		text-align: left;
		transition:
			color 0.12s,
			background 0.12s;
	}

	.suggestion-item:hover,
	.suggestion-item:focus {
		color: var(--fg);
		background: var(--bg-highlight);
		outline: none;
	}
</style>
