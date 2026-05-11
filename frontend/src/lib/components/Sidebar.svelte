<script lang="ts">
	import { slide } from 'svelte/transition';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import {
		ChevronsRightLeft,
		ListChevronsDownUp,
		ListChevronsUpDown,
		SquareLibrary,
		ScanSearch,
		ClipboardPaste,
		ChevronRight,
		ChevronDown,
		Settings
	} from 'lucide-svelte';
	import { readText } from '@tauri-apps/plugin-clipboard-manager';
	import { entries as entriesApi, tags as tagsApi, extract as extractApi, type Entry, type Tag } from '$lib/api/client';
	import {
		sidebarCollapsed,
		sidebarWidth,
		libraryOpen,
		expandedSections,
		activeSection,
		selectedTag,
		searchOpen
	} from '$lib/stores/ui';
	import { entryAddedTick } from '$lib/stores/sse';

	const SECTIONS = [
		{ id: 'all', label: 'All' },
		{ id: 'unread', label: 'Unread' },
		{ id: 'read', label: 'Read' },
		{ id: 'favorite', label: 'Favorite' },
		{ id: 'recommend', label: 'Recommend' }
	] as const;

	type SectionId = (typeof SECTIONS)[number]['id'];

	let counts = $state<Record<string, number>>({});
	let sectionEntries = $state<Map<string, Entry[]>>(new Map());
	let tagList = $state<Tag[]>([]);
	let tagsExpanded = $state(false);

	type PasteStatus = 'idle' | 'loading' | 'ok' | 'error';
	let pasteStatus = $state<PasteStatus>('idle');
	let pasteMessage = $state('');

	async function fetchCounts() {
		const results = await Promise.allSettled(
			SECTIONS.map((s) =>
				entriesApi
					.list(s.id === 'all' ? {} : { status: s.id })
					.then((r) => [s.id, r.length] as [string, number])
			)
		);
		const next: Record<string, number> = {};
		for (const r of results) {
			if (r.status === 'fulfilled') next[r.value[0]] = r.value[1];
		}
		counts = next;
	}

	async function fetchSection(id: string) {
		const data = await entriesApi.list(id === 'all' ? {} : { status: id });
		sectionEntries = new Map(sectionEntries).set(id, data);
	}

	async function fetchTags() {
		try {
			tagList = await tagsApi.list();
		} catch {
			// sidecar may not be ready yet
		}
	}

	$effect(() => {
		fetchCounts();
		fetchTags();
	});

	let prevTick = 0;
	$effect(() => {
		const tick = $entryAddedTick;
		if (tick <= prevTick) return;
		prevTick = tick;
		fetchCounts();
		for (const id of $expandedSections) {
			if (id === 'tags') fetchTags();
			else fetchSection(id);
		}
	});

	function toggleCollapsed() {
		sidebarCollapsed.update((v) => !v);
	}

	function collapseAll() {
		expandedSections.set(new Set());
		tagsExpanded = false;
	}

	function expandAll() {
		const all = new Set(SECTIONS.map((s) => s.id));
		expandedSections.set(all);
		tagsExpanded = true;
		for (const s of SECTIONS) fetchSection(s.id);
		fetchTags();
	}

	function toggleLibrary() {
		if ($sidebarCollapsed) {
			sidebarCollapsed.set(false);
			libraryOpen.set(true);
		} else {
			libraryOpen.update((v) => !v);
		}
	}

	function openSearch() {
		searchOpen.set(true);
	}

	async function pasteUrl() {
		let url: string;
		try {
			url = (await readText()).trim();
		} catch {
			pasteStatus = 'error';
			pasteMessage = 'Could not read clipboard.';
			setTimeout(() => (pasteStatus = 'idle'), 3000);
			return;
		}

		if (!url.startsWith('http://') && !url.startsWith('https://')) {
			pasteStatus = 'error';
			pasteMessage = 'Clipboard is not a URL.';
			setTimeout(() => (pasteStatus = 'idle'), 3000);
			return;
		}

		pasteStatus = 'loading';
		try {
			await extractApi.url(url);
			pasteStatus = 'ok';
			pasteMessage = 'Saved.';
		} catch (e) {
			pasteStatus = 'error';
			pasteMessage = e instanceof Error ? e.message : 'Extraction failed.';
		}
		setTimeout(() => (pasteStatus = 'idle'), 3000);
	}

	function toggleSection(id: string) {
		expandedSections.update((set) => {
			const next = new Set(set);
			if (next.has(id)) {
				next.delete(id);
			} else {
				next.add(id);
				fetchSection(id);
			}
			return next;
		});
	}

	function toggleTags() {
		tagsExpanded = !tagsExpanded;
		if (tagsExpanded) fetchTags();
	}

	function selectSection(id: SectionId) {
		activeSection.set(id);
		selectedTag.set(null);
		toggleSection(id);
		if ($page.url.pathname !== '/') goto('/');
	}

	function selectTag(name: string) {
		selectedTag.update((current) => (current === name ? null : name));
		if ($page.url.pathname !== '/') goto('/');
	}

	function openEntry(id: number) {
		goto(`/viewer/${id}`);
	}

	const isSettingsActive = $derived($page.url.pathname.startsWith('/settings'));
