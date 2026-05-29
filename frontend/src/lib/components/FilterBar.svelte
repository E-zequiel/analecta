<script lang="ts">
	const { active, onChange }: { active: string; onChange: (status: string) => void } = $props();

	const filters = ['all', 'unread', 'read', 'favorite', 'recommend'];
</script>

<div class="filter-bar">
	{#each filters as f (f)}
		<button class:active={active === f} onclick={() => onChange(f)}>
			{f}
		</button>
	{/each}
</div>

<style>
	.filter-bar {
		display: flex;
		gap: 0.25rem;
		flex-wrap: wrap;
	}

	button {
		padding: 0.25rem 0.6rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 12px;
		cursor: pointer;
		transition:
			color 0.15s,
			background 0.15s,
			border-color 0.15s;
	}

	button:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	button.active {
		color: var(--accent);
		border-color: var(--accent-dark);
		background: var(--bg-highlight);
	}
</style>
