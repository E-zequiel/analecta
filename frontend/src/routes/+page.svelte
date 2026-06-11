<script lang="ts">
	import { onMount } from 'svelte';
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { confirm } from '$lib/platform';
	import {
		entries as entriesApi,
		tags as tagsApi,
		config as configApi,
		type Entry,
		type Tag,
		type SubgraphResult,
	} from '$lib/api/client';
	import {
		activeSection,
		selectedTag,
		lastViewedId,
		sidebarTagPreview,
		rightSidebarOpen,
		dashboardPreviewEntryId,
	} from '$lib/stores/ui';
	import { entryAddedTick, entryChangedTick } from '$lib/stores/sse';
	import { navigateInTab, navigateInSectionTab } from '$lib/stores/tabs';
	import EntryList from '$lib/components/EntryList.svelte';
	import SortBar from '$lib/components/SortBar.svelte';
	import LocalGraph from '$lib/components/LocalGraph.svelte';
	import VaultGraph from '$lib/components/VaultGraph.svelte';
	import { tooltip } from '$lib/actions/tooltip';

	let entryList = $state<Entry[]>([]);
	let loading = $state(false);
	let checking = $state(true);
	let sortBy = $state<'title' | 'created_at'>('created_at');
	let sortDir = $state<'asc' | 'desc'>('desc');

	// Tags dashboard state
	let tagGrid = $state<Tag[]>([]);
	let expandedTag = $state<string | null>(null);
	let tagEntries = $state<Entry[]>([]);
	let tagEntriesLoading = $state(false);
	let tagContextMenu = $state<{ x: number; y: number; tag: string } | null>(null);
	let tagContextMenuEl = $state<HTMLElement | null>(null);
	let editingTagName = $state<string | null>(null);
	let editingTagValue = $state('');

	// Collecta dashboard state
	let collectaMetrics = $state<{
		reads_week: number;
		reads_month: number;
		reads_year: number;
	} | null>(null);
	let collectaCounts = $state<Record<string, number>>({});
	let collectaTagGrid = $state<Tag[]>([]);
	let lastOpenedEntry = $state<Entry | null>(null);
	let collectaExpanded = $state<string | null>(null);
	let collectaEntries = $state<Entry[]>([]);
	let collectaLoading = $state(false);
	let collectaTagExpanded = $state<string | null>(null);
	let collectaTagEntries = $state<Entry[]>([]);

	// Dashboard graph state (shared across section + tags dashboards)
	let dashboardSelectedId = $state<number | null>(null);
	let dashboardSubgraph = $state<SubgraphResult | null>(null);
	let dashboardSubgraphLoading = $state(false);
	let graphColumnHeight = $state(200);
	const graphHeight = $derived(graphColumnHeight || 200);

	function selectDashboardEntry(id: number | null) {
		dashboardSelectedId = id;
		dashboardPreviewEntryId.set(id);
		if (id !== null) {
			sidebarTagPreview.set(null);
			rightSidebarOpen.set(true);
		}
	}

	const FLAG_SECTIONS = new Set(['bookmark', 'gem', 'archive']);

	const COLLECTA_GRID: Array<{ id: string; label: string }> = [
		{ id: 'unread', label: 'UNREAD' },
		{ id: 'read', label: 'READ' },
		{ id: 'bookmark', label: 'BOOKMARK' },
		{ id: 'gem', label: 'GEM' },
		{ id: 'archive', label: 'ARCHIVE' },
		{ id: 'tags', label: 'TAGS' },
	];

	function sectionListParams(
		section: string,
		tag: string | undefined,
		by: 'title' | 'created_at',
		dir: 'asc' | 'desc'
	) {
		const sort = { sort_by: by, sort_dir: dir };
		if (section === 'library') return { exclude_flag: 'archive', tag, ...sort };
		if (section === 'archive') return { flag: 'archive', tag, ...sort };
		if (FLAG_SECTIONS.has(section)) return { flag: section, exclude_flag: 'archive', tag, ...sort };
		// unread / read
		return { status: section, exclude_flag: 'archive', tag, ...sort };
	}

	// Fetch subgraph when an entry is selected in a dashboard
	$effect(() => {
		const id = dashboardSelectedId;
		dashboardSubgraph = null;
		if (id === null) return;
		dashboardSubgraphLoading = true;
		let cancelled = false;
		entriesApi
			.getSubgraph(id)
			.then((data) => {
				if (!cancelled) {
					dashboardSubgraph = data;
					dashboardSubgraphLoading = false;
				}
			})
			.catch(() => {
				if (!cancelled) dashboardSubgraphLoading = false;
			});
		return () => {
			cancelled = true;
		};
	});

	// Clear graph state when user navigates between sections
	$effect(() => {
		void $activeSection;
		selectDashboardEntry(null);
		dashboardSubgraph = null;
		sidebarTagPreview.set(null);
	});

	// Main list — active for all sections except tags and collecta
	$effect(() => {
		const section = $activeSection;
		if (section === 'tags' || section === 'collecta') return;
		const tag = $selectedTag ?? undefined;
		const params = sectionListParams(section, tag, sortBy, sortDir);

		let cancelled = false;
		loading = true;

		entriesApi
			.list(params)
			.then((data) => {
				if (!cancelled) {
					entryList = data;
					loading = false;
				}
			})
			.catch(() => {
				if (!cancelled) loading = false;
			});

		return () => {
			cancelled = true;
		};
	});

	// Tags dashboard — fetch tag grid when activeSection === 'tags'
	$effect(() => {
		if ($activeSection !== 'tags') return;
		tagsApi
			.list()
			.then((data) => {
				tagGrid = data;
			})
			.catch(() => {});
	});

	// Auto-expand a specific tag when navigating from the sidebar.
	$effect(() => {
		const tag = $selectedTag;
		if ($activeSection !== 'tags' || !tag) return;
		untrack(() => openTagEntries(tag));
	});

	// Collecta dashboard — parallel fetches when active
	$effect(() => {
		if ($activeSection !== 'collecta') return;
		const vid = $lastViewedId;
		entriesApi
			.getMetrics()
			.then((m) => {
				collectaMetrics = m;
			})
			.catch(() => {});
		entriesApi
			.getCounts()
			.then((c) => {
				collectaCounts = c;
			})
			.catch(() => {});
		tagsApi
			.list()
			.then((t) => {
				collectaTagGrid = t;
			})
			.catch(() => {});
		if (vid !== null) {
			entriesApi
				.get(vid)
				.then((e) => {
					lastOpenedEntry = e;
				})
				.catch(() => {});
		}
	});

	async function openTagEntries(name: string) {
		if (expandedTag === name) return;
		expandedTag = name;
		tagEntriesLoading = true;
		try {
			tagEntries = await entriesApi.list({ tag: name });
		} catch {
			tagEntries = [];
		} finally {
			tagEntriesLoading = false;
		}
	}

	async function toggleTagEntries(name: string) {
		if (expandedTag === name) {
			expandedTag = null;
			tagEntries = [];
			return;
		}
		await openTagEntries(name);
	}

	function focusAndSelect(node: HTMLInputElement) {
		node.focus();
		node.select();
	}

	$effect(() => {
		if (!tagContextMenu) return;
		function onPointerDown(e: PointerEvent) {
			if (tagContextMenuEl && !tagContextMenuEl.contains(e.target as Node)) tagContextMenu = null;
		}
		function onKeyDown(e: KeyboardEvent) {
			if (e.key === 'Escape') tagContextMenu = null;
		}
		document.addEventListener('pointerdown', onPointerDown, true);
		document.addEventListener('keydown', onKeyDown);
		return () => {
			document.removeEventListener('pointerdown', onPointerDown, true);
			document.removeEventListener('keydown', onKeyDown);
		};
	});

	async function refreshTagGrid() {
		try {
			tagGrid = await tagsApi.list();
		} catch {}
	}

	async function renameTagDashboard(oldName: string, newName: string) {
		editingTagName = null;
		const trimmed = newName.trim();
		if (!trimmed || trimmed === oldName) return;
		try {
			await tagsApi.rename(oldName, trimmed);
			if ($selectedTag === oldName) selectedTag.set(trimmed);
			if (expandedTag === oldName) expandedTag = trimmed;
			await refreshTagGrid();
			entryChangedTick.update((n) => n + 1);
		} catch {}
	}

	async function deleteTagDashboard(name: string) {
		tagContextMenu = null;
		const ok = await confirm(`Delete tag "#${name}"?`, 'Confirm Delete');
		if (!ok) return;
		try {
			await tagsApi.delete(name);
			if ($selectedTag === name) selectedTag.set(null);
			if (expandedTag === name) {
				expandedTag = null;
				tagEntries = [];
			}
			await refreshTagGrid();
			entryChangedTick.update((n) => n + 1);
		} catch {}
	}

	async function expandCollectaSection(id: string) {
		if (collectaExpanded === id) {
			collectaExpanded = null;
			collectaEntries = [];
			collectaTagExpanded = null;
			collectaTagEntries = [];
			return;
		}
		collectaExpanded = id;
		collectaTagExpanded = null;
		collectaTagEntries = [];
		if (id === 'tags') return; // tag grid already in collectaTagGrid
		collectaLoading = true;
		try {
			collectaEntries = await entriesApi.list(sectionListParams(id, undefined, sortBy, sortDir));
		} catch {
			collectaEntries = [];
		} finally {
			collectaLoading = false;
		}
	}

	async function expandCollectaTag(name: string) {
		if (collectaTagExpanded === name) {
			collectaTagExpanded = null;
			collectaTagEntries = [];
			return;
		}
		collectaTagExpanded = name;
		try {
			collectaTagEntries = await entriesApi.list({ tag: name });
		} catch {
			collectaTagEntries = [];
		}
	}

	function entryBadges(entry: Entry): Array<{ label: string; cls: string }> {
		const badges: Array<{ label: string; cls: string }> = [];
		if (entry.flags.includes('archive')) badges.push({ label: 'archived', cls: 'badge-archive' });
		if (entry.status === 'read') badges.push({ label: 'read', cls: 'badge-read' });
		if (entry.flags.includes('bookmark')) badges.push({ label: 'bookmark', cls: 'badge-bookmark' });
		if (entry.flags.includes('gem')) badges.push({ label: 'gem', cls: 'badge-gem' });
		return badges;
	}

	onMount(async () => {
		try {
			const cfg = await configApi.get();
			if (cfg.first_run) {
				goto('/first-run');
				return;
			}
		} catch {
			// if check fails, stay on dashboard
		}
		checking = false;
	});

	let prevAddedTick = 0;
	$effect(() => {
		const tick = $entryAddedTick;
		if (tick <= prevAddedTick) return;
		prevAddedTick = tick;
		const section = untrack(() => $activeSection);
		if (section === 'tags') {
			tagsApi
				.list()
				.then((d) => {
					tagGrid = d;
				})
				.catch(() => {});
			if (untrack(() => expandedTag)) {
				entriesApi
					.list({ tag: untrack(() => expandedTag)! })
					.then((d) => {
						tagEntries = d;
					})
					.catch(() => {});
			}
			return;
		}
		if (section === 'collecta') {
			entriesApi
				.getCounts()
				.then((c) => {
					collectaCounts = c;
				})
				.catch(() => {});
			tagsApi
				.list()
				.then((t) => {
					collectaTagGrid = t;
				})
				.catch(() => {});
			const expanded = untrack(() => collectaExpanded);
			if (expanded && expanded !== 'tags') {
				entriesApi
					.list(
						sectionListParams(
							expanded,
							undefined,
							untrack(() => sortBy),
							untrack(() => sortDir)
						)
					)
					.then((d) => {
						collectaEntries = d;
					})
					.catch(() => {});
			}
			return;
		}
		const tag = untrack(() => $selectedTag ?? undefined);
		const by = untrack(() => sortBy);
		const dir = untrack(() => sortDir);
		entriesApi
			.list(sectionListParams(section, tag, by, dir))
			.then((data) => {
				entryList = data;
			})
			.catch(() => {});
	});

	let prevChangedTick = 0;
	$effect(() => {
		const tick = $entryChangedTick;
		if (tick <= prevChangedTick) return;
		prevChangedTick = tick;
		const section = untrack(() => $activeSection);
		if (section === 'tags') {
			tagsApi
				.list()
				.then((d) => {
					tagGrid = d;
				})
				.catch(() => {});
			if (untrack(() => expandedTag)) {
				entriesApi
					.list({ tag: untrack(() => expandedTag)! })
					.then((d) => {
						tagEntries = d;
					})
					.catch(() => {});
			}
			return;
		}
		if (section === 'collecta') {
			entriesApi
				.getCounts()
				.then((c) => {
					collectaCounts = c;
				})
				.catch(() => {});
			tagsApi
				.list()
				.then((t) => {
					collectaTagGrid = t;
				})
				.catch(() => {});
			const expanded = untrack(() => collectaExpanded);
			if (expanded && expanded !== 'tags') {
				entriesApi
					.list(
						sectionListParams(
							expanded,
							undefined,
							untrack(() => sortBy),
							untrack(() => sortDir)
						)
					)
					.then((d) => {
						collectaEntries = d;
					})
					.catch(() => {});
			}
			return;
		}
		const tag = untrack(() => $selectedTag ?? undefined);
		const by = untrack(() => sortBy);
		const dir = untrack(() => sortDir);
		entriesApi
			.list(sectionListParams(section, tag, by, dir))
			.then((data) => {
				entryList = data;
			})
			.catch(() => {});
	});
