<script lang="ts">
	import { onMount } from 'svelte';
	import {
		Minus,
		Square,
		X,
		ChevronsRightLeft,
		ChevronsDownUp,
		ChevronsUpDown,
		ScanSearch,
	} from '@lucide/svelte';
	import {
		windowMinimize,
		windowMaximize,
		windowClose,
		windowStartMove,
		windowIsMaximized,
		onWindowMaximized,
	} from '$lib/platform';
	import {
		sidebarCollapsed,
		sidebarWidth,
		searchOpen,
		expandedSections,
		tagsExpanded,
		expandAllSignal,
		rightSidebarOpen,
	} from '$lib/stores/ui';
	import { activeEntryTitle } from '$lib/stores/tabs';

	let maximized = $state(false);

	function onTitlebarMouseDown(e: MouseEvent) {
		if (e.button !== 0) return;
		if ((e.target as HTMLElement).closest('button')) return;
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
	function toggleRightSidebar() {
		rightSidebarOpen.update((v) => !v);
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
	<!-- LEFT: sidebar controls -->
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

	<!-- CENTER: drag region + título de entrada activa -->
	<div class="drag-region">
		{#if $activeEntryTitle}
			<span class="active-title">{$activeEntryTitle}</span>
		{/if}
	</div>

	<!-- RIGHT: PanelRight toggle + spacer + controles de ventana -->
	<div class="window-controls">
		<button
			class="wc-btn panel-toggle"
			class:active={$rightSidebarOpen}
			onclick={toggleRightSidebar}
			title="Toggle entry stack"
		>
			<svg
				width="14"
				height="14"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="1.5"
				stroke-linecap="round"
				stroke-linejoin="round"
			>
				<rect width="18" height="18" x="3" y="3" rx="2" />
				<path d="M15 3v18" />
			</svg>
		</button>

		<div class="wc-spacer"></div>

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
		height: 40px;
		display: flex;
		align-items: stretch;
		background: var(--bg-dark);
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
		-webkit-app-region: no-drag;
	}

	/* ── Left ── */
	.sidebar-controls {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 4px;
		border-right: 1px solid var(--border);
		flex-shrink: 0;
		overflow: hidden;
		transition: width 0.2s ease;
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
		transition:
			background 0.12s,
			color 0.12s;
		flex-shrink: 0;
	}
	.sc-btn:hover {
		background: var(--bg-alt);
		color: var(--fg);
	}

	/* ── Center ── */
	.drag-region {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0 16px;
		-webkit-app-region: drag;
		cursor: default;
		user-select: none;
	}

	.active-title {
		font-size: 13px;
		color: var(--fg-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 480px;
		font-family: var(--font-ui-family);
	}

	/* ── Right ── */
	.window-controls {
		display: flex;
		align-items: center;
		gap: 0;
		padding: 0 4px 0 0;
		border-left: 1px solid var(--border);
		flex-shrink: 0;
	}

	.wc-spacer {
		width: 6px;
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
		transition:
			background 0.12s,
			color 0.12s;
		flex-shrink: 0;
	}
	.wc-btn:hover {
		background: var(--bg-alt);
		color: var(--fg);
	}
	.wc-btn.active {
		color: var(--accent);
	}
	.wc-close:hover {
		background: rgba(255, 117, 127, 0.15);
		color: var(--red);
	}

	.panel-toggle {
		width: 28px;
		margin-right: 2px;
	}
</style>
