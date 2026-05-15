<script lang="ts">
	import { X } from 'lucide-svelte';
	import { tabs, activeTabId, activateTab, closeTab } from '$lib/stores/tabs';
</script>

<div class="tab-bar" role="tablist">
	{#each $tabs as tab (tab.id)}
		<!-- svelte-ignore a11y_interactive_supports_focus -->
		<div
			class="tab"
			class:active={$activeTabId === tab.id}
			role="tab"
			aria-selected={$activeTabId === tab.id}
			onclick={() => activateTab(tab.id)}
			onmousedown={(e) => {
				if (e.button === 1) {
					e.preventDefault();
					closeTab(tab.id);
				}
			}}
			onkeydown={(e) => {
				if (e.key === 'Enter') activateTab(tab.id);
			}}
		>
			<span class="tab-title">{tab.title}</span>
			{#if $tabs.length > 1}
				<button
					class="tab-close"
					onclick={(e) => {
						e.stopPropagation();
						closeTab(tab.id);
					}}
					title="Close tab"
					tabindex="-1"
				>
					<X size={11} />
				</button>
			{/if}
		</div>
	{/each}
</div>

<style>
	.tab-bar {
		display: flex;
		align-items: stretch;
		overflow-x: auto;
		background: var(--bg-dark);
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
		min-height: 32px;
		scrollbar-width: none;
	}

	.tab-bar::-webkit-scrollbar {
		display: none;
	}

	.tab {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 6px 0 12px;
		border-right: 1px solid var(--border);
		cursor: pointer;
		color: var(--fg-muted);
		font-size: 0.75rem;
		white-space: nowrap;
		min-width: 80px;
		max-width: 200px;
		position: relative;
		transition: background 0.12s, color 0.12s;
		user-select: none;
	}

	.tab:hover {
		background: var(--bg-highlight);
		color: var(--fg);
	}

	.tab.active {
		background: var(--bg);
		color: var(--fg);
		border-bottom: 2px solid var(--accent);
	}

	.tab-title {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.tab-close {
		display: none;
		align-items: center;
		justify-content: center;
		width: 16px;
		height: 16px;
		padding: 0;
		background: none;
		border: none;
		border-radius: 2px;
		color: var(--fg-muted);
		cursor: pointer;
		flex-shrink: 0;
		transition: color 0.12s, background 0.12s;
	}

	.tab:hover .tab-close {
		display: flex;
	}

	.tab-close:hover {
		color: var(--fg);
		background: var(--bg-alt);
	}
</style>
