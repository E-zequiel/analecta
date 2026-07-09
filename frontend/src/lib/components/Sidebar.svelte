<script lang="ts">
	import { untrack } from 'svelte';
	import { slide, fade } from 'svelte/transition';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import {
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
		BookOpenText,
		Plus,
		Pencil,
		Trash2,
		Archive,
		TriangleAlert,
		X,
	} from '@lucide/svelte';
	import { writable } from 'svelte/store';
	import { SvelteMap } from 'svelte/reactivity';
	import { clipboardReadText } from '$lib/platform';
	import {
		entries as entriesApi,
		tags as tagsApi,
		extract as extractApi,
		ApiError,
		type Entry,
		type Tag,
	} from '$lib/api/client';
	import {
		sidebarCollapsed,
		sidebarWidth,
		expandedSections,
		activeSection,
		selectedTag,
		lastViewedId,
		expandAllSignal,
		pasteUrlSignal,
		dashboardPreviewEntryId,
		preSettingsState,
		pendingScrollRestore,
	} from '$lib/stores/ui';
	import { viewerEntry } from '$lib/stores/toolbar';
	import { navigateInTab, navigateInSectionTab, openEntryTab } from '$lib/stores/tabs';
	import { showContextMenu } from '$lib/stores/contextMenu';
	import { entryAddedTick, entryChangedTick, lastChangedEntry } from '$lib/stores/sse';
	import { tooltip } from '$lib/actions/tooltip';
	import { flash } from '$lib/actions/flash';

	const SECTIONS = [
		{ id: 'library', label: 'LIBRARY', icon: Library },
		{ id: 'unread', label: 'UNREAD', icon: EyeClosed },
		{ id: 'read', label: 'READ', icon: Eye },
		{ id: 'bookmark', label: 'BOOKMARK', icon: Bookmark },
		{ id: 'gem', label: 'GEM', icon: Gem },
		{ id: 'archive', label: 'ARCHIVE', icon: Archive },
	] as const;

	type SectionId = (typeof SECTIONS)[number]['id'];

	function sectionParams(id: string): Parameters<typeof entriesApi.list>[0] {
		if (id === 'archive') return { flag: 'archive' };
		if (id === 'bookmark' || id === 'gem') return { flag: id, exclude_flag: 'archive' };
		if (id === 'library') return { exclude_flag: 'archive' };
		return { status: id, exclude_flag: 'archive' };
	}

	let counts = $state<Record<string, number>>({});
	const sectionEntries = new SvelteMap<string, Entry[]>();
	let tagList = $state<Tag[]>([]);

	type PasteStatus = 'idle' | 'loading' | 'ok' | 'error';
	let pasteStatus = $state<PasteStatus>('idle');
	let pasteMessage = $state('');

	const urlInputActive = writable(false);
	let urlInputValue = $state('');
	let urlInputEl = $state<HTMLInputElement | null>(null);

	// When a dashboard entry is selected, show only its tags; reading view uses viewerEntry
	let dashboardEntry = $state<Entry | null>(null);

	$effect(() => {
		const id = $dashboardPreviewEntryId;
		dashboardEntry = null;
		if (id === null) return;
		let cancelled = false;
		entriesApi
			.get(id)
			.then((e) => {
				if (!cancelled) dashboardEntry = e;
			})
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	});

	// Dashboard selection takes priority; fall back to viewer entry in reading view
	const activeDisplayEntry = $derived(
		$dashboardPreviewEntryId !== null ? dashboardEntry : $viewerEntry
	);
	const currentEntryTagSet = $derived(
		new Set([...(activeDisplayEntry?.tags ?? []), ...(activeDisplayEntry?.content_tags ?? [])])
	);
	const displayTagList = $derived(
		activeDisplayEntry && $activeSection !== 'collecta'
			? tagList.filter((t) => currentEntryTagSet.has(t.name))
			: tagList
	);

	// Sections the currently-previewed entry belongs to (for membership indicator)
	const entryMatchSections = $derived.by(() => {
		const e = dashboardEntry;
		const ids = e ? SECTIONS.filter((s) => entryBelongsToSection(e, s.id)).map((s) => s.id) : [];
		return new Set(ids);
	});

	// Auto-expand matching sections and fetch their entries when an entry is selected.
	// untrack() is required: fetchSection reads fetchGen (SvelteMap) synchronously, which
	// would otherwise be tracked as a reactive dependency and cause an infinite loop.
	$effect(() => {
		const matchIds = [...entryMatchSections];
		if (matchIds.length === 0) return;
		untrack(() => {
			expandedSections.update((set) => {
				// eslint-disable-next-line svelte/prefer-svelte-reactivity -- immutable-update pattern inside store updater, not reactive state
				const next = new Set(set);
				for (const id of matchIds) next.add(id);
				return next;
			});
			for (const id of matchIds) fetchSection(id);
		});
	});

	let newTagExpanded = $state(false);
	let newTagName = $state('');
	let newTagInputEl = $state<HTMLInputElement | null>(null);
	let createError = $state<string | null>(null);
	let editingTag = $state<string | null>(null);
	let editTagValue = $state('');
	let editTagInputEl = $state<HTMLInputElement | null>(null);
	let renameError = $state<string | null>(null);
	let deletingTag = $state<string | null>(null);
	let deletingTagBodyCount = $state(0);
	let deleteRowEl = $state<HTMLElement | null>(null);
	let mergingTag = $state<{ oldName: string; newName: string } | null>(null);
	let mergeError = $state<string | null>(null);
	let mergeRowEl = $state<HTMLElement | null>(null);

	$effect(() => {
		if (newTagExpanded && newTagInputEl) newTagInputEl.focus();
	});

	$effect(() => {
		if (editingTag && editTagInputEl) editTagInputEl.focus();
	});

	$effect(() => {
		if (!editingTag) return;
		function onKeyDown(e: KeyboardEvent) {
			if (e.key === 'Escape') {
				editingTag = null;
				renameError = null;
			}
		}
		window.addEventListener('keydown', onKeyDown);
		return () => window.removeEventListener('keydown', onKeyDown);
	});

	$effect(() => {
		if (!deletingTag) return;
		function onKeyDown(e: KeyboardEvent) {
			if (e.key === 'Escape') deletingTag = null;
		}
		function onMouseDown(e: MouseEvent) {
			if (deleteRowEl && !deleteRowEl.contains(e.target as Node)) deletingTag = null;
		}
		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('mousedown', onMouseDown);
		return () => {
			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('mousedown', onMouseDown);
		};
	});

	$effect(() => {
		if (!mergingTag) return;
		function onKeyDown(e: KeyboardEvent) {
			if (e.key === 'Escape') {
				mergingTag = null;
				mergeError = null;
			}
		}
		function onMouseDown(e: MouseEvent) {
			if (mergeRowEl && !mergeRowEl.contains(e.target as Node)) {
				mergingTag = null;
				mergeError = null;
			}
		}
		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('mousedown', onMouseDown);
		return () => {
			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('mousedown', onMouseDown);
		};
	});

	async function fetchCounts() {
		try {
			counts = await entriesApi.getCounts();
		} catch {
			// sidecar may not be ready
		}
	}

	const fetchGen = new SvelteMap<string, number>();

	async function fetchSection(id: string) {
		const gen = (fetchGen.get(id) ?? 0) + 1;
		fetchGen.set(id, gen);
		const data = await entriesApi.list(sectionParams(id));
		if (fetchGen.get(id) === gen) {
			sectionEntries.set(id, data);
		}
	}

	function entryBelongsToSection(e: Entry, sectionId: string): boolean {
		const isArchived = e.flags.includes('archive');
		if (sectionId === 'archive') return isArchived;
		if (isArchived) return false;
		if (sectionId === 'library') return true;
		if (sectionId === 'bookmark' || sectionId === 'gem') return e.flags.includes(sectionId);
		return e.status === sectionId;
	}

	$effect(() => {
		const changed = $lastChangedEntry;
		if (!changed) return;
		untrack(() => {
			for (const [sectionId, entries] of sectionEntries) {
				const idx = entries.findIndex((e) => e.id === changed.id);
				const shouldBeHere = entryBelongsToSection(changed, sectionId);
				if (idx !== -1 && !shouldBeHere) {
					sectionEntries.set(
						sectionId,
						entries.filter((e) => e.id !== changed.id)
					);
				} else if (idx === -1 && shouldBeHere) {
					sectionEntries.set(sectionId, [changed, ...entries]);
				} else if (idx !== -1 && shouldBeHere) {
					const updated = [...entries];
					updated[idx] = changed;
					sectionEntries.set(sectionId, updated);
				}
			}
		});
	});

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
		fetchTags();
		const ids = untrack(() => [...sectionEntries.keys()]);
		for (const id of ids) {
			fetchSection(id);
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

	function expandAll() {
		expandedSections.set(new Set(SECTIONS.map((s) => s.id)));
		for (const s of SECTIONS) fetchSection(s.id);
		fetchTags();
	}

	let prevExpandSignal = 0;
	$effect(() => {
		const n = $expandAllSignal;
		if (n > prevExpandSignal) {
			prevExpandSignal = n;
			expandAll();
		}
	});

	let prevPasteUrlSignal = 0;
	$effect(() => {
		const n = $pasteUrlSignal;
		if (n > prevPasteUrlSignal) {
			prevPasteUrlSignal = n;
			pasteUrl();
		}
	});

	async function submitUrl() {
		const url = urlInputValue.trim();
		urlInputActive.set(false);
		urlInputValue = '';
		if (!url.startsWith('http://') && !url.startsWith('https://')) {
			pasteStatus = 'error';
			pasteMessage = 'Not a valid URL.';
			setTimeout(() => (pasteStatus = 'idle'), 3000);
			return;
		}
		pasteStatus = 'loading';
		try {
			await extractApi.url(url);
			pasteStatus = 'ok';
			pasteMessage = 'Saved.';
			setTimeout(() => (pasteStatus = 'idle'), 3000);
		} catch (e) {
			pasteStatus = 'error';
			pasteMessage = e instanceof Error ? e.message : 'Extraction failed.';
			setTimeout(() => (pasteStatus = 'idle'), 9_000);
		}
	}

	async function pasteUrl() {
		let url: string;
		try {
			url = (await clipboardReadText()).trim();
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
			setTimeout(() => (pasteStatus = 'idle'), 3000);
		} catch (e) {
			pasteStatus = 'error';
			pasteMessage = e instanceof Error ? e.message : 'Extraction failed.';
			setTimeout(() => (pasteStatus = 'idle'), 9_000);
		}
	}

	async function createTag() {
		const name = newTagName.trim();
		if (!name) {
			newTagExpanded = false;
			createError = null;
			return;
		}
		createError = null;
		try {
			await tagsApi.create(name);
			newTagName = '';
			newTagExpanded = false;
			await fetchTags();
		} catch (e) {
			// e.g. name isn't a valid hashtag token — keep the input open
			// and surface the backend's own message instead of failing
			// silently.
			createError = e instanceof ApiError ? e.message : 'Create failed.';
		}
	}

	async function renameTag(oldName: string, newName: string) {
		const trimmed = newName.trim();
		if (!trimmed || trimmed === oldName) {
			editingTag = null;
			renameError = null;
			return;
		}

		// A collision with another *existing* tag is a merge, not a plain
		// rename — require explicit confirmation before it's irreversible.
		const collision = tagList.find(
			(t) =>
				t.name.toLowerCase() === trimmed.toLowerCase() &&
				t.name.toLowerCase() !== oldName.toLowerCase()
		);
		if (collision) {
			// Show/send the destination's actual canonical casing, not
			// whatever was typed — that's what the merge will actually do.
			editingTag = null;
			renameError = null;
			mergeError = null;
			mergingTag = { oldName, newName: collision.name };
			return;
		}

		renameError = null;
		try {
			const updated = await tagsApi.rename(oldName, trimmed);
			editingTag = null;
			if ($selectedTag === oldName) selectedTag.set(updated.name);
			await fetchTags();
			entryChangedTick.update((n) => n + 1);
		} catch (e) {
			// e.g. body text can't migrate to the new name — keep the
			// input open and surface the backend's own message instead
			// of failing silently.
			renameError = e instanceof ApiError ? e.message : 'Rename failed.';
		}
	}

	async function confirmMergeTag() {
		if (!mergingTag) return;
		const { oldName, newName } = mergingTag;
		mergeError = null;
		try {
			const updated = await tagsApi.rename(oldName, newName, true);
			mergingTag = null;
			if ($selectedTag === oldName) selectedTag.set(updated.name);
			await fetchTags();
			entryChangedTick.update((n) => n + 1);
		} catch (e) {
			// Merge rejected (e.g. body text can't migrate to the
			// destination's name) — keep the row open and surface the
			// backend's own message instead of failing silently.
			mergeError = e instanceof ApiError ? e.message : 'Merge failed.';
		}
	}

	async function requestDeleteTag(name: string) {
		deletingTag = name;
		deletingTagBodyCount = 0;
		try {
			const { count } = await tagsApi.bodyCount(name);
			if (deletingTag === name) deletingTagBodyCount = count;
		} catch {
			// ignore — dialog just won't show the body-text warning
		}
	}

	async function deleteTag(name: string) {
		try {
			await tagsApi.delete(name);
			if ($selectedTag === name) selectedTag.set(null);
			await fetchTags();
			entryChangedTick.update((n) => n + 1);
		} catch {
			// ignore
		}
	}

	function toggleSection(id: string) {
		expandedSections.update((set) => {
			// eslint-disable-next-line svelte/prefer-svelte-reactivity -- immutable-update pattern inside store updater, not reactive state
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

	function selectSection(id: SectionId) {
		selectedTag.set(null);
		navigateInSectionTab(id);
	}

	function selectTag(name: string) {
		if ($viewerEntry) {
			selectedTag.set($selectedTag === name ? null : name);
		} else {
			selectedTag.set(name);
			navigateInSectionTab('tags');
		}
	}

	function openEntry(id: number, title: string) {
		navigateInTab(id, title);
	}

	function goLast() {
		if ($lastViewedId !== null) navigateInTab($lastViewedId, `Entry #${$lastViewedId}`);
	}

	const isSettingsActive = $derived(page.url.pathname.startsWith('/settings'));

	function handleSettingsClick() {
		if (isSettingsActive) {
			const state = $preSettingsState;
			preSettingsState.set(null);
			const path = state?.path ?? '/';
			if (state?.path.startsWith('/viewer/') && (state.scrollTop ?? 0) > 0) {
				pendingScrollRestore.set(state.scrollTop);
			}
			void goto(path);
		} else {
			const scrollTop = document.querySelector<HTMLElement>('.content')?.scrollTop ?? 0;
			preSettingsState.set({ path: page.url.pathname, scrollTop });
			void goto('/settings');
		}
	}
</script>

<aside
	class="sidebar"
	class:collapsed={$sidebarCollapsed}
	style={$sidebarCollapsed ? '' : `width: ${$sidebarWidth}px`}
>
	{#if pasteStatus !== 'idle' && !$sidebarCollapsed}
		<div
			class="paste-feedback"
			class:is-ok={pasteStatus === 'ok'}
			class:is-err={pasteStatus === 'error'}
		>
			{pasteStatus === 'loading' ? 'Saving…' : pasteMessage}
		</div>
	{/if}

	<!-- Navigator (hidden when collapsed) -->
	{#if !$sidebarCollapsed}
		<nav class="nav" transition:slide={{ duration: 180 }}>
			{#each SECTIONS as section (section.id)}
				{@const SectionIcon = section.icon}
				{@const isEntrySection = entryMatchSections.has(section.id)}
				<div class="section-row" class:entry-match={isEntrySection}>
					<button
						class="chevron-btn"
						onclick={() => toggleSection(section.id)}
						use:tooltip={$expandedSections.has(section.id) ? 'Collapse' : 'Expand'}
						aria-label={$expandedSections.has(section.id) ? 'Collapse' : 'Expand'}
					>
						{#if $expandedSections.has(section.id)}
							<ChevronDown size={18} />
						{:else}
							<ChevronRight size={18} />
						{/if}
					</button>
					<button
						class="section-label"
						class:active={$activeSection === section.id}
						onclick={() => selectSection(section.id)}
						use:flash={isEntrySection ? dashboardEntry?.id : undefined}
					>
						<SectionIcon size={18} />
						<span class="label-text">{section.label}</span>
						{#if counts[section.id] !== undefined}
							<span class="count">{counts[section.id]}</span>
						{/if}
					</button>
				</div>

				{#if $expandedSections.has(section.id)}
					<div class="section-entries" transition:fade={{ duration: 120 }}>
						{#each sectionEntries.get(section.id) ?? [] as entry (entry.id)}
							{@const isMatchEntry = dashboardEntry?.id === entry.id}
							<button
								class="entry-item"
								class:active-entry={page.params['id'] === String(entry.id)}
								class:entry-match-item={isMatchEntry}
								onclick={() => openEntry(entry.id, entry.title)}
								onmousedown={(e) => {
									if (e.button === 1) {
										e.preventDefault();
										openEntryTab(entry.id, entry.title, true, entry.source_type);
									}
								}}
								oncontextmenu={(e) => showContextMenu(e, entry)}
								use:tooltip={entry.title}
								use:flash={isMatchEntry ? dashboardEntry?.id : undefined}
							>
								<span class="entry-title-text">{entry.title}</span>
								{#if isMatchEntry}
									<span class="entry-indicator" aria-hidden="true">↗</span>
								{/if}
							</button>
						{:else}
							<span class="empty-section">No entries</span>
						{/each}
					</div>
				{/if}
			{/each}
		</nav>

		<!-- Tags section — fixed below scrollable nav, mirrors RightSidebar Backlinks layout -->
		<div class="tags-section">
			<div class="section-row">
				<button
					class="section-label"
					class:active={$activeSection === 'tags'}
					onclick={() => navigateInSectionTab('tags')}
				>
					<BrainCircuit size={18} />
					<span class="label-text">TAGS</span>
				</button>
				{#if tagList.length > 0}
					<span class="count">{tagList.length}</span>
				{/if}
				<button
					class="icon-btn create-tag-btn"
					onclick={(e) => {
						e.stopPropagation();
						newTagExpanded = !newTagExpanded;
						if (newTagExpanded) newTagName = '';
						createError = null;
					}}
					use:tooltip={'Create tag'}
					aria-label="Create tag"
				>
					<Plus size={15} />
				</button>
			</div>

			{#if newTagExpanded}
				<div class="new-tag-row" transition:slide={{ duration: 120 }}>
					<input
						class="tag-input"
						type="text"
						placeholder="Tag name…"
						bind:value={newTagName}
						bind:this={newTagInputEl}
						onkeydown={(e) => {
							if (e.key === 'Enter') {
								e.preventDefault();
								createTag();
							} else if (e.key === 'Escape') {
								newTagExpanded = false;
								createError = null;
							}
						}}
						onblur={() => {
							if (!newTagName.trim()) {
								newTagExpanded = false;
								createError = null;
							}
						}}
					/>
				</div>
				{#if createError}
					<div class="tag-op-error"><TriangleAlert size={11} />{createError}</div>
				{/if}
			{/if}

			<div class="section-entries tags-section-entries">
				{#each displayTagList as tag (tag.name)}
					{#if mergingTag?.oldName === tag.name}
						<div bind:this={mergeRowEl}>
							<div class="tag-delete-row">
								<span class="tag-delete-label"
									>Merge <strong>{tag.name}</strong> into
									<strong>{mergingTag.newName}</strong>?</span
								>
								<button
									class="tag-action-btn tag-delete-confirm"
									onclick={() => confirmMergeTag()}
									use:tooltip={'Confirm merge'}
									aria-label="Confirm merge">✓</button
								>
								<button
									class="tag-action-btn"
									onclick={() => {
										mergingTag = null;
										mergeError = null;
									}}
									use:tooltip={'Cancel'}
									aria-label="Cancel merge"><X size={13.25} /></button
								>
							</div>
							{#if mergeError}
								<div class="tag-op-error"><TriangleAlert size={11} />{mergeError}</div>
							{/if}
						</div>
					{:else if deletingTag === tag.name}
						<div class="tag-delete-row" bind:this={deleteRowEl}>
							<span class="tag-delete-label">Delete <strong>{tag.name}</strong>?</span>
							{#if deletingTagBodyCount > 0}
								<span
									class="tag-delete-body-count"
									use:tooltip={`${deletingTagBodyCount} ${deletingTagBodyCount === 1 ? 'entry contains' : 'entries contain'} this as literal text in their body — it won't be removed, just converted to inline code`}
								>
									<TriangleAlert size={11} />{deletingTagBodyCount}
								</span>
							{/if}
							<button
								class="tag-action-btn tag-delete-confirm"
								onclick={() => deleteTag(tag.name)}
								use:tooltip={'Confirm delete'}
								aria-label="Confirm delete">✓</button
							>
							<button
								class="tag-action-btn"
								onclick={() => {
									deletingTag = null;
								}}
								use:tooltip={'Cancel'}
								aria-label="Cancel delete"><X size={13.25} /></button
							>
						</div>
					{:else if editingTag === tag.name}
						<div class="tag-edit-row">
							<input
								class="tag-input"
								type="text"
								bind:value={editTagValue}
								bind:this={editTagInputEl}
								onkeydown={(e) => {
									if (e.key === 'Enter') {
										e.preventDefault();
										renameTag(tag.name, editTagValue);
									} else if (e.key === 'Escape') {
										e.stopPropagation();
										editingTag = null;
										renameError = null;
									}
								}}
								onblur={() => {
									editingTag = null;
									renameError = null;
								}}
							/>
							<button
								class="tag-edit-cancel"
								onmousedown={(e) => e.preventDefault()}
								onclick={() => {
									editingTag = null;
									renameError = null;
								}}
								use:tooltip={'Cancel'}
								aria-label="Cancel rename"><X size={13.25} /></button
							>
						</div>
						{#if renameError}
							<div class="tag-op-error"><TriangleAlert size={11} />{renameError}</div>
						{/if}
					{:else}
						<div class="tag-item">
							<button
								class="tag-item-label"
								class:active-entry={$selectedTag === tag.name}
								class:entry-match-tag={activeDisplayEntry !== null &&
									currentEntryTagSet.has(tag.name)}
								onclick={() => selectTag(tag.name)}
								use:tooltip={tag.name}
							>
								<span class="tag-name">{tag.name}</span>
								<span class="tag-count">{tag.count}</span>
							</button>
							<div class="tag-actions">
								<button
									class="tag-action-btn"
									onclick={() => {
										editingTag = tag.name;
										editTagValue = tag.name;
										renameError = null;
									}}
									use:tooltip={'Rename tag'}
									aria-label="Rename tag"><Pencil size={13.25} /></button
								>
								<button
									class="tag-action-btn"
									onclick={() => requestDeleteTag(tag.name)}
									use:tooltip={'Delete tag'}
									aria-label="Delete tag"><Trash2 size={13.25} /></button
								>
							</div>
						</div>
					{/if}
				{:else}
					<span class="empty-section">No tags</span>
				{/each}
			</div>
		</div>
	{/if}

	<!-- URL input modal -->
	{#if $urlInputActive}
		<div
			class="url-backdrop"
			onclick={() => urlInputActive.set(false)}
			onkeydown={(e) => {
				if (e.key === 'Escape') urlInputActive.set(false);
			}}
			role="button"
			tabindex="-1"
		>
			<div
				class="url-dialog"
				role="dialog"
				aria-modal="true"
				tabindex="-1"
				onclick={(e) => e.stopPropagation()}
				onkeydown={(e) => {
					if (e.key === 'Escape') urlInputActive.set(false);
				}}
			>
				<input
					class="url-input-modal"
					type="text"
					placeholder="Paste or type a URL…"
					bind:value={urlInputValue}
					bind:this={urlInputEl}
					onkeydown={(e) => {
						if (e.key === 'Enter') {
							e.preventDefault();
							submitUrl();
						} else if (e.key === 'Escape') {
							urlInputActive.set(false);
						}
					}}
				/>
			</div>
		</div>
	{/if}

	<!-- Bottom bar -->
	<div class="bottom-bar">
		<button
			class="icon-btn"
			onclick={() => navigateInSectionTab('collecta')}
			use:tooltip={'Collecta'}
			aria-label="Collecta"
		>
			<Origami size={18} />
		</button>
		<button
			class="icon-btn"
			onclick={goLast}
			use:tooltip={'Last viewed'}
			aria-label="Last viewed"
			disabled={$lastViewedId === null}
		>
			<BookOpenText size={18} />
		</button>
		<button
			class="icon-btn paste-btn"
			class:paste-ok={pasteStatus === 'ok'}
			class:paste-err={pasteStatus === 'error'}
			class:paste-loading={pasteStatus === 'loading'}
			onclick={pasteUrl}
			use:tooltip={'Add URL from clipboard'}
			aria-label="Add URL from clipboard"
			disabled={pasteStatus === 'loading'}
		>
			<ClipboardPaste size={18} />
		</button>
		<button
			type="button"
			class="icon-btn settings-btn"
			class:active={isSettingsActive}
			onclick={handleSettingsClick}
			use:tooltip={'Settings'}
			aria-label="Settings"
		>
			<Settings size={18} />
		</button>
	</div>
</aside>

<style>
	.sidebar {
		width: 160px;
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
		transition:
			color 0.15s,
			background 0.15s;
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

	.paste-btn.paste-ok {
		color: #9ece6a;
	}
	.paste-btn.paste-err {
		color: var(--accent);
	}
	.paste-btn.paste-loading {
		color: var(--fg-muted);
		animation: pulse 1s ease-in-out infinite;
	}

	@keyframes pulse {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.4;
		}
	}

	.paste-feedback {
		padding: 3px 10px;
		font-size: var(--font-size-sublabel);
		color: var(--fg-muted);
		background: var(--bg-highlight);
		border-bottom: 1px solid var(--border);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.paste-feedback.is-ok {
		color: #9ece6a;
	}
	.paste-feedback.is-err {
		color: var(--accent);
	}

	.nav {
		flex: 1;
		overflow-y: auto;
		padding: 4px 0;
		min-height: 0;
	}

	.tags-section {
		flex-shrink: 0;
		border-top: 1px solid var(--border);
	}

	.tags-section-entries {
		max-height: 160px;
		overflow-y: auto;
		padding-left: 4px;
	}

	.section-row {
		display: flex;
		align-items: center;
		gap: 4px;
		padding: 3px 4px;
	}

	.section-row.entry-match .section-label {
		color: var(--accent);
	}

	.chevron-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 24px;
		height: 28px;
		padding: 0;
		margin-left: 3px;
		background: none;
		border: none;
		border-radius: 3px;
		color: var(--fg-muted);
		cursor: pointer;
		flex-shrink: 0;
		transition: color 0.12s;
	}

	.chevron-btn:hover {
		color: var(--fg);
	}

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
		font-size: var(--font-size-label);
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		cursor: pointer;
		text-align: left;
		transition:
			color 0.12s,
			background 0.12s;
	}

	.section-label:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.section-label.active {
		color: var(--accent);
	}

	.label-text {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.count {
		font-size: var(--font-size-sublabel);
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
		gap: 4px;
		width: 100%;
		padding: 3px 8px;
		background: none;
		border: none;
		border-radius: 3px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: var(--font-size-label);
		cursor: pointer;
		text-align: left;
		transition:
			color 0.12s,
			background 0.12s;
	}

	.entry-item:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.entry-item.active-entry {
		color: var(--accent);
		background: var(--bg-highlight);
	}

	.entry-item.entry-match-item {
		color: var(--accent);
	}

	.tag-item-label.entry-match-tag {
		color: var(--accent);
	}

	.entry-title-text {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.entry-indicator {
		flex-shrink: 0;
		font-size: 13.25px;
		color: var(--accent);
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
		margin-top: auto;
	}

	.create-tag-btn {
		width: 22px;
		height: 22px;
		flex-shrink: 0;
	}

	.new-tag-row {
		padding: 4px 8px 4px 26px;
	}

	.tag-delete-row {
		display: flex;
		align-items: center;
		gap: 3px;
		padding: 2px 8px 2px 22px;
	}

	.tag-delete-label {
		flex: 1;
		min-width: 0;
		font-size: var(--font-size-label);
		color: var(--fg-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.tag-delete-label strong {
		color: var(--fg);
		font-weight: 600;
	}

	.tag-delete-confirm {
		color: #9ece6a;
	}

	.tag-delete-confirm:hover {
		color: #b9ee8a;
	}

	.tag-delete-body-count {
		display: inline-flex;
		align-items: center;
		gap: 2px;
		flex-shrink: 0;
		color: var(--yellow);
		font-size: var(--font-size-count);
	}

	.tag-op-error {
		display: flex;
		align-items: flex-start;
		gap: 4px;
		padding: 2px 8px 4px 22px;
		color: var(--yellow);
		font-size: var(--font-size-sublabel);
		line-height: 1.35;
	}

	.tag-op-error :global(svg) {
		flex-shrink: 0;
		margin-top: 2px;
	}

	.tag-edit-row {
		display: flex;
		align-items: center;
		gap: 3px;
		padding: 2px 8px 2px 22px;
	}

	.tag-edit-cancel {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 18px;
		height: 18px;
		padding: 0;
		flex-shrink: 0;
		background: none;
		border: none;
		border-radius: 2px;
		color: var(--fg-muted);
		cursor: pointer;
		transition: color 0.12s;
	}

	.tag-edit-cancel:hover {
		color: var(--fg);
	}

	.tag-input {
		width: 100%;
		padding: 2px 6px;
		background: var(--bg-highlight);
		border: 1px solid var(--border);
		border-radius: 3px;
		color: var(--fg);
		font-family: inherit;
		font-size: var(--font-size-label);
		outline: none;
		box-sizing: border-box;
	}

	.tag-input:focus {
		border-color: var(--accent-dark);
	}

	.tag-item {
		display: flex;
		align-items: center;
		width: 100%;
		border-radius: 3px;
	}

	.tag-item-label {
		display: flex;
		align-items: center;
		gap: 4px;
		flex: 1;
		min-width: 0;
		padding: 3px 4px 3px 8px;
		background: none;
		border: none;
		border-radius: 3px 0 0 3px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: var(--font-size-label);
		cursor: pointer;
		text-align: left;
		overflow: hidden;
		transition:
			color 0.12s,
			background 0.12s;
	}

	.tag-item-label:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.tag-item-label.active-entry {
		color: var(--accent);
	}

	.tag-actions {
		display: flex;
		align-items: center;
		gap: 1px;
		padding-right: 4px;
		flex-shrink: 0;
		opacity: 0;
		transition: opacity 0.12s;
	}

	.tag-item:hover .tag-actions {
		opacity: 1;
	}

	.tag-action-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 18px;
		height: 18px;
		padding: 0;
		background: none;
		border: none;
		border-radius: 2px;
		color: var(--fg-muted);
		cursor: pointer;
		transition: color 0.12s;
	}

	.tag-action-btn:hover {
		color: var(--fg);
	}

	.url-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.55);
		display: flex;
		align-items: flex-start;
		justify-content: center;
		padding-top: 28vh;
		z-index: 200;
	}

	.url-dialog {
		width: 540px;
		max-width: 90vw;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow: hidden;
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
	}

	.url-input-modal {
		width: 100%;
		padding: 14px 16px;
		background: transparent;
		border: none;
		color: var(--fg);
		font-family: inherit;
		font-size: 1rem;
		outline: none;
		box-sizing: border-box;
	}

	.url-input-modal::placeholder {
		color: var(--fg-muted);
	}
</style>
