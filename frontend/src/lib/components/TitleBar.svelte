<script lang="ts">
	import { onMount } from 'svelte';
	import { Minus, Square, X, ChevronsRightLeft, ChevronsDownUp, ChevronsUpDown, ScanSearch } from '@lucide/svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import {
		windowMinimize,
		windowMaximize,
		windowClose,
		windowStartMove,
		windowIsMaximized,
		onWindowMaximized
	} from '$lib/platform';
	import {
		sidebarCollapsed,
		sidebarWidth,
		searchOpen,
		expandedSections,
		tagsExpanded,
		expandAllSignal
	} from '$lib/stores/ui';

	let maximized = $state(false);

	function onTitlebarMouseDown(e: MouseEvent) {
		if (e.button !== 0) return;
		if ((e.target as HTMLElement).closest('button, [role="tab"]')) return;
		e.preventDefault();
		windowStartMove().catch(() => {});
	}

	function toggleSidebar() {
		sidebarCollapsed.update((v) => !v);
	}

	function collapseAll() {
		expandedSections.set(new Set());
		tagsExpanded.set(false);
	}

	function expandAll() {
		expandAllSignal.update((n) => n + 1);
	}

	function openSearch() {
		searchOpen.set(true);
	}

	onMount(() => {
		windowIsMaximized()
			.then((v) => {
				maximized = v;
			})
			.catch(() => {});
		return onWindowMaximized((v) => {
			maximized = v;
		});
	});
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="titlebar" onmousedown={onTitlebarMouseDown}>
	<div class="sidebar-controls" style:width={$sidebarCollapsed ? '44px' : `${$sidebarWidth}px`}>
		<button class="sc-btn" onclick={toggleSidebar} title="Toggle sidebar">
			<ChevronsRightLeft size={16} />
		</button>
		{#if !$sidebarCollapsed}
			<button class="sc-btn" onclick={collapseAll} title="Collapse all sections">
				<ChevronsDownUp size={16} />
			</button>
			<button class="sc-btn" onclick={expandAll} title="Expand all sections">
				<ChevronsUpDown size={16} />
			</button>
			<button class="sc-btn" onclick={openSearch} title="Search (Ctrl+K)">
				<ScanSearch size={16} />
			</button>
		{/if}
	</div>
	<div class="tabs-area">
		<TabBar />
	</div>
	<div class="window-controls">
		<button class="wc-btn" onclick={() => windowMinimize().catch(() => {})} title="Minimize">
			<Minus size={12} />
		</button>
		<button
			class="wc-btn"
			onclick={() => windowMaximize().catch(() => {})}
			title={maximized ? 'Restore' : 'Maximize'}
		>
			<Square size={12} />
		</button>
		<button class="wc-btn wc-close" onclick={() => windowClose().catch(() => {})} title="Close">
			<X size={12} />
		</button>
	</div>
</div>

<style>
	.titlebar {
		display: flex;
		align-items: stretch;
		background: var(--bg-dark);
		flex-shrink: 0;
		-webkit-app-region: no-drag;
	}

	.sidebar-controls {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 4px;
		border-right: 1px solid var(--border);
		flex-shrink: 0;
		overflow: hidden;
	}

	.sc-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--fg-muted);
		cursor: pointer;
		transition: background 0.12s, color 0.12s;
		flex-shrink: 0;
	}

	.sc-btn:hover {
		background: var(--bg-alt);
		color: var(--fg);
	}

	.tabs-area {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: stretch;
	}

	.window-controls {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 6px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.wc-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 28px;
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--fg-muted);
		cursor: pointer;
		transition: background 0.12s, color 0.12s;
		flex-shrink: 0;
	}

	.wc-btn:hover {
		background: var(--bg-alt);
		color: var(--fg);
	}

	.wc-close:hover {
		background: rgba(255, 117, 127, 0.15);
		color: var(--accent);
	}
</style>
