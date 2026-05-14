<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { openUrl, openPath } from '@tauri-apps/plugin-opener';
	import { readTextFile } from '@tauri-apps/plugin-fs';
	import { entries as entriesApi, config as configApi, security, type Entry, type ScanResult } from '$lib/api/client';
	import { createRenderer } from '$lib/markdown/renderer';
	import '$lib/markdown/tokyo-night.css';

	const entryId = $derived(parseInt($page.params['id'] as string));

	let entry = $state<Entry | null>(null);
	let html = $state('');
	let vtEnabled = $state(false);
	let scanning = $state(false);
	let scanResult = $state<ScanResult | null>(null);
	let scanError = $state('');
	let error = $state('');

	onMount(async () => {
		try {
			const [e, cfg] = await Promise.all([entriesApi.get(entryId), configApi.get()]);
			entry = e;
			vtEnabled = cfg.virustotal_enabled;
			const source = await readTextFile(e.file_path);
			html = createRenderer(e.file_path)(source);
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	});

	async function setStatus(status: string) {
		if (!entry) return;
		const newStatus = entry.status === status ? 'unread' : status;
		entry = await entriesApi.patch(entry.id, { status: newStatus });
	}

	async function copyUrl() {
		if (!entry) return;
		await navigator.clipboard.writeText(`analecta://open?id=${entry.id}`);
	}

	async function openInBrowser() {
		if (!entry) return;
		await openUrl(entry.url);
	}

	async function openFiles() {
		if (!entry) return;
		const dir = entry.file_path.substring(0, entry.file_path.lastIndexOf('/'));
		await openPath(dir);
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
		<button class="btn-icon" onclick={() => goto('/')}>← Back</button>

		{#if entry}
			<button class="btn-icon" onclick={() => goto(`/editor/${entry!.id}`)}>Edit</button>
			<button class="btn-icon" onclick={copyUrl}>Copy URL</button>
			<button class="btn-icon" onclick={openInBrowser}>Open</button>
			<button class="btn-icon" onclick={openFiles}>Files</button>
			{#if vtEnabled}
				<button class="btn-icon" onclick={runScan} disabled={scanning}>
					{scanning ? 'Scanning…' : 'VirusTotal'}
				</button>
			{/if}

			<span class="spacer"></span>

			<button
				class="btn-toggle"
				class:active={entry.status === 'read'}
				onclick={() => setStatus('read')}
			>Read</button>
			<button
				class="btn-toggle"
				class:active={entry.status === 'favorite'}
				onclick={() => setStatus('favorite')}
			>Favorite</button>
			<button
				class="btn-toggle"
				class:active={entry.status === 'recommend'}
				onclick={() => setStatus('recommend')}
			>Recommend</button>
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
		<div class="content">
			<h1 class="entry-title">{entry.title}</h1>
			<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
			<div class="markdown-body" onclick={handleContentClick}>
				{@html html}
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
		gap: 0.25rem;
		padding: 0.5rem 1rem;
		border-bottom: 1px solid var(--border);
		background: var(--bg-dark);
		flex-shrink: 0;
	}

	.spacer {
		flex: 1;
	}

	.btn-icon,
	.btn-toggle {
		padding: 0.3rem 0.6rem;
		background: none;
		border: 1px solid transparent;
		border-radius: 4px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 12px;
		cursor: pointer;
		transition: color 0.15s, background 0.15s, border-color 0.15s;
		white-space: nowrap;
	}

	.btn-icon:hover:not(:disabled),
	.btn-toggle:hover:not(:disabled) {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.btn-icon:disabled,
	.btn-toggle:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.btn-toggle.active {
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
		padding: 2rem;
		max-width: 860px;
	}

	.entry-title {
		font-size: 1.4rem;
		font-weight: 600;
		color: var(--red);
		margin: 0 0 1.5rem;
		line-height: 1.3;
	}
</style>
