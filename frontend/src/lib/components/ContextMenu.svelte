<script lang="ts">
	import { revealItemInDir } from '@tauri-apps/plugin-opener';
	import { confirm } from '@tauri-apps/plugin-dialog';
	import { entries as entriesApi } from '$lib/api/client';
	import { contextMenu, hideContextMenu } from '$lib/stores/contextMenu';
	import { closeTab } from '$lib/stores/tabs';
	import { entryChangedTick, lastChangedEntry } from '$lib/stores/sse';

	let menuEl = $state<HTMLElement | null>(null);

	$effect(() => {
		if (!$contextMenu.visible) return;
		function onPointerDown(e: PointerEvent) {
			if (menuEl && !menuEl.contains(e.target as Node)) hideContextMenu();
		}
		function onKeyDown(e: KeyboardEvent) {
			if (e.key === 'Escape') hideContextMenu();
		}
		document.addEventListener('pointerdown', onPointerDown, true);
		document.addEventListener('keydown', onKeyDown);
		return () => {
			document.removeEventListener('pointerdown', onPointerDown, true);
			document.removeEventListener('keydown', onKeyDown);
		};
	});

	async function revealFile() {
		if ($contextMenu.entry) {
			await revealItemInDir($contextMenu.entry.file_path).catch(() => {});
		}
		hideContextMenu();
	}

	async function archiveEntry() {
		const entry = $contextMenu.entry;
		hideContextMenu();
		if (!entry) return;
		const current = entry.flags ?? [];
		// Archiving strips all other flags (bookmark, gem); unarchiving restores empty flags
		const newFlags = current.includes('archive') ? [] : ['archive'];
		const updated = await entriesApi.patch(entry.id, { flags: newFlags });
		lastChangedEntry.set(updated);
		entryChangedTick.update((n) => n + 1);
	}

	async function deleteEntry() {
		const entry = $contextMenu.entry;
		hideContextMenu();
		if (!entry) return;
		const ok = await confirm(`Delete "${entry.title}"?`, { title: 'Confirm Delete', kind: 'warning' });
		if (!ok) return;
		await entriesApi.delete(entry.id);
		entryChangedTick.update((n) => n + 1);
		closeTab(`viewer-${entry.id}`);
	}
</script>

{#if $contextMenu.visible && $contextMenu.entry}
	<div
		class="context-menu"
		style="left: {$contextMenu.x}px; top: {$contextMenu.y}px"
		bind:this={menuEl}
		role="menu"
	>
		<button class="menu-item" onclick={revealFile} role="menuitem">
			Show in system explorer
		</button>
		<div class="separator"></div>
		<button class="menu-item" onclick={archiveEntry} role="menuitem">
			{$contextMenu.entry?.flags?.includes('archive') ? 'Unarchive' : 'Archive'}
		</button>
		<div class="separator"></div>
		<button class="menu-item danger" onclick={deleteEntry} role="menuitem">
			Delete
		</button>
	</div>
{/if}

<style>
	.context-menu {
		position: fixed;
		z-index: 1000;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 3px;
		box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
		min-width: 180px;
	}

	.menu-item {
		display: block;
		width: 100%;
		padding: 6px 10px;
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--fg);
		font-family: inherit;
		font-size: 0.82rem;
		text-align: left;
		cursor: pointer;
		transition: background 0.1s, color 0.1s;
	}

	.menu-item:hover {
		background: var(--bg-highlight);
		color: var(--accent);
	}

	.menu-item.danger {
		color: var(--red);
	}

	.menu-item.danger:hover {
		background: var(--bg-highlight);
		color: var(--red);
	}

	.separator {
		height: 1px;
		background: var(--border);
		margin: 3px 0;
	}
</style>
