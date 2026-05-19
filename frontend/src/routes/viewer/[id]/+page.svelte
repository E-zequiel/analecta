<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { openUrl } from '@tauri-apps/plugin-opener';
	import { confirm } from '@tauri-apps/plugin-dialog';
	import { readTextFile } from '@tauri-apps/plugin-fs';
	import {
		CornerUpLeft,
		PenLine,
		Link,
		Archive,
		Shredder,
		ShieldCheck,
		AArrowDown,
		AArrowUp,
		Eye,
		EyeClosed,
		Bookmark,
		Gem,
		BrainCircuit
	} from '@lucide/svelte';
	import {
		entries as entriesApi,
		tags as tagsApi,
		config as configApi,
		security,
		type Entry,
		type Tag,
		type ScanResult
	} from '$lib/api/client';
	import { createRenderer } from '$lib/markdown/renderer';
	import '$lib/markdown/tokyo-night.css';
	import { lastViewedId } from '$lib/stores/ui';
	import { ensureEntryTab, closeTab } from '$lib/stores/tabs';
	import { entryChangedTick, lastChangedEntry } from '$lib/stores/sse';

	const entryId = $derived(parseInt($page.params['id'] as string));

	$effect(() => {
		if (!isNaN(entryId)) lastViewedId.set(entryId);
	});

	let entry = $state<Entry | null>(null);
	let html = $state('');
	let vtEnabled = $state(false);
	let contentEl = $state<HTMLElement | null>(null);
	let readingFontSize = $state(17);

	$effect(() => {
		function handleKeydown(e: KeyboardEvent) {
			if (!contentEl) return;
			if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
			const step = 120;
			if (e.key === 'ArrowDown') { contentEl.scrollBy(0, step); e.preventDefault(); }
			else if (e.key === 'ArrowUp') { contentEl.scrollBy(0, -step); e.preventDefault(); }
			else if (e.key === 'PageDown') { contentEl.scrollBy(0, contentEl.clientHeight * 0.85); e.preventDefault(); }
			else if (e.key === 'PageUp') { contentEl.scrollBy(0, -contentEl.clientHeight * 0.85); e.preventDefault(); }
			else if (e.key === 'Home') { contentEl.scrollTo(0, 0); e.preventDefault(); }
			else if (e.key === 'End') { contentEl.scrollTo(0, contentEl.scrollHeight); e.preventDefault(); }
		}
		window.addEventListener('keydown', handleKeydown);
		return () => window.removeEventListener('keydown', handleKeydown);
	});

	let scanning = $state(false);
	let scanResult = $state<ScanResult | null>(null);
	let scanError = $state('');
	let error = $state('');

	let tagsOpen = $state(false);
	let newTagInput = $state('');
	let allTags = $state<Tag[]>([]);
	let tagsContainerEl = $state<HTMLElement | null>(null);
	let tagAddInputEl = $state<HTMLInputElement | null>(null);

	const tagSuggestions = $derived(
		newTagInput.length > 0
			? allTags
					.map((t) => t.name)
					.filter(
						(n) =>
							!entry?.tags.includes(n) && n.toLowerCase().includes(newTagInput.toLowerCase())
					)
					.slice(0, 6)
			: []
	);

	$effect(() => {
		if (!tagsOpen) return;
		function onPointerDown(e: PointerEvent) {
			if (tagsContainerEl && !tagsContainerEl.contains(e.target as Node)) {
				tagsOpen = false;
			}
		}
		document.addEventListener('pointerdown', onPointerDown, true);
		return () => document.removeEventListener('pointerdown', onPointerDown, true);
	});

	$effect(() => {
		if (tagsOpen && tagAddInputEl) tagAddInputEl.focus();
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
		configApi.get().then((cfg) => {
			vtEnabled = cfg.virustotal_enabled;
			readingFontSize = cfg.reading_font_size;
		}).catch(() => {});
	});

	// Re-fetch entry whenever the route param changes (same-component navigation).
	$effect(() => {
		const id = entryId;
		if (isNaN(id)) return;

		entry = null;
		html = '';
		error = '';
		scanning = false;
		scanResult = null;
		scanError = '';
		tagsOpen = false;

		let cancelled = false;

		entriesApi
			.get(id)
			.then((e) => {
				if (cancelled) return Promise.reject('cancelled');
				entry = e;
				ensureEntryTab(id, e.title);
				return readTextFile(e.file_path);
			})
			.then((source) => {
				if (cancelled) return;
				html = createRenderer(entry!.file_path)(source);
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
		const ok = await confirm(`Delete "${entry.title}"?`, { title: 'Confirm Delete', kind: 'warning' });
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
		if (entry.tags.includes(trimmed)) {
			newTagInput = '';
			return;
		}
		newTagInput = '';
		const addedEntry = await entriesApi.patch(entry.id, { tags: [...entry.tags, trimmed] });
		entry = addedEntry;
		lastChangedEntry.set(addedEntry);
		entryChangedTick.update((n) => n + 1);
	}

	async function removeTag(name: string) {
		if (!entry) return;
		const removedEntry = await entriesApi.patch(entry.id, { tags: entry.tags.filter((t) => t !== name) });
		entry = removedEntry;
		lastChangedEntry.set(removedEntry);
		entryChangedTick.update((n) => n + 1);
	}

	async function runScan() {
		if (!entry || scanning) return;
		scanning = true;
		scanResult = null;
		scanError = '';
		try {
			scanResult = await security.scan(entry.id);
		} catch (err) {
			scanError = err instanceof Error ? err.message : String(err);
		} finally {
			scanning = false;
		}
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

<div class="viewer">
	<div class="toolbar">
		<!-- Left: navigation -->
		<button class="btn-icon" onclick={() => closeTab(`viewer-${entryId}`)} title="Back">
			<CornerUpLeft size={18} />
		</button>
		{#if entry}
			<button class="btn-icon" onclick={() => goto(`/editor/${entry!.id}`)} title="Edit">
				<PenLine size={18} />
			</button>
			<button class="btn-icon" onclick={copyUrl} title="Copy URL">
				<Link size={18} />
			</button>
			<button class="btn-icon" class:active={entry.flags?.includes('archive')} onclick={() => toggleFlag('archive')} title="Archive">
				<Archive size={18} />
			</button>
			<button class="btn-icon" onclick={deleteEntry} title="Delete">
				<Shredder size={18} />
			</button>
			{#if vtEnabled}
				<button
					class="btn-icon"
					onclick={runScan}
					disabled={scanning}
					title={scanning ? 'Scanning…' : 'VirusTotal'}
				>
					<ShieldCheck size={18} />
				</button>
			{/if}
		{/if}

		<span class="spacer"></span>

		<!-- Center: font size controls -->
		<div class="font-controls">
			<button class="btn-icon" onclick={() => adjustFontSize(-1)} title="Decrease font size">
				<AArrowDown size={18} />
			</button>
			<span class="font-size-label">{readingFontSize}px</span>
			<button class="btn-icon" onclick={() => adjustFontSize(1)} title="Increase font size">
				<AArrowUp size={18} />
			</button>
		</div>

		<span class="spacer"></span>

		<!-- Right: status toggles + tags -->
		{#if entry}
			<div class="status-controls">
				<button
					class="btn-icon"
					class:active={entry.status === 'read'}
					onclick={() => setStatus('read')}
					title="Read"
				><Eye size={18} /></button>
				<button
					class="btn-icon"
					class:active={entry.status === 'unread'}
					onclick={() => setStatus('unread')}
					title="Unread"
				><EyeClosed size={18} /></button>
				<button
					class="btn-icon"
					class:active={entry.flags?.includes('bookmark')}
					onclick={() => toggleFlag('bookmark')}
					title="Bookmark"
				><Bookmark size={18} /></button>
				<button
					class="btn-icon"
					class:active={entry.flags?.includes('gem')}
					onclick={() => toggleFlag('gem')}
					title="Gem"
				><Gem size={18} /></button>
			</div>

			<div class="tags-container" bind:this={tagsContainerEl}>
				<button
					class="btn-icon"
					class:active={tagsOpen}
					onclick={() => { tagsOpen = !tagsOpen; if (tagsOpen) fetchAllTags(); }}
					title="Tags"
				>
					<BrainCircuit size={18} />
				</button>

				{#if tagsOpen}
					<div class="tags-panel">
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
								if (e.key === 'Enter') { e.preventDefault(); addTag(newTagInput); }
								else if (e.key === 'Escape') tagsOpen = false;
							}}
						/>
						{#if tagSuggestions.length > 0}
							<div class="tag-suggestions">
								{#each tagSuggestions as s (s)}
									<button class="suggestion-item" onclick={() => addTag(s)}>{s}</button>
								{/each}
							</div>
						{/if}
					</div>
				{/if}
			</div>
		{/if}
	</div>

	{#if scanResult}
		<div class="scan-result" class:danger={scanResult.malicious > 0}>
			VirusTotal: <strong>{scanResult.verdict}</strong> —
			{scanResult.malicious} malicious · {scanResult.suspicious} suspicious ·
			{scanResult.undetected} undetected / {scanResult.total} engines
		</div>
	{/if}

	{#if scanError}
		<div class="scan-result danger">{scanError}</div>
	{/if}

	{#if error}
		<div class="error-banner">{error}</div>
	{:else if entry && html}
		<div class="content" bind:this={contentEl}>
			<div class="content-inner">
				<h1 class="entry-title">{entry.title}</h1>
				<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
				<div class="markdown-body" onclick={handleContentClick}>
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

	.toolbar {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 6px;
		border-bottom: 1px solid var(--border);
		background: var(--bg-dark);
		flex-shrink: 0;
		min-height: 40px;
	}

	.spacer {
		flex: 1;
	}

	.font-controls {
		display: flex;
		align-items: center;
		gap: 2px;
	}

	.font-size-label {
		font-size: 0.72rem;
		color: var(--fg-muted);
		padding: 0 4px;
		min-width: 2.8rem;
		text-align: center;
	}

	.status-controls {
		display: flex;
		align-items: center;
		gap: 2px;
	}

	.btn-icon {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		padding: 0;
		background: none;
		border: 1px solid transparent;
		border-radius: 4px;
		color: var(--fg-muted);
		font-family: inherit;
		cursor: pointer;
		transition: color 0.15s, background 0.15s, border-color 0.15s;
		flex-shrink: 0;
	}

	.btn-icon:hover:not(:disabled) {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.btn-icon:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.btn-icon.active {
		color: var(--accent);
		border-color: var(--accent-dark);
		background: var(--bg-highlight);
	}

	.scan-result {
		padding: 0.4rem 1rem;
		font-size: 12px;
		background: var(--bg-alt);
		border-bottom: 1px solid var(--border);
		color: var(--green);
	}

	.scan-result.danger {
		color: var(--red);
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
	}

	.content-inner {
		max-width: 720px;
		margin: 0 auto;
		padding: 2rem;
	}

	.entry-title {
		font-size: 1.4rem;
		font-weight: 600;
		color: var(--red);
		margin: 0 0 1.5rem;
		line-height: 1.3;
	}

	.tags-container {
		position: relative;
	}

	.tags-panel {
		position: absolute;
		right: 0;
		top: calc(100% + 4px);
		width: 220px;
		background: var(--bg-dark);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 6px;
		z-index: 100;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
		display: flex;
		flex-direction: column;
		gap: 4px;
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
		font-size: 0.72rem;
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
		font-size: 0.78rem;
		cursor: pointer;
		text-align: left;
		transition: color 0.12s, background 0.12s;
	}

	.suggestion-item:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}
</style>