</script>

{#if !checking}
	{#if $activeSection === 'collecta'}
		<div class="collecta-dashboard">
			<!-- Metrics bar -->
			<div class="collecta-metrics">
				<div class="metric-item">
					<span class="metric-label">Last opened</span>
					{#if lastOpenedEntry}
						<button
							class="metric-link"
							onclick={() =>
								navigateInTab(
									lastOpenedEntry!.id,
									lastOpenedEntry!.title,
									lastOpenedEntry!.source_type
								)}
						>
							{lastOpenedEntry.title}
						</button>
					{:else}
						<span class="metric-value">—</span>
					{/if}
				</div>
				<div class="metric-divider"></div>
				<div class="metric-item">
					<span class="metric-label">Reads</span>
					<span class="metric-value">
						{collectaMetrics?.reads_week ?? '—'} this week ·
						{collectaMetrics?.reads_month ?? '—'} this month ·
						{collectaMetrics?.reads_year ?? '—'} this year
					</span>
				</div>
			</div>

			<VaultGraph onopen={(id, title, sourceType) => navigateInTab(id, title, sourceType)} />

			<!-- Subdashboard grid -->
			<div class="collecta-grid">
				{#each COLLECTA_GRID as card (card.id)}
					<button
						class="collecta-card"
						class:active={collectaExpanded === card.id}
						onclick={() => expandCollectaSection(card.id)}
					>
						<span class="collecta-card-label">{card.label}</span>
						<span class="collecta-card-count">
							{#if card.id === 'tags'}
								{collectaTagGrid.length}
							{:else}
								{collectaCounts[card.id] ?? 0}
							{/if}
						</span>
					</button>
				{/each}
			</div>

			<!-- Expanded section content -->
			{#if collectaExpanded}
				<div class="collecta-expanded">
					<div class="collecta-expanded-header">
						<span class="collecta-expanded-title">
							{COLLECTA_GRID.find((c) => c.id === collectaExpanded)?.label}
						</span>
						<button
							class="collecta-nav-btn"
							onclick={() => navigateInSectionTab(collectaExpanded!)}
							use:tooltip={'Open as tab'}
						>
							Open ↗
						</button>
					</div>

					{#if collectaExpanded === 'tags'}
						<!-- Tag chip grid -->
						<div class="tag-grid">
							{#if collectaTagGrid.length === 0}
								<p class="hint">No tags yet.</p>
							{:else}
								{#each collectaTagGrid as tag (tag.name)}
									<button
										class="tag-chip"
										class:active={collectaTagExpanded === tag.name}
										onclick={() => expandCollectaTag(tag.name)}
									>
										<span class="tag-chip-name">#{tag.name}</span>
										<span class="tag-chip-count">{tag.count}</span>
									</button>
								{/each}
							{/if}
						</div>
						{#if collectaTagExpanded}
							<div class="collecta-tag-entries">
								<p class="collecta-tag-header">#{collectaTagExpanded}</p>
								{#each collectaTagEntries as entry (entry.id)}
									<button
										class="tag-entry-card"
										onclick={() => navigateInTab(entry.id, entry.title, entry.source_type)}
									>
										<span class="tag-entry-title">{entry.title}</span>
										<div class="tag-entry-badges">
											{#each entryBadges(entry) as badge (badge.cls)}
												<span class="badge {badge.cls}">{badge.label}</span>
											{/each}
										</div>
									</button>
								{/each}
							</div>
						{/if}
					{:else}
						<EntryList entries={collectaEntries} loading={collectaLoading} />
					{/if}
				</div>
			{/if}
		</div>
	{:else if $activeSection === 'tags'}
		<div class="tags-dashboard">
			<div class="tags-left">
				<div class="tag-grid">
					{#if tagGrid.length === 0}
						<p class="hint">No tags yet.</p>
					{:else}
						{#each tagGrid as tag (tag.name)}
							{#if editingTagName === tag.name}
								<input
									class="tag-chip-edit"
									type="text"
									bind:value={editingTagValue}
									use:focusAndSelect
									onkeydown={(e) => {
										if (e.key === 'Enter') {
											e.preventDefault();
											renameTagDashboard(tag.name, editingTagValue);
										} else if (e.key === 'Escape') {
											editingTagName = null;
										}
									}}
									onblur={() => renameTagDashboard(tag.name, editingTagValue)}
								/>
							{:else}
								<button
									class="tag-chip"
									class:active={expandedTag === tag.name}
									onclick={() => toggleTagEntries(tag.name)}
									oncontextmenu={(e) => {
										e.preventDefault();
										tagContextMenu = { x: e.clientX, y: e.clientY, tag: tag.name };
									}}
								>
									<span class="tag-chip-name">#{tag.name}</span>
									<span class="tag-chip-count">{tag.count}</span>
								</button>
							{/if}
						{/each}
					{/if}
				</div>

				{#if expandedTag}
					<div class="tag-entry-list">
						<p class="tag-entry-header">#{expandedTag}</p>
						{#if tagEntriesLoading}
							<p class="hint">Loading…</p>
						{:else if tagEntries.length === 0}
							<p class="hint">No entries.</p>
						{:else}
							{#each tagEntries as entry (entry.id)}
								<div
									class="tag-entry-card"
									role="button"
									tabindex="0"
									onclick={() => {
										selectDashboardEntry(entry.id);
									}}
									onkeydown={(e) => {
										if (e.key === 'Enter' || e.key === ' ') {
											e.preventDefault();
											selectDashboardEntry(entry.id);
										}
									}}
								>
									<div class="tag-entry-body">
										<span class="tag-entry-title">{entry.title}</span>
										<div class="tag-entry-badges">
											{#each entryBadges(entry) as badge (badge.cls)}
												<span class="badge {badge.cls}">{badge.label}</span>
											{/each}
										</div>
									</div>
									<button
										class="view-btn"
										onclick={(e) => {
											e.stopPropagation();
											navigateInTab(entry.id, entry.title, entry.source_type);
										}}
									>
										View ↗
									</button>
								</div>
							{/each}
						{/if}
					</div>
				{/if}
			</div>

			{#if dashboardSelectedId !== null}
				<div class="graph-column" bind:clientHeight={graphColumnHeight}>
					{#if dashboardSubgraphLoading}
						<p class="graph-hint">Loading…</p>
					{:else if dashboardSubgraph}
						<LocalGraph
							nodes={dashboardSubgraph.nodes}
							edges={dashboardSubgraph.edges}
							focusNodeId={$sidebarTagPreview ? undefined : dashboardSubgraph.focus_node_id}
							height={graphHeight}
							onopen={(id) => {
								selectDashboardEntry(id);
							}}
							ontagclick={(tagName) => {
								sidebarTagPreview.set(tagName);
								rightSidebarOpen.set(true);
							}}
						/>
					{/if}
				</div>
			{/if}
		</div>

		{#if tagContextMenu}
			<div
				class="tag-context-menu"
				style:left="{tagContextMenu.x}px"
				style:top="{tagContextMenu.y}px"
				bind:this={tagContextMenuEl}
				role="menu"
			>
				<button
					class="tag-menu-item"
					role="menuitem"
					onclick={() => {
						editingTagName = tagContextMenu!.tag;
						editingTagValue = tagContextMenu!.tag;
						tagContextMenu = null;
					}}
				>
					Rename
				</button>
				<div class="tag-menu-separator"></div>
				<button
					class="tag-menu-item danger"
					role="menuitem"
					onclick={() => deleteTagDashboard(tagContextMenu!.tag)}
				>
					Delete
				</button>
			</div>
		{/if}
	{:else}
		<div class="dashboard">
			<SortBar
				{sortBy}
				{sortDir}
				onsort={(by, dir) => {
					sortBy = by;
					sortDir = dir;
				}}
			/>
			<div class="dashboard-body">
				<div class="list-column">
					<EntryList
						entries={entryList}
						{loading}
						onitemclick={(entry) => {
							selectDashboardEntry(entry.id);
						}}
					/>
				</div>
				{#if dashboardSelectedId !== null}
					<div class="graph-column" bind:clientHeight={graphColumnHeight}>
						{#if dashboardSubgraphLoading}
							<p class="graph-hint">Loading…</p>
						{:else if dashboardSubgraph}
							<LocalGraph
								nodes={dashboardSubgraph.nodes}
								edges={dashboardSubgraph.edges}
								focusNodeId={$sidebarTagPreview ? undefined : dashboardSubgraph.focus_node_id}
								height={graphHeight}
								onopen={(id) => {
									selectDashboardEntry(id);
								}}
								ontagclick={(tagName) => {
									sidebarTagPreview.set(tagName);
									rightSidebarOpen.set(true);
								}}
							/>
						{/if}
					</div>
				{/if}
			</div>
		</div>
	{/if}
{/if}

<style>
	/* Section dashboard — two-column layout */
	.dashboard {
		display: flex;
		flex-direction: column;
		height: 100%;
		container-type: inline-size;
	}

	.dashboard-body {
		flex: 1;
		min-height: 0;
		display: flex;
		flex-wrap: nowrap;
		overflow: hidden;
	}

	.list-column {
		flex: 1 1 0;
		min-width: 0;
		min-height: 0;
		overflow-y: auto;
	}

	.graph-column {
		flex: 0 0 570px;
		min-height: 0;
		border-left: 1px solid var(--border);
		overflow: hidden;
		display: flex;
		flex-direction: column;
		justify-content: flex-start;
	}

	@container (max-width: 620px) {
		.dashboard-body {
			flex-direction: column-reverse;
		}

		.graph-column {
			flex: 0 0 325px;
			width: 100%;
			border-left: none;
			border-bottom: 1px solid var(--border);
		}

		.list-column {
			flex: 1 1 0;
			width: 100%;
		}
	}

	.graph-hint {
		padding: 1rem;
		font-size: 12px;
		color: var(--fg-muted);
		font-style: italic;
		text-align: center;
		margin: 0;
	}

	/* Tags dashboard */
	.tags-dashboard {
		display: flex;
		flex-direction: row;
		height: 100%;
		overflow: hidden;
	}

	.tags-left {
		flex: 1;
		min-width: 0;
		overflow-y: auto;
		padding: 1rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	/* Responsive stacking: graph above list when main is narrow.
	   Uses <main container-type: inline-size> as the query container. */
	@container (max-width: 620px) {
		.tags-dashboard {
			flex-direction: column-reverse;
		}

		.tags-dashboard .graph-column {
			flex: 0 0 325px;
			width: 100%;
			border-left: none;
			border-bottom: 1px solid var(--border);
		}

		.tags-left {
			flex: 1 1 0;
			width: 100%;
		}
	}

	.tag-grid {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		align-content: flex-start;
	}

	.tag-chip {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.3rem 0.7rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 20px;
		cursor: pointer;
		font-family: inherit;
		transition:
			border-color 0.15s,
			background 0.15s;
	}

	.tag-chip:hover {
		border-color: var(--accent-dark);
		background: var(--bg-highlight);
	}

	.tag-chip.active {
		border-color: var(--accent);
		background: var(--bg-alt);
	}

	.tag-chip-name {
		font-size: 12px;
		color: var(--fg);
	}

	.tag-chip-count {
		font-size: 13px;
		color: var(--fg-muted);
		background: var(--bg-highlight);
		border-radius: 10px;
		padding: 0 5px;
		min-width: 18px;
		text-align: center;
	}

	.tag-chip-edit {
		display: flex;
		align-items: center;
		padding: 0.3rem 0.7rem;
		background: var(--bg-alt);
		border: 1px solid var(--accent);
		border-radius: 20px;
		color: var(--fg);
		font-family: inherit;
		font-size: 12px;
		outline: none;
		min-width: 80px;
	}

	.tag-context-menu {
		position: fixed;
		z-index: 1000;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 3px;
		box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
		min-width: 140px;
	}

	.tag-menu-item {
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
		transition:
			background 0.1s,
			color 0.1s;
	}

	.tag-menu-item:hover {
		background: var(--bg-highlight);
		color: var(--accent);
	}

	.tag-menu-item.danger {
		color: var(--red);
	}

	.tag-menu-item.danger:hover {
		background: var(--bg-highlight);
		color: var(--red);
	}

	.tag-menu-separator {
		height: 1px;
		background: var(--border);
		margin: 3px 0;
	}

	.tag-entry-list {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.tag-entry-header {
		font-size: 13px;
		font-weight: 700;
		color: var(--accent);
		padding: 0.25rem 0;
		border-bottom: 1px solid var(--border);
		margin-bottom: 0.25rem;
	}

	.tag-entry-card {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		width: 100%;
		padding: 0.6rem 0.8rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 6px;
		cursor: pointer;
		text-align: left;
		font-family: inherit;
		transition:
			border-color 0.15s,
			background 0.15s;
	}

	.tag-entry-card:hover {
		background: var(--bg-highlight);
		border-color: var(--accent-dark);
	}

	.tag-entry-body {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}

	.tag-entry-title {
		font-size: 13px;
		font-weight: 700;
		color: var(--fg);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.tag-entry-badges {
		display: flex;
		gap: 0.3rem;
		flex-wrap: wrap;
	}

	.badge {
		font-size: 10px;
		padding: 1px 6px;
		border-radius: 10px;
		font-weight: 700;
	}

	.badge-archive {
		background: var(--bg-highlight);
		color: var(--fg-muted);
	}
	.badge-read {
		background: var(--bg-highlight);
		color: var(--yellow);
	}
	.badge-bookmark {
		background: var(--bg-highlight);
		color: var(--cyan);
	}
	.badge-gem {
		background: var(--bg-highlight);
		color: var(--magenta);
	}

	.view-btn {
		flex-shrink: 0;
		padding: 3px 10px;
		background: none;
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 0.72rem;
		cursor: pointer;
		white-space: nowrap;
		transition:
			color 0.12s,
			border-color 0.12s;
	}

	.view-btn:hover {
		color: var(--accent);
		border-color: var(--accent-dark);
	}

	.hint {
		padding: 1rem 0;
		color: var(--fg-muted);
		font-size: 13px;
	}

	/* Collecta dashboard */
	.collecta-dashboard {
		display: flex;
		flex-direction: column;
		height: 100%;
		overflow-y: auto;
		padding: 1rem;
		gap: 1rem;
	}

	.collecta-metrics {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.75rem 1rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 8px;
		flex-wrap: wrap;
	}

	.metric-item {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		min-width: 0;
	}

	.metric-label {
		font-size: 13px;
		font-weight: 700;
		color: var(--fg-muted);
		text-transform: uppercase;
		letter-spacing: 0.06em;
		white-space: nowrap;
		flex-shrink: 0;
	}

	.metric-value {
		font-size: 13px;
		color: var(--fg);
	}

	.metric-link {
		font-size: 13px;
		color: var(--accent);
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
		font-family: inherit;
		text-align: left;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 260px;
		transition: color 0.12s;
	}

	.metric-link:hover {
		color: var(--fg);
	}

	.metric-divider {
		width: 1px;
		height: 20px;
		background: var(--border);
		flex-shrink: 0;
	}

	.collecta-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0.5rem;
	}

	.collecta-card {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.75rem 1rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 8px;
		cursor: pointer;
		font-family: inherit;
		text-align: left;
		transition:
			border-color 0.15s,
			background 0.15s;
	}

	.collecta-card:hover {
		border-color: var(--accent-dark);
		background: var(--bg-highlight);
	}

	.collecta-card.active {
		border-color: var(--accent);
		background: var(--bg-alt);
	}

	.collecta-card-label {
		font-size: 12px;
		font-weight: 700;
		color: var(--fg);
		letter-spacing: 0.05em;
	}

	.collecta-card-count {
		font-size: 16px;
		font-weight: 700;
		color: var(--accent);
	}

	.collecta-expanded {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 1rem;
	}

	.collecta-expanded-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		border-bottom: 1px solid var(--border);
		padding-bottom: 0.5rem;
	}

	.collecta-expanded-title {
		font-size: 12px;
		font-weight: 700;
		color: var(--accent);
		letter-spacing: 0.05em;
	}

	.collecta-nav-btn {
		font-size: 13px;
		color: var(--fg-muted);
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
		font-family: inherit;
		transition: color 0.12s;
	}

	.collecta-nav-btn:hover {
		color: var(--accent);
	}

	.collecta-tag-entries {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		margin-top: 0.5rem;
	}

	.collecta-tag-header {
		font-size: 12px;
		font-weight: 700;
		color: var(--accent);
		padding: 0.2rem 0;
		border-bottom: 1px solid var(--border);
		margin-bottom: 0.2rem;
	}
</style>
