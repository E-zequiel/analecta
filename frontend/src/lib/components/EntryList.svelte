<script lang="ts">
	import type { Entry } from '$lib/api/client';
	import { navigateInTab, openEntryTab } from '$lib/stores/tabs';
	import { showContextMenu } from '$lib/stores/contextMenu';

	let { entries, loading = false }: { entries: Entry[]; loading?: boolean } = $props();

	const sourceColors: Record<string, string> = {
		article: 'var(--accent)',
		youtube: 'var(--red)',
		substack: 'var(--accent-warm)',
		x: 'var(--fg-muted)'
	};

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleDateString(undefined, {
			year: 'numeric',
			month: 'short',
			day: 'numeric'
		});
	}
</script>

<div class="entry-list">
	{#if loading}
		<p class="hint">Loading…</p>
	{:else if entries.length === 0}
		<p class="hint">No entries found.</p>
	{:else}
		{#each entries as entry (entry.id)}
			<button
				class="entry-card"
				onclick={() => navigateInTab(entry.id, entry.title)}
				onmousedown={(e) => {
					if (e.button === 1) {
						e.preventDefault();
						openEntryTab(entry.id, entry.title, true);
					}
				}}
				oncontextmenu={(e) => showContextMenu(e, entry)}
			>
				<div class="entry-header">
					<span class="title">{entry.title}</span>
					<span class="source" style="color: {sourceColors[entry.source_type] ?? 'var(--fg-muted)'}">
						{entry.source_type}
					</span>
				</div>
				<div class="entry-meta">
					<span class="date">{formatDate(entry.created_at)}</span>
					{#if entry.status !== 'unread'}
						<span class="status">{entry.status}</span>
					{/if}
					{#each entry.tags as tag}
						<span class="tag">#{tag}</span>
					{/each}
				</div>
			</button>
		{/each}
	{/if}
</div>

<style>
	.entry-list {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.hint {
		padding: 1rem;
		color: var(--fg-muted);
		font-size: 13px;
	}

	.entry-card {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		width: 100%;
		padding: 0.75rem 1rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 6px;
		cursor: pointer;
		text-align: left;
		font-family: inherit;
		transition: border-color 0.15s, background 0.15s;
	}

	.entry-card:hover {
		background: var(--bg-highlight);
		border-color: var(--accent-dark);
	}

	.entry-header {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 0.5rem;
	}

	.title {
		font-size: 13px;
		font-weight: 600;
		color: var(--fg);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.source {
		font-size: 11px;
		flex-shrink: 0;
	}

	.entry-meta {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
	}

	.date {
		font-size: 11px;
		color: var(--fg-muted);
	}

	.status {
		font-size: 11px;
		color: var(--yellow);
	}

	.tag {
		font-size: 11px;
		color: var(--magenta);
	}
</style>
