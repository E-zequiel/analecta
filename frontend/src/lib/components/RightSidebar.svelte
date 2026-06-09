<script lang="ts">
	import { X, Cable, ChevronRight } from '@lucide/svelte';
	import {
		entries as entriesApi,
		type Backlink,
		type Entry,
		type SubgraphResult,
	} from '$lib/api/client';
	import { selectedTag } from '$lib/stores/ui';
	import LocalGraph from './LocalGraph.svelte';
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

	let backlinks = $state<Backlink[]>([]);
	let tagEntries = $state<Entry[]>([]);
	let subgraph = $state<SubgraphResult | null>(null);
	let graphCollapsed = $state(true);

	$effect(() => {
		const tag = $selectedTag;
		const id = activeEntryId;
		tagEntries = [];
		if (!tag || id === null) return;

		let cancelled = false;
		entriesApi
			.list({ tag })
			.then((result) => {
				if (!cancelled) tagEntries = result.filter((e) => e.id !== id);
			})
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		const id = activeEntryId;
		subgraph = null;
		if (id === null) return;

		let cancelled = false;
		entriesApi
			.getSubgraph(id)
			.then((result) => {
				if (!cancelled) subgraph = result;
			})
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		const id = activeEntryId;
		backlinks = [];
		selectedTag.set(null);
		if (id === null) return;

		let cancelled = false;
		entriesApi
			.getBacklinks(id)
			.then((result) => {
				if (!cancelled) backlinks = result.linked;
			})
			.catch(() => {});
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

	{#if activeEntryId !== null}
		<div class="graph-section">
			<button
				class="section-header"
				onclick={() => (graphCollapsed = !graphCollapsed)}
				aria-expanded={!graphCollapsed}
			>
				<span class="chevron" class:rotated={!graphCollapsed}>
					<ChevronRight size={13} />
				</span>
				<span class="section-label">LOCAL GRAPH</span>
			</button>
			{#if !graphCollapsed && subgraph}
				<LocalGraph
					nodes={subgraph.nodes}
					edges={subgraph.edges}
					focusNodeId={subgraph.focus_node_id}
					onopen={onbacklinksopen}
				/>
			{/if}
		</div>
	{/if}

	{#if activeEntryId !== null}
		<div class="backlinks-section">
			{#if $selectedTag}
				<div class="bl-row">
					<div class="bl-header">
						<span class="bl-tag-name">#{$selectedTag}</span>
						{#if tagEntries.length > 0}
							<span class="bl-count">{tagEntries.length}</span>
						{/if}
					</div>
					<button
						class="bl-clear-btn"
						onclick={() => selectedTag.set(null)}
						use:tooltip={'Back to backlinks'}
						aria-label="Back to backlinks"
					>
						<X size={13} />
					</button>
				</div>

				{#if tagEntries.length === 0}
					<p class="bl-empty">No entries.</p>
				{:else}
					<div class="bl-list">
						{#each tagEntries as entry (entry.id)}
							<button class="bl-item" onclick={() => onbacklinksopen?.(entry.id, entry.title)}>
								<span class="bl-item-name">{entry.title}</span>
							</button>
						{/each}
					</div>
				{/if}
			{:else}
				<div class="bl-row">
					<div class="bl-header">
						<Cable size={15} />
						<span class="bl-label">BACKLINKS</span>
						{#if backlinks.length > 0}
							<span class="bl-count">{backlinks.length}</span>
						{/if}
					</div>
				</div>

				{#if backlinks.length === 0}
					<p class="bl-empty">No backlinks.</p>
				{:else}
					<div class="bl-list">
						{#each backlinks as item, i (`${item.id}-${i}`)}
							<button class="bl-item" onclick={() => onbacklinksopen?.(item.id, item.name)}>
								<span class="bl-item-name">{item.name}</span>
								{#if item.context?.heading}
									<span class="bl-item-heading">{item.context.heading}</span>
								{/if}
								{#if item.context}
									<span class="bl-item-ctx"
										>…{item.context.pre}<em class="bl-em">{item.context.highlight}</em>{item.context
											.post}…</span
									>
								{/if}
							</button>
						{/each}
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
		font-size: 13px;
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
		font-size: 13px;
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
		font-size: 12px;
		color: var(--fg-muted);
		margin: 0;
	}

	/* ── Local graph section ── */
	.graph-section {
		flex-shrink: 0;
		border-top: 1px solid var(--border);
	}

	.section-header {
		display: flex;
		align-items: center;
		gap: 5px;
		width: 100%;
		padding: 6px 4px;
		background: none;
		border: none;
		cursor: pointer;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 0.7rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		transition: color 0.12s;
	}

	.section-header:hover {
		color: var(--fg);
	}

	.section-label {
		flex: 1;
		text-align: left;
	}

	.chevron {
		display: flex;
		flex-shrink: 0;
		transition: transform 0.15s;
	}

	.chevron.rotated {
		transform: rotate(90deg);
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
		padding: 6px 4px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 0.7rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
	}

	.bl-label {
		flex: 1;
	}

	.bl-tag-name {
		flex: 1;
		font-size: 0.7rem;
		font-weight: 700;
		color: var(--accent);
		letter-spacing: 0.06em;
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
		font-size: 0.68rem;
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

	.bl-empty {
		padding: 4px 10px 8px 26px;
		font-size: 12px;
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

	.bl-item-name {
		font-size: 12px;
		color: var(--cyan);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.bl-item-heading {
		font-size: 10px;
		color: var(--fg-muted);
		font-style: italic;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.bl-item-ctx {
		font-size: 13px;
		color: var(--fg-muted);
		line-height: 1.3;
		word-break: break-word;
	}

	.bl-em {
		color: var(--fg);
		font-style: normal;
	}
</style>
