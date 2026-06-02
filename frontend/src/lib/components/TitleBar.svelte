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
		CornerUpLeft,
		PenLine,
		Link,
		Archive,
		Shredder,
		AArrowDown,
		AArrowUp,
		Eye,
		EyeClosed,
		Bookmark,
		Gem,
		BrainCircuit,
		RotateCcw,
		Save,
		BookOpenText,
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
		rightSidebarWidth,
	} from '$lib/stores/ui';
	import { activeEntryTitle } from '$lib/stores/tabs';
	import { page } from '$app/stores';
	import {
		viewerEntry,
		viewerFontSize,
		viewerTagsOpen,
		viewerActions,
		editorSaving,
		editorSaved,
		editorShowPreview,
		editorIsDirty,
		editorActions,
	} from '$lib/stores/toolbar';

	let maximized = $state(false);

	const toolbarMode = $derived(
		$page.url.pathname.startsWith('/viewer/')
			? 'viewer'
			: $page.url.pathname.startsWith('/editor/')
				? 'editor'
				: null
	);

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
	<div
		class="sidebar-controls"
		style:width={$sidebarCollapsed ? '44px' : `${$sidebarWidth}px`}
		style:justify-content={$sidebarCollapsed ? 'center' : 'space-between'}
		style:padding={$sidebarCollapsed ? '0' : '0 6px'}
	>
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

	<!-- CENTER: drag region / toolbar -->
	{#if toolbarMode === 'viewer' && $viewerActions !== null}
		<div class="toolbar-center viewer-toolbar">
			{#if $viewerEntry}
				<div class="tb-group">
					<button class="btn-icon" onclick={() => $viewerActions!.goBack()} title="Back">
						<CornerUpLeft size={16} />
					</button>
					<button class="btn-icon" onclick={() => $viewerActions!.goToEditor()} title="Edit">
						<PenLine size={16} />
					</button>
					<button class="btn-icon" onclick={() => $viewerActions!.copyUrl()} title="Copy URL">
						<Link size={16} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerEntry.flags?.includes('archive')}
						onclick={() => $viewerActions!.toggleFlag('archive')}
						title="Archive"
					>
						<Archive size={16} />
					</button>
					<button class="btn-icon" onclick={() => $viewerActions!.deleteEntry()} title="Delete">
						<Shredder size={16} />
					</button>
				</div>
				<div class="tb-group">
					<button class="btn-icon" onclick={() => $viewerActions!.adjustFont(-1)} title="Decrease font size">
						<AArrowDown size={16} />
					</button>
					<span class="font-size-label">{$viewerFontSize}px</span>
					<button class="btn-icon" onclick={() => $viewerActions!.adjustFont(1)} title="Increase font size">
						<AArrowUp size={16} />
					</button>
				</div>
				<div class="tb-group">
					<button
						class="btn-icon"
						class:active={$viewerEntry.status === 'read'}
						onclick={() => $viewerActions!.setStatus('read')}
						title="Read"
					>
						<Eye size={16} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerEntry.status === 'unread'}
						onclick={() => $viewerActions!.setStatus('unread')}
						title="Unread"
					>
						<EyeClosed size={16} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerEntry.flags?.includes('bookmark')}
						onclick={() => $viewerActions!.toggleFlag('bookmark')}
						title="Bookmark"
					>
						<Bookmark size={16} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerEntry.flags?.includes('gem')}
						onclick={() => $viewerActions!.toggleFlag('gem')}
						title="Gem"
					>
						<Gem size={16} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerTagsOpen}
						data-tags-toggle
						onclick={() => viewerTagsOpen.update((v) => !v)}
						title="Tags"
					>
						<BrainCircuit size={16} />
					</button>
				</div>
			{/if}
		</div>
	{:else if toolbarMode === 'editor' && $editorActions !== null}
		<div class="toolbar-center">
			<div class="tb-group">
				<button class="btn-icon" onclick={() => $editorActions!.goBack()} title="Back">
					<CornerUpLeft size={16} />
				</button>
				<button
					class="btn-icon"
					class:active={$editorShowPreview}
					onclick={() => $editorActions!.togglePreview()}
					title="Preview"
				>
					<BookOpenText size={16} />
				</button>
				<button
					class="btn-icon"
					class:active={$editorSaved}
					onclick={() => $editorActions!.save()}
					disabled={$editorSaving}
					title={$editorSaving ? 'Saving…' : $editorSaved ? 'Saved ✓' : 'Save'}
				>
					<Save size={16} />
				</button>
				<button
					class="btn-icon"
					onclick={() => $editorActions!.revert()}
					disabled={!$editorIsDirty}
					title="Revert"
				>
					<RotateCcw size={16} />
				</button>
			</div>
		</div>
	{:else}
		<div class="drag-region">
			{#if $activeEntryTitle}
				<span class="active-title">{$activeEntryTitle}</span>
			{/if}
		</div>
	{/if}

	<!-- RIGHT: PanelRight toggle + spacer + controles de ventana -->
	<div
		class="window-controls"
		style:width={$rightSidebarOpen ? `${$rightSidebarWidth}px` : undefined}
	>
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
		justify-content: flex-end;
		gap: 2px;
		padding: 0 6px;
		border-left: 1px solid var(--border);
		flex-shrink: 0;
	}

	.wc-spacer {
		flex: 1;
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
	}

	/* ── Center toolbar ── */
	.toolbar-center {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		padding: 0 8px;
	}

	.viewer-toolbar {
		justify-content: space-between;
		padding: 0 28px;
		gap: 0;
	}

	.tb-group {
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

	.btn-icon {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 26px;
		height: 26px;
		padding: 0;
		background: none;
		border: 1px solid transparent;
		border-radius: 4px;
		color: var(--fg-muted);
		cursor: pointer;
		transition:
			color 0.15s,
			background 0.15s,
			border-color 0.15s;
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
</style>
