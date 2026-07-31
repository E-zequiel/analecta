<script lang="ts">
	import { X, Cable, ArrowDownLeft, ArrowUpRight } from '@lucide/svelte';
	import {
		entries as entriesApi,
		type Backlink,
		type Entry,
		type HashtagGroup,
		type OutgoingLink,
	} from '$lib/api/client';
	import { selectedTag, sidebarTagPreview } from '$lib/stores/ui';
	import { entryChangedTick } from '$lib/stores/sse';
	import { showContextMenu } from '$lib/stores/contextMenu';
	import { tooltip } from '$lib/actions/tooltip';

	export type StackEntry = {
		id: string;
		title: string;
		sourceType?: string;
		entryId?: number;
	};

	const {
		entries = [],
		activeId = null,
		width = 240,
		onselect,
		onclose,
		onwidthchange,
		activeEntryId = null,
		onbacklinksopen,
	}: {
		entries?: StackEntry[];
		activeId?: string | null;
		width?: number;
		onselect?: (id: string, title: string) => void;
		onclose?: (id: string) => void;
		onwidthchange?: (w: number) => void;
		activeEntryId?: number | null;
		onbacklinksopen?: (id: number, name: string) => void;
	} = $props();

	const SOURCE_COLORS: Record<string, string> = {
		article: 'var(--accent)',
		youtube: 'var(--red)',
		substack: 'var(--accent-warm)',
		x: 'var(--fg-muted)',
	};

	function sourceColor(type?: string): string {
		return (type && SOURCE_COLORS[type]) ?? 'var(--fg-muted)';
	}

	const MIN_W = 160;
	const MAX_W = 320;

	async function handleContextMenu(e: MouseEvent, entryId: number) {
		e.preventDefault();
		try {
			const entry = await entriesApi.get(entryId);
			showContextMenu(e, {
				id: entry.id,
				title: entry.title,
				url: entry.url,
				file_path: entry.file_path,
				status: entry.status,
				flags: entry.flags,
			});
		} catch {
			// entry deleted or sidecar not ready
		}
	}

	function startResize(e: MouseEvent) {
		const startX = e.clientX;
		const startW = width;

		function onMove(ev: MouseEvent) {
			const delta = startX - ev.clientX;
			onwidthchange?.(Math.min(MAX_W, Math.max(MIN_W, startW + delta)));
		}
		function onUp() {
			window.removeEventListener('mousemove', onMove);
			window.removeEventListener('mouseup', onUp);
		}

		window.addEventListener('mousemove', onMove);
		window.addEventListener('mouseup', onUp);
		e.preventDefault();
	}

	type DirectLink = {
		id: number;
		name: string;
		direction: 'in' | 'out';
		context?: { heading?: string; pre: string; highlight: string; post: string };
	};

	let tagEntries = $state<Entry[]>([]);
	let hashtagGroups = $state<HashtagGroup[]>([]);
	let directBacklinks = $state<Backlink[]>([]);
	let outgoingLinks = $state<OutgoingLink[]>([]);
	let connLoading = $state(false);

	const activeTag = $derived($sidebarTagPreview ?? $selectedTag);

	// Incoming + outgoing links merged into one list, each row tagged with its
	// direction — the user chose a single combined list over two sections.
	const directLinks = $derived<DirectLink[]>(
		[
			...directBacklinks.map((b) => ({ ...b, direction: 'in' as const })),
			...outgoingLinks.map((o) => ({ ...o, direction: 'out' as const })),
		].sort((a, b) => a.name.localeCompare(b.name))
	);

	$effect(() => {
		const tag = activeTag;
		tagEntries = [];
		if (!tag) return;

		let cancelled = false;
		entriesApi
			.list({ tag })
			.then((result) => {
				if (!cancelled) tagEntries = result;
			})
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	});

	// Unified connections — hashtag groups (via backlink_refs) + direct backlinks.
	// Runs for both reading view and dashboard preview whenever an entry is active.
	$effect(() => {
		const id = activeEntryId;
		const tag = activeTag;
		void $entryChangedTick;

		hashtagGroups = [];
		directBacklinks = [];
		outgoingLinks = [];

		if (id === null || tag !== null) return;

		connLoading = true;
		let cancelled = false;

		(async () => {
			try {
				const [conn, bl, ol] = await Promise.all([
					entriesApi.getHashtagConnections(id).catch(() => ({ groups: [] as HashtagGroup[] })),
					entriesApi.getBacklinks(id).catch(() => ({ linked: [] as Backlink[] })),
					entriesApi.getOutgoingLinks(id).catch(() => ({ linked: [] as OutgoingLink[] })),
				]);
				if (cancelled) return;
				hashtagGroups = conn.groups;
				directBacklinks = bl.linked;
				outgoingLinks = ol.linked;
			} catch {
				// outer fetch failed — leave empty state
			} finally {
				if (!cancelled) connLoading = false;
			}
		})();

		return () => {
			cancelled = true;
		};
	});
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<aside class="stack-panel" style:width="{width}px">
	<div
		class="resize-handle"
		onmousedown={startResize}
		ondblclick={() => onwidthchange?.(240)}
	></div>

	<div class="stack-list">
		{#each entries as entry (entry.id)}
			{@const active = entry.id === activeId}
			<div
				class="stack-item"
				class:active
				style:--src-color={sourceColor(entry.sourceType)}
				onauxclick={(e) => {
					if (e.button === 1) {
						e.preventDefault();
						onclose?.(entry.id);
					}
				}}
				oncontextmenu={(e) => {
					if (entry.entryId !== undefined) void handleContextMenu(e, entry.entryId);
				}}
			>
				<button
					class="stack-item-btn"
					onclick={() => onselect?.(entry.id, entry.title)}
					use:tooltip={entry.title}
				>
					<span class="item-title">{entry.title}</span>
					{#if entry.sourceType}
						<span class="item-type">{entry.sourceType}</span>
					{/if}
				</button>

				<button
					class="stack-close"
					onclick={(e) => {
						e.stopPropagation();
						onclose?.(entry.id);
					}}
					use:tooltip={'Close'}
					aria-label="Close"
				>
					<X size={12} />
				</button>
			</div>
		{/each}

		{#if entries.length === 0}
			<p class="stack-empty">No entries open.</p>
		{/if}
	</div>

	{#if activeEntryId !== null || activeTag !== null}
		<div class="backlinks-section">
			<div class="bl-row">
				<div class="bl-header">
					<Cable size={18} />
					<span class="bl-label">BACKLINKS</span>
					{#if activeTag && tagEntries.length > 0}
						<span class="bl-count">{tagEntries.length}</span>
					{:else if !activeTag && !connLoading}
						{@const total =
							hashtagGroups.reduce((s, g) => s + g.entries.length, 0) + directLinks.length}
						{#if total > 0}
							<span class="bl-count">{total}</span>
						{/if}
					{/if}
				</div>
				{#if activeTag}
					<button
						class="bl-clear-btn"
						onclick={() => {
							sidebarTagPreview.set(null);
							selectedTag.set(null);
						}}
						use:tooltip={'Back to backlinks'}
						aria-label="Back to backlinks"
					>
						<X size={13} />
					</button>
				{/if}
			</div>

			{#if activeTag}
				<p class="bl-tag-label">#{activeTag}</p>
				{#if tagEntries.length === 0}
					<p class="bl-empty">No entries.</p>
				{:else}
					<div class="bl-list">
						{#each tagEntries as entry (entry.id)}
							<button
								class="bl-item"
								onclick={() => onbacklinksopen?.(entry.id, entry.title)}
								use:tooltip={entry.title}
							>
								<span class="bl-item-name">{entry.title}</span>
							</button>
						{/each}
					</div>
				{/if}
			{:else}
				{#if connLoading}
					<p class="bl-empty">Loading…</p>
				{:else if hashtagGroups.length === 0 && directLinks.length === 0}
					<p class="bl-empty">No connections.</p>
				{:else}
					<div class="bl-rich-scroll">
						{#each hashtagGroups as group (group.hashtag)}
							<div class="bl-group-header">
								<span class="bl-group-tag">#{group.hashtag}</span>
								<span class="bl-count">{group.entries.length}</span>
							</div>
							<div class="bl-group-list">
								{#each group.entries as entry (entry.id)}
									<button
										class="bl-item"
										onclick={() => onbacklinksopen?.(entry.id, entry.title)}
										use:tooltip={entry.title}
									>
										<span class="bl-item-name">{entry.title}</span>
									</button>
								{/each}
							</div>
						{/each}
						{#if directLinks.length > 0}
							<div class="bl-group-header">
								<span class="bl-group-direct">Direct</span>
								<span class="bl-count">{directLinks.length}</span>
							</div>
							<div class="bl-group-list">
								{#each directLinks as item, i (`${item.direction}-${item.id}-${i}`)}
									<button
										class="bl-item"
										onclick={() => onbacklinksopen?.(item.id, item.name)}
										use:tooltip={item.name}
									>
										<span class="bl-item-row">
											{#if item.direction === 'in'}
												<span class="bl-item-dir" use:tooltip={'Incoming link'}>
													<ArrowDownLeft size={11} />
												</span>
											{:else}
												<span class="bl-item-dir" use:tooltip={'Outgoing link'}>
													<ArrowUpRight size={11} />
												</span>
											{/if}
											<span class="bl-item-name">{item.name}</span>
										</span>
										{#if item.context?.heading}
											<span class="bl-item-heading">{item.context.heading}</span>
										{/if}
									</button>
								{/each}
							</div>
						{/if}
					</div>
				{/if}
			{/if}
		</div>
	{/if}
</aside>

<style>
	.stack-panel {
		flex-shrink: 0;
		background: var(--bg-dark);
		border-left: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		overflow: hidden;
		position: relative;
	}

	.resize-handle {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 4px;
		cursor: col-resize;
		z-index: 10;
		transition: background 0.14s;
	}
	.resize-handle:hover {
		background: var(--accent-dark);
	}

	.stack-list {
		flex: 1;
		overflow-y: auto;
		padding: 4px 0;
	}

	.stack-item {
		display: flex;
		align-items: stretch;
		position: relative;
		border-left: 2px solid transparent;
		transition:
			background 0.14s,
			border-color 0.14s;
	}
	.stack-item:hover {
		background: var(--bg-highlight);
		border-left-color: var(--src-color);
	}
	.stack-item.active {
		background: var(--bg-highlight);
		border-left-color: var(--src-color);
	}

	.stack-item-btn {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 7px 28px 7px 10px;
		background: none;
		border: none;
		cursor: pointer;
		text-align: left;
		font-family: inherit;
	}

	.item-title {
		font-size: var(--font-size-label);
		color: var(--fg-dark);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-weight: 400;
		transition: color 0.14s;
	}
	.stack-item:hover .item-title,
	.stack-item.active .item-title {
		color: var(--fg);
	}
	.stack-item.active .item-title {
		font-weight: 600;
	}

	.item-type {
		font-size: var(--font-size-label);
		color: var(--fg-muted);
	}

	.stack-close {
		display: none;
		align-items: center;
		justify-content: center;
		position: absolute;
		right: 6px;
		top: 50%;
		transform: translateY(-50%);
		width: 16px;
		height: 16px;
		background: none;
		border: none;
		border-radius: 2px;
		color: var(--fg-muted);
		cursor: pointer;
		padding: 0;
		transition:
			color 0.12s,
			background 0.12s;
	}
	.stack-item:hover .stack-close,
	.stack-item.active .stack-close {
		display: flex;
	}
	.stack-close:hover {
		color: var(--fg);
		background: rgba(255, 255, 255, 0.1);
	}

	.stack-empty {
		padding: 12px;
		font-size: var(--font-size-sublabel);
		color: var(--fg-muted);
		margin: 0;
	}

	/* ── Backlinks section ── */
	.backlinks-section {
		flex-shrink: 0;
		border-top: 1px solid var(--border);
	}

	.bl-row {
		display: flex;
		align-items: center;
		padding: 0 4px 0 2px;
	}

	.bl-header {
		display: flex;
		align-items: center;
		gap: 5px;
		flex: 1;
		min-width: 0;
		padding: 3px 6px;
		color: var(--fg-dark);
		font-family: inherit;
		font-size: var(--font-size-label);
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.bl-label {
		flex: 1;
	}

	.bl-tag-label {
		padding: 2px 10px 4px 26px;
		font-size: var(--font-size-sublabel);
		font-weight: 700;
		color: var(--accent);
		letter-spacing: 0.06em;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.bl-clear-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 20px;
		height: 20px;
		padding: 0;
		background: none;
		border: none;
		border-radius: 3px;
		color: var(--fg-muted);
		cursor: pointer;
		flex-shrink: 0;
		transition: color 0.12s;
	}
	.bl-clear-btn:hover {
		color: var(--fg);
	}

	.bl-count {
		font-size: var(--font-size-count);
		color: var(--fg-muted);
		background: var(--bg-highlight);
		border-radius: 10px;
		padding: 0 5px;
		min-width: 16px;
		text-align: center;
		flex-shrink: 0;
	}

	.bl-list {
		max-height: 160px;
		overflow-y: auto;
		padding: 2px 0 4px;
	}

	.bl-rich-scroll {
		max-height: 320px;
		overflow-y: auto;
		padding-top: 2px;
	}

	.bl-group-header {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 5px 8px 3px 10px;
		font-size: var(--font-size-count);
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}

	.bl-group-tag {
		color: var(--magenta);
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.bl-group-direct {
		color: var(--cyan);
		flex: 1;
	}

	.bl-group-list {
		padding: 2px 0 4px;
	}

	.bl-empty {
		padding: 4px 10px 8px 26px;
		font-size: var(--font-size-sublabel);
		color: var(--fg-muted);
		font-style: italic;
		margin: 0;
	}

	.bl-item {
		display: flex;
		flex-direction: column;
		gap: 2px;
		width: 100%;
		padding: 5px 10px 5px 26px;
		background: none;
		border: none;
		border-bottom: 1px solid rgba(41, 46, 66, 0.4);
		cursor: pointer;
		text-align: left;
		font-family: inherit;
		transition: background 0.14s;
	}
	.bl-item:hover {
		background: var(--bg-highlight);
	}

	.bl-item-row {
		display: flex;
		align-items: center;
		gap: 5px;
		min-width: 0;
	}

	.bl-item-dir {
		display: flex;
		align-items: center;
		flex-shrink: 0;
		color: var(--fg-muted);
	}

	.bl-item-name {
		font-size: var(--font-size-label);
		color: var(--fg-dark);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		transition: color 0.14s;
	}
	.bl-item:hover .bl-item-name {
		color: var(--fg);
	}

	.bl-item-heading {
		font-size: var(--font-size-sublabel);
		color: var(--fg-muted);
		font-style: italic;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
