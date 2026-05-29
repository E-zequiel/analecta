<script lang="ts">
	import { X } from '@lucide/svelte';

	export type StackEntry = {
		id: string;
		title: string;
		sourceType?: string;
	};

	const {
		entries = [],
		activeId = null,
		width = 240,
		onselect,
		onclose,
		onwidthchange,
	}: {
		entries?: StackEntry[];
		activeId?: string | null;
		width?: number;
		onselect?: (id: string, title: string) => void;
		onclose?: (id: string) => void;
		onwidthchange?: (w: number) => void;
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
<aside class="stack-panel" style:width="{width}px">
	<div class="resize-handle" onmousedown={startResize}></div>

	<div class="stack-list">
		{#each entries as entry (entry.id)}
			{@const active = entry.id === activeId}
			<div class="stack-item" class:active style:--src-color={sourceColor(entry.sourceType)}>
				<button
					class="stack-item-btn"
					onclick={() => onselect?.(entry.id, entry.title)}
					title={entry.title}
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
					title="Cerrar"
				>
					<X size={12} />
				</button>
			</div>
		{/each}

		{#if entries.length === 0}
			<p class="stack-empty">No entries open.</p>
		{/if}
	</div>
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
		font-size: 11px;
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
</style>
