<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { openUrl } from '@tauri-apps/plugin-opener';
	import { confirm } from '@tauri-apps/plugin-dialog';
	import { readTextFile } from '@tauri-apps/plugin-fs';
	import {
		CornerUpLeft,
		PenLine,
		Link,
		Shredder,
		ShieldCheck,
		AArrowDown,
		AArrowUp,
		Eye,
		EyeClosed,
		Bookmark,
		Gem
	} from 'lucide-svelte';
	import {
		entries as entriesApi,
		config as configApi,
		security,
		type Entry,
		type ScanResult
	} from '$lib/api/client';
	import { createRenderer } from '$lib/markdown/renderer';
	import '$lib/markdown/tokyo-night.css';
	import { lastViewedId } from '$lib/stores/ui';
	import { entryChangedTick } from '$lib/stores/sse';

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

	onMount(async () => {
		try {
			const [e, cfg] = await Promise.all([entriesApi.get(entryId), configApi.get()]);
			entry = e;
			vtEnabled = cfg.virustotal_enabled;
			readingFontSize = cfg.reading_font_size;
			const source = await readTextFile(e.file_path);
			html = createRenderer(e.file_path)(source);
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	});

	async function setStatus(status: string) {
		if (!entry) return;
		const newStatus = entry.status === status ? 'unread' : status;
		if (newStatus === entry.status) return;
		entry = await entriesApi.patch(entry.id, { status: newStatus });
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
		goto('/');
	}

	function adjustFontSize(delta: number) {
		readingFontSize = Math.max(12, Math.min(24, readingFontSize + delta));
		document.documentElement.style.setProperty('--font-text-size', `${readingFontSize}px`);
		configApi.update({ reading_font_size: readingFontSize }).catch(() => {});
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
		<button class="btn-icon" onclick={() => goto('/')} title="Back">
			<CornerUpLeft size={16} />
		</button>
		{#if entry}
			<button class="btn-icon" onclick={() => goto(`/editor/${entry!.id}`)} title="Edit">
				<PenLine size={16} />
			</button>
			<button class="btn-icon" onclick={copyUrl} title="Copy URL">
				<Link size={16} />
			</button>
			<button class="btn-icon" onclick={deleteEntry} title="Delete">
				<Shredder size={16} />
			</button>
			{#if vtEnabled}
				<button
					class="btn-icon"
					onclick={runScan}
					disabled={scanning}
					title={scanning ? 'Scanning…' : 'VirusTotal'}
				>
					<ShieldCheck size={16} />
				</button>
			{/if}
		{/if}

		<span class="spacer"></span>

		<!-- Center: font size controls -->
		<div class="font-controls">
			<button class="btn-icon" onclick={() => adjustFontSize(-1)} title="Decrease font size">
				<AArrowDown size={16} />
			</button>
			<span class="font-size-label">{readingFontSize}px</span>
			<button class="btn-icon" onclick={() => adjustFontSize(1)} title="Increase font size">
				<AArrowUp size={16} />
			</button>
		</div>

		<span class="spacer"></span>

		<!-- Right: status toggles -->
		{#if entry}
			<div class="status-controls">
				<button
					class="btn-icon"
					class:active={entry.status === 'read'}
					onclick={() => setStatus('read')}
					title="Read"
				><Eye size={16} /></button>
				<button
					class="btn-icon"
					class:active={entry.status === 'unread'}
					onclick={() => setStatus('unread')}
					title="Unread"
				><EyeClosed size={16} /></button>
				<button
					class="btn-icon"
					class:active={entry.status === 'favorite'}
					onclick={() => setStatus('favorite')}
					title="Bookmark"
				><Bookmark size={16} /></button>
				<button
					class="btn-icon"
					class:active={entry.status === 'recommend'}
					onclick={() => setStatus('recommend')}
					title="Gem"
				><Gem size={16} /></button>
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
</style>
