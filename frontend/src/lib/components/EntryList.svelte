<script lang="ts">
	import type { Entry } from '$lib/api/client';
	import { navigateInTab, openEntryTab } from '$lib/stores/tabs';
	import { showContextMenu } from '$lib/stores/contextMenu';
	import { Eye, EyeClosed, Bookmark, Gem } from '@lucide/svelte';

	const {
		entries,
		loading = false,
		onitemclick,
		showStatusLabel = false,
	}: {
		entries: Entry[];
		loading?: boolean;
		onitemclick?: (entry: Entry) => void;
		showStatusLabel?: boolean;
	} = $props();

	const sourceColors: Record<string, string> = {
		article: 'var(--accent)',
		youtube: 'var(--red)',
		substack: 'var(--accent-warm)',
		x: 'var(--fg-muted)',
	};

	function formatDate(iso: string): string {
		return new Date(iso).toLocaleDateString(undefined, {
			year: 'numeric',
			month: 'short',
			day: 'numeric',
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
			{#if onitemclick}
				<div
					class="entry-row selectable"
					role="button"
					tabindex="0"
					onclick={() => onitemclick!(entry)}
					onkeydown={(e) => {
						if (e.key === 'Enter' || e.key === ' ') {
							e.preventDefault();
							onitemclick!(entry);
						}
					}}
					oncontextmenu={(e) => showContextMenu(e, entry)}
				>
					<div class="entry-body">
						<div class="entry-top">
							<span class="entry-title">{entry.title}</span>
							<span
								class="entry-source"
								style:color={sourceColors[entry.source_type] ?? 'var(--fg-muted)'}
								>{entry.source_type}</span
							>
						</div>
						<div class="entry-meta">
							<span class="entry-date">{formatDate(entry.created_at)}</span>
							{#if showStatusLabel}
								<span class="entry-status entry-status-{entry.status}">
									{#if entry.status === 'read'}
										<Eye size={14} />
									{:else}
										<EyeClosed size={14} />
									{/if}
								</span>
							{/if}
							{#if entry.flags.includes('bookmark')}
								<span class="entry-badge entry-badge-bookmark"><Bookmark size={14} /></span>
							{/if}
							{#if entry.flags.includes('gem')}
								<span class="entry-badge entry-badge-gem"><Gem size={14} /></span>
							{/if}
							{#each entry.tags as tag (tag)}
								<span class="entry-tag">#{tag}</span>
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
			{:else}
				<button
					class="entry-row"
					onclick={() => navigateInTab(entry.id, entry.title, entry.source_type)}
					onmousedown={(e) => {
						if (e.button === 1) {
							e.preventDefault();
							openEntryTab(entry.id, entry.title, true, entry.source_type);
						}
					}}
					oncontextmenu={(e) => showContextMenu(e, entry)}
				>
					<div class="entry-body">
						<div class="entry-top">
							<span class="entry-title">{entry.title}</span>
							<span
								class="entry-source"
								style:color={sourceColors[entry.source_type] ?? 'var(--fg-muted)'}
								>{entry.source_type}</span
							>
						</div>
						<div class="entry-meta">
							<span class="entry-date">{formatDate(entry.created_at)}</span>
							{#if showStatusLabel}
								<span class="entry-status entry-status-{entry.status}">
									{#if entry.status === 'read'}
										<Eye size={14} />
									{:else}
										<EyeClosed size={14} />
									{/if}
								</span>
							{/if}
							{#if entry.flags.includes('bookmark')}
								<span class="entry-badge entry-badge-bookmark"><Bookmark size={14} /></span>
							{/if}
							{#if entry.flags.includes('gem')}
								<span class="entry-badge entry-badge-gem"><Gem size={14} /></span>
							{/if}
							{#each entry.tags as tag (tag)}
								<span class="entry-tag">#{tag}</span>
							{/each}
						</div>
					</div>
				</button>
			{/if}
		{/each}
	{/if}
</div>

<style>
	.entry-list {
		display: flex;
		flex-direction: column;
	}

	.hint {
		padding: 2rem 1.25rem;
		color: var(--fg-muted);
		font-size: 13px;
		font-style: italic;
	}

	.entry-row {
		display: flex;
		align-items: center;
		width: 100%;
		padding: 12px 20px;
		border: none;
		border-bottom: 1px solid var(--border);
		background: transparent;
		cursor: pointer;
		text-align: left;
		font-family: inherit;
		transition: background 0.1s;
	}

	.entry-row:hover {
		background: var(--bg-highlight);
	}

	.entry-body {
		flex: 1;
		min-width: 0;
	}

	.entry-top {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		margin-bottom: 4px;
	}

	.entry-title {
		flex: 1;
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--fg);
		line-height: 1.4;
	}

	.entry-source {
		font-size: 0.68rem;
		flex-shrink: 0;
		padding: 2px 7px;
		border-radius: 3px;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		margin-top: 2px;
	}

	.entry-meta {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
	}

	.entry-date {
		font-size: 0.68rem;
		color: var(--fg-muted);
	}

	.entry-status,
	.entry-badge {
		display: inline-flex;
		align-items: center;
	}

	.entry-status-read {
		color: var(--yellow);
	}

	.entry-status-unread {
		color: var(--fg-muted);
	}

	.entry-badge-bookmark {
		color: var(--magenta);
	}

	.entry-badge-gem {
		color: var(--cyan);
	}

	.entry-tag {
		font-size: 0.62rem;
		color: var(--fg-muted);
		padding: 1px 5px;
		border-radius: 3px;
		background: var(--bg-alt);
		border: 1px solid var(--border);
	}

	.view-btn {
		flex-shrink: 0;
		margin-left: 8px;
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
</style>
