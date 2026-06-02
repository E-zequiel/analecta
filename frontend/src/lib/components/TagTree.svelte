<script lang="ts">
	import { onMount } from 'svelte';
	import { tags as tagsApi, type Tag } from '$lib/api/client';
	import { selectedTag } from '$lib/stores/ui';
	import { entryAddedTick } from '$lib/stores/sse';

	let tagList = $state<Tag[]>([]);

	async function fetchTags() {
		try {
			tagList = await tagsApi.list();
		} catch {
			// sidecar may not be ready yet
		}
	}

	onMount(() => {
		fetchTags();
	});

	let prevTick = 0;
	$effect(() => {
		const tick = $entryAddedTick;
		if (tick > prevTick) {
			prevTick = tick;
			fetchTags();
		}
	});

	function select(name: string) {
		selectedTag.update((current) => (current === name ? null : name));
	}
</script>

{#if tagList.length > 0}
	<div class="tag-tree">
		<p class="label">Tags</p>
		{#each tagList as tag (tag.name)}
			<button class:active={$selectedTag === tag.name} onclick={() => select(tag.name)}>
				<span class="name">{tag.name}</span>
				<span class="count">{tag.count}</span>
			</button>
		{/each}
	</div>
{/if}

<style>
	.tag-tree {
		display: flex;
		flex-direction: column;
		gap: 0.125rem;
		padding: 0.5rem 0;
	}

	.label {
		margin: 0 0 0.25rem 1rem;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--fg-muted);
	}

	button {
		display: flex;
		justify-content: space-between;
		align-items: center;
		width: 100%;
		padding: 0.3rem 1rem;
		margin: 0 0.5rem;
		width: calc(100% - 1rem);
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 12px;
		cursor: pointer;
		text-align: left;
		transition:
			color 0.15s,
			background 0.15s;
	}

	button:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	button.active {
		color: var(--accent);
		background: var(--bg-highlight);
	}

	.count {
		font-size: 11px;
		color: var(--fg-muted);
	}

	button.active .count {
		color: var(--accent-dark);
	}
</style>