</script>

<aside
	class="sidebar"
	class:collapsed={$sidebarCollapsed}
	style={$sidebarCollapsed ? '' : `width: ${$sidebarWidth}px`}
>
	<!-- Top toolbar -->
	<div class="toolbar">
		<button class="icon-btn" onclick={toggleCollapsed} title="Toggle sidebar">
			<ChevronsRightLeft size={15} />
		</button>
		{#if !$sidebarCollapsed}
			<button class="icon-btn" onclick={collapseAll} title="Collapse all sections">
				<ListChevronsDownUp size={15} />
			</button>
			<button class="icon-btn" onclick={expandAll} title="Expand all sections">
				<ListChevronsUpDown size={15} />
			</button>
		{/if}
	</div>

	<!-- Section type icons -->
	<div class="icon-row">
		<button
			class="icon-btn"
			class:active={$libraryOpen && !$sidebarCollapsed}
			onclick={toggleLibrary}
			title="Library"
		>
			<SquareLibrary size={18} />
		</button>

		{#if !$sidebarCollapsed}
			<button
				class="icon-btn paste-btn"
				class:paste-ok={pasteStatus === 'ok'}
				class:paste-err={pasteStatus === 'error'}
				class:paste-loading={pasteStatus === 'loading'}
				onclick={pasteUrl}
				title="Add URL from clipboard"
				disabled={pasteStatus === 'loading'}
			>
				<ClipboardPaste size={18} />
			</button>

			<button class="icon-btn" onclick={openSearch} title="Search (Ctrl+K)">
				<ScanSearch size={18} />
			</button>
		{/if}
	</div>

	{#if pasteStatus !== 'idle' && !$sidebarCollapsed}
		<div class="paste-feedback" class:is-ok={pasteStatus === 'ok'} class:is-err={pasteStatus === 'error'}>
			{pasteStatus === 'loading' ? 'Saving…' : pasteMessage}
		</div>
	{/if}

	<!-- Navigator (hidden when collapsed) -->
	{#if !$sidebarCollapsed && $libraryOpen}
		<nav class="nav" transition:slide={{ duration: 180 }}>
			{#each SECTIONS as section}
				<div class="section-row">
					<button
						class="chevron-btn"
						onclick={() => toggleSection(section.id)}
						title={$expandedSections.has(section.id) ? 'Collapse' : 'Expand'}
					>
						{#if $expandedSections.has(section.id)}
							<ChevronDown size={13} />
						{:else}
							<ChevronRight size={13} />
						{/if}
					</button>
					<button
						class="section-label"
						class:active={$activeSection === section.id}
						onclick={() => selectSection(section.id)}
					>
						<span class="label-text">{section.label}</span>
						{#if counts[section.id] !== undefined}
							<span class="count">{counts[section.id]}</span>
						{/if}
					</button>
				</div>

				{#if $expandedSections.has(section.id)}
					<div class="section-entries" transition:slide={{ duration: 140 }}>
						{#each sectionEntries.get(section.id) ?? [] as entry (entry.id)}
							<button
								class="entry-item"
								class:active-entry={$page.params['id'] === String(entry.id)}
								onclick={() => openEntry(entry.id)}
								title={entry.title}
							>
								{entry.title}
							</button>
						{:else}
							<span class="empty-section">No entries</span>
						{/each}
					</div>
				{/if}
			{/each}

			<!-- Tags section -->
			<div class="section-row">
				<button class="chevron-btn" onclick={toggleTags} title={tagsExpanded ? 'Collapse' : 'Expand'}>
					{#if tagsExpanded}
						<ChevronDown size={13} />
					{:else}
						<ChevronRight size={13} />
					{/if}
				</button>
				<button class="section-label" onclick={toggleTags}>
					<span class="label-text">Tags</span>
					{#if tagList.length > 0}
						<span class="count">{tagList.length}</span>
					{/if}
				</button>
			</div>

			{#if tagsExpanded}
				<div class="section-entries" transition:slide={{ duration: 140 }}>
					{#each tagList as tag (tag.name)}
						<button
							class="entry-item"
							class:active-entry={$selectedTag === tag.name}
							onclick={() => selectTag(tag.name)}
						>
							<span class="tag-name">{tag.name}</span>
							<span class="tag-count">{tag.count}</span>
						</button>
					{:else}
						<span class="empty-section">No tags</span>
					{/each}
				</div>
			{/if}
		</nav>
	{/if}

	<div class="sidebar-spacer"></div>

	<!-- Settings -->
	<div class="settings-row">
		<a
			href="/settings"
			class="settings-link"
			class:active={isSettingsActive}
			title="Settings"
		>
			<Settings size={18} />
			{#if !$sidebarCollapsed}
				<span class="settings-label">Settings</span>
			{/if}
		</a>
	</div>
</aside>

<style>
	.sidebar {
		width: 260px;
		flex-shrink: 0;
		background: var(--bg-dark);
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		overflow: hidden;
		transition: width 200ms ease;
	}

	.sidebar.collapsed {
		width: 44px;
		transition: none;
	}

	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 6px 6px 4px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.icon-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 6px 6px 4px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.icon-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		padding: 0;
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--fg-muted);
		cursor: pointer;
		transition: color 0.15s, background 0.15s;
		flex-shrink: 0;
	}

	.icon-btn:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.icon-btn.active {
		color: var(--accent);
	}

	.icon-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.paste-btn.paste-ok { color: #9ece6a; }
	.paste-btn.paste-err { color: var(--accent); }
	.paste-btn.paste-loading {
		color: var(--fg-muted);
		animation: pulse 1s ease-in-out infinite;
	}

	@keyframes pulse {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.4; }
	}

	.paste-feedback {
		padding: 3px 10px;
		font-size: 0.72rem;
		color: var(--fg-muted);
		background: var(--bg-highlight);
		border-bottom: 1px solid var(--border);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.paste-feedback.is-ok { color: #9ece6a; }
	.paste-feedback.is-err { color: var(--accent); }

	.nav {
		flex: 1;
		overflow-y: auto;
		padding: 4px 0;
	}

	.section-row {
		display: flex;
		align-items: center;
		padding: 0 4px;
	}

	.chevron-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 22px;
		height: 26px;
		padding: 0;
		background: none;
		border: none;
		border-radius: 3px;
		color: var(--fg-muted);
		cursor: pointer;
		flex-shrink: 0;
		transition: color 0.12s;
	}

	.chevron-btn:hover { color: var(--fg); }

	.section-label {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex: 1;
		min-width: 0;
		padding: 3px 6px;
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 0.82rem;
		font-weight: 600;
		cursor: pointer;
		text-align: left;
		letter-spacing: 0.01em;
		transition: color 0.12s, background 0.12s;
	}

	.section-label:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.section-label.active { color: var(--accent); }

	.label-text {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.count {
		font-size: 0.72rem;
		color: var(--fg-muted);
		margin-left: 6px;
		flex-shrink: 0;
	}

	.section-entries {
		padding: 2px 0 4px 22px;
	}

	.entry-item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		width: 100%;
		padding: 3px 8px;
		background: none;
		border: none;
		border-radius: 3px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 0.78rem;
		cursor: pointer;
		text-align: left;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		transition: color 0.12s, background 0.12s;
	}

	.entry-item:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.entry-item.active-entry {
		color: var(--accent);
		background: var(--bg-highlight);
	}

	.tag-name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.tag-count {
		font-size: 0.7rem;
		color: var(--fg-muted);
		margin-left: 6px;
		flex-shrink: 0;
	}

	.empty-section {
		display: block;
		padding: 3px 8px;
		font-size: 0.75rem;
		color: var(--fg-muted);
		font-style: italic;
	}

	.sidebar-spacer { flex: 1; }

	.settings-row {
		padding: 6px;
		border-top: 1px solid var(--border);
		flex-shrink: 0;
	}

	.settings-link {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 5px 6px;
		border-radius: 4px;
		color: var(--fg-muted);
		text-decoration: none;
		transition: color 0.15s, background 0.15s;
	}

	.settings-link:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.settings-link.active {
		color: var(--accent);
		background: var(--bg-highlight);
	}

	.settings-label {
		font-size: 0.82rem;
		font-weight: 600;
		white-space: nowrap;
		overflow: hidden;
	}
</style>
