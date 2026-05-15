<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import { invoke } from '@tauri-apps/api/core';
	import { listen } from '@tauri-apps/api/event';
	import { getCurrent, onOpenUrl } from '@tauri-apps/plugin-deep-link';
	import type { Update } from '@tauri-apps/plugin-updater';
	import { port } from '$lib/stores/sidecar';
	import { entryAddedTick } from '$lib/stores/sse';
	import { sidebarCollapsed, sidebarWidth, searchOpen } from '$lib/stores/ui';
	import { openEntryTab, syncActiveTabFromPath } from '$lib/stores/tabs';
	import { pkm, config as configApi } from '$lib/api/client';
	import { applyFont } from '$lib/font';
	import SidecarLoadingScreen from '$lib/components/SidecarLoadingScreen.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import SearchDialog from '$lib/components/SearchDialog.svelte';
	import ContextMenu from '$lib/components/ContextMenu.svelte';
	import UpdateBanner from '$lib/components/UpdateBanner.svelte';

	let { children } = $props();
	let timedOut = $state(false);
	let pendingDeepLink = $state<string | null>(null);
	let pendingUpdate = $state<Update | null>(null);
	let isResizing = $state(false);

	function startResize(e: PointerEvent) {
		if ($sidebarCollapsed) return;
		e.preventDefault();
		isResizing = true;
		const startX = e.clientX;
		const startWidth = $sidebarWidth;

		function onMove(ev: PointerEvent) {
			sidebarWidth.set(Math.max(160, Math.min(480, startWidth + ev.clientX - startX)));
		}
		function onUp() {
			isResizing = false;
			window.removeEventListener('pointermove', onMove);
			window.removeEventListener('pointerup', onUp);
		}
		window.addEventListener('pointermove', onMove);
		window.addEventListener('pointerup', onUp);
	}


	async function handleDeepLink(rawUrl: string) {
		pendingDeepLink = rawUrl;
	}

	onMount(() => {
		const timeout = setTimeout(() => {
			timedOut = true;
		}, 10_000);

		invoke<number | null>('get_sidecar_port').then((existingPort) => {
			if (existingPort !== null) {
				clearTimeout(timeout);
				port.set(existingPort);
			}
		});

		const unlistenSidecar = listen<{ port: number }>('sidecar-ready', (event) => {
			clearTimeout(timeout);
			port.set(event.payload.port);
		});

		getCurrent().then((urls) => {
			if (urls && urls.length > 0) handleDeepLink(urls[0]);
		});

		const unlistenDeepLink = onOpenUrl((urls) => {
			if (urls.length > 0) handleDeepLink(urls[0]);
		});

		const unlistenRelay = listen<string>('deep-link', (event) => {
			handleDeepLink(event.payload);
		});

		function handleKey(e: KeyboardEvent) {
			if (e.ctrlKey && e.key === 'b') {
				sidebarCollapsed.update((v) => !v);
				e.preventDefault();
			}
			if (e.ctrlKey && e.key === 'k') {
				searchOpen.set(true);
				e.preventDefault();
			}
		}
		window.addEventListener('keydown', handleKey);

		return () => {
			clearTimeout(timeout);
			unlistenSidecar.then((u) => u());
			unlistenDeepLink.then((u) => u());
			unlistenRelay.then((u) => u());
			window.removeEventListener('keydown', handleKey);
		};
	});

	afterNavigate(({ to }) => {
		if (to) syncActiveTabFromPath(to.url.pathname);
	});

	$effect(() => {
		const p = $port;
		const url = pendingDeepLink;
		if (p === null || url === null) return;

		pendingDeepLink = null;
		pkm.parseUrl(url).then((result) => {
			if (result.entry_id !== null) {
				openEntryTab(result.entry_id, `Entry #${result.entry_id}`);
			}
		});
	});

	$effect(() => {
		if ($port === null) return;
		import('@tauri-apps/plugin-updater')
			.then(({ check }) => check())
			.then((update) => {
				if (update?.available) pendingUpdate = update;
			})
			.catch(() => {});
	});

	$effect(() => {
		if ($port === null) return;
		configApi
			.get()
			.then((cfg) => {
				applyFont(cfg.font_variant, cfg.custom_font_path, cfg.ui_font_size, cfg.reading_font_size, cfg.theme, cfg.accent_color);
			})
			.catch(() => {});
	});

	$effect(() => {
		const p = $port;
		if (p === null) return;

		const source = new EventSource(`http://localhost:${p}/api/v1/system/events`);
		source.addEventListener('message', async (ev) => {
			try {
				const data = JSON.parse(ev.data) as { type: string };
				if (data.type === 'entry_added') {
					entryAddedTick.update((n) => n + 1);
					await invoke('notify_success', { title: 'Analecta', body: 'New entry saved.' });
				}
			} catch {
				// ignore malformed events
			}
		});

		return () => source.close();
	});
</script>

{#if $port === null}
	<SidecarLoadingScreen {timedOut} />
{:else}
	{#if pendingUpdate}
		<UpdateBanner update={pendingUpdate} />
	{/if}
	<div class="shell" class:resizing={isResizing}>
		<Sidebar />
		{#if !$sidebarCollapsed}
			<div
				class="resize-handle"
				role="separator"
				aria-orientation="vertical"
				aria-label="Resize sidebar"
				onpointerdown={startResize}
			></div>
		{/if}
		<main>
			<TabBar />
			{@render children()}
		</main>
	</div>
	<SearchDialog />
	<ContextMenu />
{/if}

<style>
	.shell {
		display: flex;
		height: 100vh;
		overflow: hidden;
	}

	.shell.resizing {
		cursor: col-resize;
		user-select: none;
	}

	.resize-handle {
		width: 4px;
		flex-shrink: 0;
		background: transparent;
		cursor: col-resize;
		transition: background 0.15s;
		outline: none;
	}

	.resize-handle:hover,
	.shell.resizing .resize-handle {
		background: var(--accent);
		opacity: 0.45;
	}

	main {
		flex: 1;
		overflow-y: auto;
		background: var(--bg);
	}
</style>
