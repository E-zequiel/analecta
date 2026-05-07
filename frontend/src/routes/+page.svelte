<script lang="ts">
	import { onMount } from 'svelte';
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { exists } from '@tauri-apps/plugin-fs';
	import { entries as entriesApi, config as configApi, type Entry } from '$lib/api/client';
	import { selectedTag } from '$lib/stores/ui';
	import { entryAddedTick } from '$lib/stores/sse';
	import SearchInput from '$lib/components/SearchInput.svelte';
	import FilterBar from '$lib/components/FilterBar.svelte';
	import EntryList from '$lib/components/EntryList.svelte';

	let filter = $state('all');
	let searchQuery = $state('');
	let entryList = $state<Entry[]>([]);
	let loading = $state(false);
	let checking = $state(true);

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

	onMount(async () => {
		try {
			const cfg = await configApi.get();
			const vaultExists = await exists(cfg.vault_path);
			if (!vaultExists) {
				goto('/first-run');
				return;
			}
		} catch {
			// if check fails, stay on dashboard
		}
		checking = false;
	});

	let prevTick = 0;
	$effect(() => {
		const tick = $entryAddedTick;
		if (tick <= prevTick) return;
		prevTick = tick;
		const params = untrack(() => ({
			status: filter === 'all' ? undefined : filter,
			tag: $selectedTag ?? undefined,
			q: searchQuery || undefined
		}));
		entriesApi
			.list(params)
			.then((data) => {
				entryList = data;
			})
			.catch(() => {});
	});
</script>

{#if !checking}
	<div class="dashboard">
		<div class="toolbar">
			<SearchInput onSearch={(q) => (searchQuery = q)} />
			<FilterBar active={filter} onChange={(f) => (filter = f)} />
		</div>
		<div class="list-wrap">
			<EntryList entries={entryList} {loading} />
		</div>
	</div>
{/if}

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
