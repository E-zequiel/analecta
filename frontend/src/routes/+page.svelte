<script lang="ts">
	import { onMount } from 'svelte';
	import { listen } from '@tauri-apps/api/event';
	import { entries as entriesApi, type Entry } from '$lib/api/client';
	import { selectedTag } from '$lib/stores/ui';
	import SearchInput from '$lib/components/SearchInput.svelte';
	import FilterBar from '$lib/components/FilterBar.svelte';
	import EntryList from '$lib/components/EntryList.svelte';

	let filter = $state('all');
	let searchQuery = $state('');
	let entryList = $state<Entry[]>([]);
	let loading = $state(false);

	$effect(() => {
		const params = {
			status: filter === 'all' ? undefined : filter,
			tag: $selectedTag ?? undefined,
			q: searchQuery || undefined
		};

		let cancelled = false;
		loading = true;

		entriesApi
			.list(params)
			.then((data) => {
				if (!cancelled) {
					entryList = data;
					loading = false;
				}
			})
			.catch(() => {
				if (!cancelled) loading = false;
			});

		return () => {
			cancelled = true;
		};
	});

	onMount(() => {
		const unlistenPromise = listen('entry_added', () => {
			entriesApi
				.list({
					status: filter === 'all' ? undefined : filter,
					tag: $selectedTag ?? undefined,
					q: searchQuery || undefined
				})
				.then((data) => {
					entryList = data;
				})
				.catch(() => {});
		});

		return () => {
			unlistenPromise.then((u) => u());
		};
	});
</script>

<div class="dashboard">
	<div class="toolbar">
		<SearchInput onSearch={(q) => (searchQuery = q)} />
		<FilterBar active={filter} onChange={(f) => (filter = f)} />
	</div>
	<div class="list-wrap">
		<EntryList entries={entryList} {loading} />
	</div>
</div>

<style>
	.dashboard {
		display: flex;
		flex-direction: column;
		height: 100%;
	}

	.toolbar {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		padding: 0.75rem 1rem;
		border-bottom: 1px solid var(--border);
		background: var(--bg);
	}

	.list-wrap {
		flex: 1;
		overflow-y: auto;
		padding: 0.75rem 1rem;
	}
</style>
