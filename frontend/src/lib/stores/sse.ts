import { writable } from 'svelte/store';
import type { Entry } from '$lib/api/client';

// Incremented each time the backend emits an `entry_added` SSE event.
export const entryAddedTick = writable(0);

// Incremented when any entry is mutated. Sidebar and page subscribe to refresh.
export const entryChangedTick = writable(0);

// Carries the most-recently patched entry so viewers can update instantly
// without an extra HTTP GET. Set alongside entryChangedTick on every patch.
export const lastChangedEntry = writable<Entry | null>(null);
