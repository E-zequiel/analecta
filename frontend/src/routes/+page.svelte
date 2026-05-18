<script lang="ts">
	import { onMount } from 'svelte';
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { exists } from '@tauri-apps/plugin-fs';
	import { entries as entriesApi, tags as tagsApi, config as configApi, type Entry, type Tag } from '$lib/api/client';
	import { activeSection, selectedTag } from '$lib/stores/ui';
	import { entryAddedTick, entryChangedTick } from '$lib/stores/sse';
	import { navigateInTab } from '$lib/stores/tabs';
	import EntryList from '$lib/components/EntryList.svelte';
	import SortBar from '$lib/components/SortBar.svelte';

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

	const FLAG_SECTIONS = new Set(['bookmark', 'gem', 'archive']);

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

	// Main list — active for all sections except tags
	$effect(() => {
		const section = $activeSection;
		if (section === 'tags') return;
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
		tagsApi.list().then((data) => {
			tagGrid = data;
		}).catch(() => {});
	});

	// Auto-expand a specific tag when navigating from the sidebar.
	// untrack prevents expandedTag/$state reads inside openTagEntries from
	// becoming effect dependencies — without it, writing expandedTag inside
	// the async call would re-trigger the effect creating an infinite loop.
	$effect(() => {
		const tag = $selectedTag;
		if ($activeSection !== 'tags' || !tag) return;
		untrack(() => openTagEntries(tag));
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
			const vaultExists = await exists(cfg.vault_path);
			if (!vaultExists) {
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
			tagsApi.list().then((d) => { tagGrid = d; }).catch(() => {});
			if (untrack(() => expandedTag)) {
				entriesApi.list({ tag: untrack(() => expandedTag)! })
					.then((d) => { tagEntries = d; }).catch(() => {});
			}
			return;
		}
		const tag = untrack(() => $selectedTag ?? undefined);
		const by = untrack(() => sortBy);
		const dir = untrack(() => sortDir);
		entriesApi
			.list(sectionListParams(section, tag, by, dir))
			.then((data) => { entryList = data; })
			.catch(() => {});
	});

	let prevChangedTick = 0;
	$effect(() => {
		const tick = $entryChangedTick;
		if (tick <= prevChangedTick) return;
		prevChangedTick = tick;
		const section = untrack(() => $activeSection);
		if (section === 'tags') {
			tagsApi.list().then((d) => { tagGrid = d; }).catch(() => {});
			if (untrack(() => expandedTag)) {
				entriesApi.list({ tag: untrack(() => expandedTag)! })
					.then((d) => { tagEntries = d; }).catch(() => {});
			}
			return;
		}
		const tag = untrack(() => $selectedTag ?? undefined);
		const by = untrack(() => sortBy);
		const dir = untrack(() => sortDir);
		entriesApi
			.list(sectionListParams(section, tag, by, dir))
			.then((data) => { entryList = data; })
			.catch(() => {});
	});
</script>

{#if !checking}
	{#if $activeSection === 'tags'}
		<div class="tags-dashboard">
			<div class="tag-grid">
				{#if tagGrid.length === 0}
					<p class="hint">No tags yet.</p>
				{:else}
					{#each tagGrid as tag (tag.name)}
						<button
							class="tag-chip"
							class:active={expandedTag === tag.name}
							onclick={() => toggleTagEntries(tag.name)}
						>
							<span class="tag-chip-name">#{tag.name}</span>
							<span class="tag-chip-count">{tag.count}</span>
						</button>
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
							<button
								class="tag-entry-card"
								onclick={() => navigateInTab(entry.id, entry.title)}
							>
								<span class="tag-entry-title">{entry.title}</span>
								<div class="tag-entry-badges">
									{#each entryBadges(entry) as badge}
										<span class="badge {badge.cls}">{badge.label}</span>
									{/each}
								</div>
							</button>
						{/each}
					{/if}
				</div>
			{/if}
		</div>
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
			<div class="list-wrap">
				<EntryList entries={entryList} {loading} />
			</div>
		</div>
	{/if}
{/if}

<style>
	.dashboard {
		display: flex;
		flex-direction: column;
		height: 100%;
	}

	.list-wrap {
		flex: 1;
		overflow-y: auto;
		padding: 0.75rem 1rem;
	}

	/* Tags dashboard */
	.tags-dashboard {
		display: flex;
		flex-direction: column;
		height: 100%;
		overflow-y: auto;
		padding: 1rem;
		gap: 1rem;
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
		transition: border-color 0.15s, background 0.15s;
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
		font-size: 11px;
		color: var(--fg-muted);
		background: var(--bg-highlight);
		border-radius: 10px;
		padding: 0 5px;
		min-width: 18px;
		text-align: center;
	}

	.tag-entry-list {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.tag-entry-header {
		font-size: 13px;
		font-weight: 600;
		color: var(--accent);
		padding: 0.25rem 0;
		border-bottom: 1px solid var(--border);
		margin-bottom: 0.25rem;
	}

	.tag-entry-card {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		width: 100%;
		padding: 0.6rem 0.8rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 6px;
		cursor: pointer;
		text-align: left;
		font-family: inherit;
		transition: border-color 0.15s, background 0.15s;
	}

	.tag-entry-card:hover {
		background: var(--bg-highlight);
		border-color: var(--accent-dark);
	}

	.tag-entry-title {
		font-size: 13px;
		font-weight: 600;
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
		font-weight: 600;
	}

	.badge-archive { background: var(--bg-highlight); color: var(--fg-muted); }
	.badge-read    { background: var(--bg-highlight); color: var(--yellow); }
	.badge-bookmark { background: var(--bg-highlight); color: var(--cyan); }
	.badge-gem     { background: var(--bg-highlight); color: var(--magenta); }

	.hint {
		padding: 1rem 0;
		color: var(--fg-muted);
		font-size: 13px;
	}
</style>
