<script lang="ts">
	import { onMount } from 'svelte';
	import {
		ChevronDown,
		Maximize2,
		Minimize2,
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
		Cable,
		RotateCcw,
		Save,
		BookOpenText,
		Library,
		Origami,
		Waypoints,
		PanelRight,
	} from '@lucide/svelte';
	import {
		windowMinimize,
		windowMaximize,
		windowClose,
		windowStartMove,
		windowIsMaximized,
		onWindowMaximized,
	} from '$lib/platform';
	import { tooltip } from '$lib/actions/tooltip';
	import {
		sidebarCollapsed,
		sidebarWidth,
		searchOpen,
		expandedSections,
		activeSection,
		expandAllSignal,
		rightSidebarOpen,
		rightSidebarWidth,
	} from '$lib/stores/ui';
	import { activeEntryTitle } from '$lib/stores/tabs';
	import { page } from '$app/state';
	import {
		viewerEntry,
		viewerFontSize,
		viewerTagsOpen,
		viewerBacklinksOpen,
		viewerActions,
		editorSaving,
		editorSaved,
		editorShowPreview,
		editorIsDirty,
		editorActions,
	} from '$lib/stores/toolbar';

	const SECTION_ICONS = {
		library: Library,
		unread: EyeClosed,
		read: Eye,
		bookmark: Bookmark,
		gem: Gem,
		archive: Archive,
		tags: BrainCircuit,
		collecta: Origami,
		backlinks: Waypoints,
	} as const;

	const SectionIcon = $derived(
		$activeSection in SECTION_ICONS
			? SECTION_ICONS[$activeSection as keyof typeof SECTION_ICONS]
			: null
	);

	let maximized = $state(false);

	const toolbarMode = $derived(
		page.url.pathname.startsWith('/viewer/')
			? 'viewer'
			: page.url.pathname.startsWith('/editor/')
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
		<button
			class="sc-btn"
			onclick={toggleSidebar}
			use:tooltip={'Toggle sidebar'}
			aria-label="Toggle sidebar"
		>
			<ChevronsRightLeft size={18} />
		</button>
		{#if !$sidebarCollapsed}
			<button
				class="sc-btn"
				onclick={collapseAll}
				use:tooltip={'Collapse all sections'}
				aria-label="Collapse all sections"
			>
				<ChevronsDownUp size={18} />
			</button>
			<button
				class="sc-btn"
				onclick={expandAll}
				use:tooltip={'Expand all sections'}
				aria-label="Expand all sections"
			>
				<ChevronsUpDown size={18} />
			</button>
			<button class="sc-btn" onclick={openSearch} use:tooltip={'Search'} aria-label="Search">
				<ScanSearch size={18} />
			</button>
		{/if}
	</div>

	<!-- CENTER: drag region / toolbar -->
	{#if toolbarMode === 'viewer' && $viewerActions !== null}
		<div class="toolbar-center viewer-toolbar">
			{#if $viewerEntry}
				<div class="tb-group">
					<button
						class="btn-icon"
						onclick={() => $viewerActions!.goBack()}
						use:tooltip={'Back'}
						aria-label="Back"
					>
						<CornerUpLeft size={18} />
					</button>
					<button
						class="btn-icon"
						onclick={() => $viewerActions!.goToEditor()}
						use:tooltip={'Edit'}
						aria-label="Edit"
					>
						<PenLine size={18} />
					</button>
					<button
						class="btn-icon"
						onclick={() => $viewerActions!.copyUrl()}
						use:tooltip={'Copy Analecta deep link'}
						aria-label="Copy Analecta deep link"
					>
						<Link size={18} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerEntry.flags?.includes('archive')}
						onclick={() => $viewerActions!.toggleFlag('archive')}
						use:tooltip={'Archive'}
						aria-label="Archive"
					>
						<Archive size={18} />
					</button>
					<button
						class="btn-icon"
						onclick={() => $viewerActions!.deleteEntry()}
						use:tooltip={'Delete'}
						aria-label="Delete"
					>
						<Shredder size={18} />
					</button>
				</div>
				<div class="tb-group">
					<button
						class="btn-icon"
						onclick={() => $viewerActions!.adjustFont(-1)}
						use:tooltip={'Decrease font size'}
						aria-label="Decrease font size"
					>
						<AArrowDown size={18} />
					</button>
					<span class="font-size-label">{$viewerFontSize}px</span>
					<button
						class="btn-icon"
						onclick={() => $viewerActions!.adjustFont(1)}
						use:tooltip={'Increase font size'}
						aria-label="Increase font size"
					>
						<AArrowUp size={18} />
					</button>
				</div>
				<div class="tb-group">
					<button
						class="btn-icon"
						class:active={$viewerEntry.status === 'read'}
						onclick={() => $viewerActions!.setStatus('read')}
						use:tooltip={'Read'}
						aria-label="Read"
					>
						<Eye size={18} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerEntry.status === 'unread'}
						onclick={() => $viewerActions!.setStatus('unread')}
						use:tooltip={'Unread'}
						aria-label="Unread"
					>
						<EyeClosed size={18} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerEntry.flags?.includes('bookmark')}
						onclick={() => $viewerActions!.toggleFlag('bookmark')}
						use:tooltip={'Bookmark'}
						aria-label="Bookmark"
					>
						<Bookmark size={18} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerEntry.flags?.includes('gem')}
						onclick={() => $viewerActions!.toggleFlag('gem')}
						use:tooltip={'Gem'}
						aria-label="Gem"
					>
						<Gem size={18} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerTagsOpen}
						data-tags-toggle
						onclick={() => viewerTagsOpen.update((v) => !v)}
						use:tooltip={'Tags'}
						aria-label="Tags"
					>
						<BrainCircuit size={18} />
					</button>
					<button
						class="btn-icon"
						class:active={$viewerBacklinksOpen}
						onclick={() => viewerBacklinksOpen.update((v) => !v)}
						use:tooltip={'Connections'}
						aria-label="Connections"
					>
						<Cable size={18} />
					</button>
				</div>
			{/if}
		</div>
	{:else if toolbarMode === 'editor' && $editorActions !== null}
		<div class="toolbar-center">
			<button
				class="btn-icon"
				onclick={() => $editorActions!.goBack()}
				use:tooltip={'Back'}
				aria-label="Back"
			>
				<CornerUpLeft size={18} />
			</button>
			<button
				class="btn-icon"
				class:active={$editorShowPreview}
				onclick={() => $editorActions!.togglePreview()}
				use:tooltip={'Preview'}
				aria-label="Preview"
			>
				<BookOpenText size={18} />
			</button>
			<button
				class="btn-icon"
				class:active={$editorSaved}
				onclick={() => $editorActions!.save()}
				disabled={$editorSaving}
				use:tooltip={$editorSaving ? 'Saving…' : $editorSaved ? 'Saved ✓' : 'Save'}
				aria-label={$editorSaving ? 'Saving…' : $editorSaved ? 'Saved ✓' : 'Save'}
			>
				<Save size={18} />
			</button>
			<button
				class="btn-icon"
				onclick={() => $editorActions!.revert()}
				disabled={!$editorIsDirty}
				use:tooltip={'Revert'}
				aria-label="Revert"
			>
				<RotateCcw size={18} />
			</button>
		</div>
	{:else}
		<div class="drag-region">
			{#if $activeEntryTitle}
				<div class="section-label">
					{#if SectionIcon}
						<SectionIcon size={13} />
					{/if}
					<span class="active-title">{$activeEntryTitle}</span>
				</div>
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
			use:tooltip={'Toggle entry stack'}
			aria-label="Toggle entry stack"
		>
			<PanelRight size={18} />
		</button>

		<div class="wc-spacer"></div>

		<button
			class="wc-btn"
			onclick={() => windowMinimize().catch(() => {})}
			use:tooltip={'Hide'}
			aria-label="Hide"
		>
			<ChevronDown size={15} />
		</button>
		<button
			class="wc-btn"
			onclick={() => windowMaximize().catch(() => {})}
			use:tooltip={maximized ? 'Minimize' : 'Maximize'}
			aria-label={maximized ? 'Minimize' : 'Maximize'}
		>
			{#if maximized}
				<Minimize2 size={15} />
			{:else}
				<Maximize2 size={15} />
			{/if}
		</button>
		<button
			class="wc-btn wc-close"
			onclick={() => windowClose().catch(() => {})}
			use:tooltip={'Close'}
			aria-label="Close"
		>
			<X size={15} />
		</button>
	</div>
</div>

<style>
	.titlebar {
		min-height: 40px;
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

	.section-label {
		display: flex;
		align-items: center;
		gap: 6px;
		color: var(--fg-muted);
		min-width: 0;
		max-width: 28.24rem;
	}

	.active-title {
		font-size: var(--font-size-sublabel);
		color: var(--fg-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-family: var(--font-ui-family);
	}

	/* ── Right ── */
	.window-controls {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 4px;
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
		width: 28px;
		height: 26px;
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
		gap: 8px;
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
		gap: 4px;
	}

	.font-size-label {
		font-size: var(--font-size-sublabel);
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
