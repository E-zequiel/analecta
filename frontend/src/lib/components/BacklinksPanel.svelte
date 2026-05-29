<script lang="ts">
	import { ChevronDown, ChevronRight, Link, Search, Filter, ArrowUpDown } from '@lucide/svelte';

	export type Backlink = {
		id: number;
		name: string;
		context?: {
			heading?: string;
			pre: string;
			highlight: string;
			post: string;
		};
	};

	const {
		linkedMentions = [],
		width = 240,
		onopen,
		onwidthchange,
	}: {
		linkedMentions?: Backlink[];
		width?: number;
		onopen?: (id: number, name: string) => void;
		onwidthchange?: (w: number) => void;
	} = $props();

	let linkedOpen = $state(true);

	const MIN_W = 160;
	const MAX_W = 320;

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
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<aside class="backlinks-panel" style:width="{width}px">
	<div class="resize-handle" onmousedown={startResize}></div>

	<div class="panel-header">
		<div class="header-left">
			<Link size={14} color="var(--fg-muted)" />
			<span class="header-title">Backlinks</span>
			<span class="header-count">{linkedMentions.length}</span>
		</div>
		<div class="header-actions">
			<button class="icon-btn" title="Search backlinks">
				<Search size={13} />
			</button>
			<button class="icon-btn" title="Filter backlinks">
				<Filter size={13} />
			</button>
			<button class="icon-btn" title="Sort backlinks">
				<ArrowUpDown size={13} />
			</button>
		</div>
	</div>

	<div class="panel-body">
		<div class="section">
			<button class="section-header" onclick={() => (linkedOpen = !linkedOpen)}>
				<span class="section-title">Linked mentions</span>
				{#if linkedOpen}
					<ChevronDown size={12} color="var(--fg-muted)" />
				{:else}
					<ChevronRight size={12} color="var(--fg-muted)" />
				{/if}
			</button>

			{#if linkedOpen}
				{#if linkedMentions.length === 0}
					<p class="empty-state">No backlinks found.</p>
				{:else}
					{#each linkedMentions as item (item.id)}
						<button class="backlink-item" onclick={() => onopen?.(item.id, item.name)}>
							<span class="backlink-name">{item.name}</span>
							{#if item.context}
								{#if item.context.heading}
									<span class="backlink-heading">{item.context.heading}</span>
								{/if}
								<span class="backlink-ctx">
									…{item.context.pre}<em class="backlink-em">{item.context.highlight}</em>{item
										.context.post}…
								</span>
							{/if}
						</button>
					{/each}
				{/if}
			{/if}
		</div>
	</div>
</aside>

<style>
	.backlinks-panel {
		flex-shrink: 0;
		background: var(--bg);
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

	.panel-header {
		height: 40px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0 12px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
		gap: 8px;
	}

	.header-left {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 0;
	}

	.header-title {
		font-size: 13px;
		color: var(--fg);
		font-weight: 500;
		white-space: nowrap;
	}

	.header-count {
		font-size: 11px;
		color: var(--fg-muted);
		background: var(--bg-alt);
		padding: 1px 7px;
		border-radius: 9999px;
		flex-shrink: 0;
	}

	.header-actions {
		display: flex;
		align-items: center;
		gap: 2px;
		flex-shrink: 0;
	}

	.panel-body {
		flex: 1;
		overflow-y: auto;
	}

	.section {
		border-bottom: 1px solid var(--border);
	}

	.section-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		width: 100%;
		padding: 8px 12px 6px;
		background: none;
		border: none;
		cursor: pointer;
		font-family: inherit;
		text-align: left;
		transition: background 0.14s;
	}
	.section-header:hover {
		background: var(--bg-highlight);
	}

	.section-title {
		font-size: 12px;
		color: var(--fg-muted);
	}

	.empty-state {
		padding: 4px 12px 10px;
		font-size: 12px;
		color: var(--fg-muted);
		margin: 0;
		opacity: 0.6;
	}

	.backlink-item {
		display: flex;
		flex-direction: column;
		gap: 3px;
		width: 100%;
		padding: 6px 12px;
		background: none;
		border: none;
		border-bottom: 1px solid rgba(41, 46, 66, 0.6);
		cursor: pointer;
		text-align: left;
		font-family: inherit;
		transition: background 0.14s;
	}
	.backlink-item:hover {
		background: rgba(255, 255, 255, 0.04);
	}

	.backlink-name {
		font-size: 13px;
		color: var(--cyan);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.backlink-heading {
		font-size: 11px;
		color: var(--fg-muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		font-style: italic;
	}

	.backlink-ctx {
		font-size: 12px;
		color: var(--fg-muted);
		line-height: 1.4;
		word-break: break-word;
	}

	.backlink-em {
		color: var(--fg);
		font-style: normal;
	}

	.icon-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 24px;
		height: 24px;
		background: none;
		border: none;
		border-radius: 2px;
		color: var(--fg-muted);
		cursor: pointer;
		transition:
			background 0.14s,
			color 0.14s;
		flex-shrink: 0;
		padding: 0;
	}
	.icon-btn:hover {
		background: rgba(255, 255, 255, 0.067);
		color: var(--fg);
	}
</style>
