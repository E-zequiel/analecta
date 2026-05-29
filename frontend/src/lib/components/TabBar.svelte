<script lang="ts">
	import { X } from '@lucide/svelte';
	import {
		tabs,
		activeTabId,
		activateTab,
		closeTab,
		reorderTabs,
		saveTabs,
	} from '$lib/stores/tabs';

	let draggedId = $state<string | null>(null);
	let dragOverId = $state<string | null>(null);
</script>

<div class="tab-bar" role="tablist">
	{#each $tabs as tab (tab.id)}
		<div
			class="tab"
			class:active={$activeTabId === tab.id}
			class:drag-over={dragOverId === tab.id && draggedId !== tab.id}
			role="tab"
			aria-selected={$activeTabId === tab.id}
			tabindex="0"
			draggable={true}
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
			ondragstart={() => {
				draggedId = tab.id;
			}}
			ondragover={(e) => {
				e.preventDefault();
				dragOverId = tab.id;
			}}
			ondrop={() => {
				if (draggedId && dragOverId && draggedId !== dragOverId) {
					reorderTabs(draggedId, dragOverId);
					saveTabs();
				}
				draggedId = null;
				dragOverId = null;
			}}
			ondragend={() => {
				draggedId = null;
				dragOverId = null;
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
		align-items: flex-end;
		overflow-x: auto;
		background: var(--bg-dark);
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
		min-height: 40px;
		padding: 4px 4px 0;
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
		border: 1px solid var(--border);
		border-bottom: none;
		border-radius: 6px 6px 0 0;
		margin-bottom: -1px;
		background: var(--bg-alt);
		cursor: pointer;
		color: var(--fg-muted);
		font-size: 0.75rem;
		white-space: nowrap;
		min-width: 80px;
		max-width: 200px;
		height: 33px;
		position: relative;
		transition:
			background 0.12s,
			color 0.12s;
		user-select: none;
	}

	.tab:hover:not(.active) {
		background: var(--bg-highlight);
		color: var(--fg);
	}

	.tab.active {
		background: var(--bg);
		border-bottom-color: var(--bg);
		color: var(--fg);
		height: 36px;
	}

	.tab.drag-over {
		border-left: 2px solid var(--accent);
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
		transition:
			color 0.12s,
			background 0.12s;
	}

	.tab:hover .tab-close {
		display: flex;
	}

	.tab-close:hover {
		color: var(--fg);
		background: var(--bg-alt);
	}
</style>
