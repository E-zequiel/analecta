<script lang="ts">
	import {
		ArrowDownAZ,
		ArrowDownZA,
		ArrowDownWideNarrow,
		ArrowDownNarrowWide
	} from 'lucide-svelte';

	interface Props {
		sortBy: 'title' | 'created_at';
		sortDir: 'asc' | 'desc';
		onsort: (sortBy: 'title' | 'created_at', sortDir: 'asc' | 'desc') => void;
	}

	let { sortBy, sortDir, onsort }: Props = $props();

	const OPTIONS = [
		{ sortBy: 'title' as const, sortDir: 'asc' as const, Icon: ArrowDownAZ, title: 'Title A→Z' },
		{ sortBy: 'title' as const, sortDir: 'desc' as const, Icon: ArrowDownZA, title: 'Title Z→A' },
		{
			sortBy: 'created_at' as const,
			sortDir: 'desc' as const,
			Icon: ArrowDownWideNarrow,
			title: 'Newest first'
		},
		{
			sortBy: 'created_at' as const,
			sortDir: 'asc' as const,
			Icon: ArrowDownNarrowWide,
			title: 'Oldest first'
		}
	];
</script>

<div class="sort-bar" role="group" aria-label="Sort order">
	{#each OPTIONS as opt}
		<button
			class="sort-btn"
			class:active={sortBy === opt.sortBy && sortDir === opt.sortDir}
			title={opt.title}
			onclick={() => onsort(opt.sortBy, opt.sortDir)}
		>
			<opt.Icon size={14} />
		</button>
	{/each}
</div>

<style>
	.sort-bar {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0.3rem 1rem 0;
	}

	.sort-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 26px;
		height: 26px;
		border: none;
		border-radius: 4px;
		background: transparent;
		color: var(--fg-muted);
		cursor: pointer;
		transition:
			color 0.15s,
			background 0.15s;
	}

	.sort-btn:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.sort-btn.active {
		color: var(--accent);
		background: var(--bg-alt);
	}
</style>
