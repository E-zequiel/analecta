<script lang="ts">
	import { slide } from 'svelte/transition';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import {
		ChevronsRightLeft,
		ChevronsDownUp,
		ChevronsUpDown,
		ScanSearch,
		ClipboardPaste,
		ChevronRight,
		ChevronDown,
		Settings,
		Library,
		EyeClosed,
		Eye,
		Bookmark,
		Gem,
		BrainCircuit,
		Origami,
		BookOpenText
	} from 'lucide-svelte';
	import { readText } from '@tauri-apps/plugin-clipboard-manager';
	import { entries as entriesApi, tags as tagsApi, extract as extractApi, type Entry, type Tag } from '$lib/api/client';
	import {
		sidebarCollapsed,
		sidebarWidth,
		expandedSections,
		activeSection,
		selectedTag,
		searchOpen,
		lastViewedId
	} from '$lib/stores/ui';
	import { entryAddedTick, entryChangedTick } from '$lib/stores/sse';

	const SECTIONS = [
		{ id: 'all',       label: 'LIBRARY',  icon: Library   },
		{ id: 'unread',    label: 'UNREAD',   icon: EyeClosed },
		{ id: 'read',      label: 'READ',     icon: Eye       },
		{ id: 'favorite',  label: 'BOOKMARK', icon: Bookmark  },
		{ id: 'recommend', label: 'GEM',      icon: Gem       }
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

	function refreshAll() {
		fetchCounts();
		for (const id of $expandedSections) {
			if (id === 'tags') fetchTags();
			else fetchSection(id);
		}
	}

	let prevAddedTick = 0;
	$effect(() => {
		const tick = $entryAddedTick;
		if (tick <= prevAddedTick) return;
		prevAddedTick = tick;
		refreshAll();
	});

	let prevChangedTick = 0;
	$effect(() => {
		const tick = $entryChangedTick;
		if (tick <= prevChangedTick) return;
		prevChangedTick = tick;
		refreshAll();
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

	function goLast() {
		if ($lastViewedId !== null) goto(`/viewer/${$lastViewedId}`);
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
			<ChevronsRightLeft size={18} />
		</button>
		{#if !$sidebarCollapsed}
			<button class="icon-btn" onclick={collapseAll} title="Collapse all sections">
				<ChevronsDownUp size={18} />
			</button>
			<button class="icon-btn" onclick={expandAll} title="Expand all sections">
				<ChevronsUpDown size={18} />
			</button>
			<button class="icon-btn search-btn" onclick={openSearch} title="Search (Ctrl+K)">
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
	{#if !$sidebarCollapsed}
		<nav class="nav" transition:slide={{ duration: 180 }}>
			{#each SECTIONS as section}
				{@const SectionIcon = section.icon}
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
						<SectionIcon size={18} />
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
					<BrainCircuit size={18} />
					<span class="label-text">TAGS</span>
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

	<!-- Bottom bar -->
	<div class="bottom-bar">
		<button class="icon-btn" onclick={() => goto('/')} title="Collectio">
			<Origami size={18} />
		</button>
		<button class="icon-btn" onclick={goLast} title="Last viewed" disabled={$lastViewedId === null}>
			<BookOpenText size={18} />
		</button>
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
		<a href="/settings" class="icon-btn settings-btn" class:active={isSettingsActive} title="Settings">
			<Settings size={18} />
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
		gap: 2px;
		padding: 6px 6px 4px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
		min-height: 40px;
	}

	.search-btn {
		margin-left: auto;
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
		text-decoration: none;
	}

	.icon-btn:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.icon-btn.active {
		color: var(--accent);
	}

	.icon-btn:disabled {
		opacity: 0.4;
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
		gap: 5px;
		flex: 1;
		min-width: 0;
		padding: 3px 6px;
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--fg-dark);
		font-family: inherit;
		font-size: 0.78rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		cursor: pointer;
		text-align: left;
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
		margin-left: auto;
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

	.bottom-bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 6px;
		border-top: 1px solid var(--border);
		flex-shrink: 0;
	}

	.sidebar.collapsed .bottom-bar {
		flex-direction: column;
		justify-content: center;
		gap: 4px;
		padding: 6px 0;
	}
</style>
