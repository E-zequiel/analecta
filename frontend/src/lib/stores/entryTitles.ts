import { writable } from 'svelte/store';
import { entries as entriesApi } from '$lib/api/client';
import { entryAddedTick, entryChangedTick } from './sse';

// lowercase title -> entry id, for resolving [[wikilinks]] to a real entry.
export const entryTitleIndex = writable<Map<string, number>>(new Map());

async function refresh(): Promise<void> {
	const rows = await entriesApi.getTitles();
	const map = new Map<string, number>();
	for (const row of rows) {
		map.set(row.title.toLowerCase(), row.id);
	}
	entryTitleIndex.set(map);
}

let initialized = false;

// Loads the index once, then keeps it in sync as entries are added/edited/renamed.
export function ensureEntryTitleIndexLoaded(): void {
	if (initialized) return;
	initialized = true;
	entryAddedTick.subscribe(() => void refresh());
	entryChangedTick.subscribe(() => void refresh());
}
