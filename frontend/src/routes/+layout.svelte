<script lang="ts">
	import '../app.css';
	import { onMount, untrack } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import { getSidecarPort, onSidecarReady, onDeepLink, getInitialDeepLink, checkUpdate, onUpdateAvailable, notify } from '$lib/platform';
	import { port } from '$lib/stores/sidecar';
	import { entryAddedTick } from '$lib/stores/sse';
	import { sidebarCollapsed, sidebarWidth, searchOpen } from '$lib/stores/ui';
	import {
		tabs,
		activeTabId,
		openEntryTab,
		syncActiveTabFromPath,
		restoreTabsFromConfig,
		saveTabs
	} from '$lib/stores/tabs';
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
	let pendingUpdateVersion = $state<string | null>(null);
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

		getSidecarPort()
			.then((p) => { clearTimeout(timeout); port.set(p); })
			.catch(() => {});

		const unlistenSidecar = onSidecarReady((p) => {
			clearTimeout(timeout);
			port.set(p);
		});

		getInitialDeepLink().then((url) => {
			if (url) handleDeepLink(url);
		}).catch(() => {});

		const unlistenDeepLink = onDeepLink(handleDeepLink);

		const unlistenUpdate = onUpdateAvailable((info) => {
			const i = info as { version: string };
			pendingUpdateVersion = i.version;
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
			unlistenSidecar();
			unlistenDeepLink();
			unlistenUpdate();
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
		checkUpdate().catch(() => {});
	});

	$effect(() => {
		if ($port === null) return;
		configApi
			.get()
			.then(async (cfg) => {
				applyFont(cfg.font_variant, cfg.custom_font_path, cfg.ui_font_size, cfg.reading_font_size, cfg.theme, cfg.accent_color);
				await restoreTabsFromConfig(cfg.open_tab_ids, cfg.active_tab_id);
			})
			.catch(() => {});
	});

	let _saveTimer: ReturnType<typeof setTimeout> | null = null;
	$effect(() => {
		void $tabs;
		void $activeTabId;
		if (_saveTimer) clearTimeout(_saveTimer);
		_saveTimer = setTimeout(() => untrack(() => saveTabs()), 800);
		return () => {
			if (_saveTimer) clearTimeout(_saveTimer);
		};
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
					await notify('Analecta', 'New entry saved.');
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
	{#if pendingUpdateVersion}
		<UpdateBanner version={pendingUpdateVersion} />
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
