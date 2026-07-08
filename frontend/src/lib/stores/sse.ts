import { writable } from 'svelte/store';
import type { Entry } from '$lib/api/client';

// Incremented each time the backend emits an `entry_added` SSE event.
export const entryAddedTick = writable(0);

// Incremented when any entry is mutated. Sidebar and page subscribe to refresh.
export const entryChangedTick = writable(0);

// Incremented specifically on a `vault_rescanned` SSE event (manual or automatic
// reconciliation of files edited outside Analecta). Open viewers use this — not
// entryChangedTick, which also fires on ordinary tag/flag/status patches — to
// re-read their file from disk without over-firing on unrelated entry edits.
export const vaultRescannedTick = writable(0);

// Carries the most-recently patched entry so viewers can update instantly
// without an extra HTTP GET. Set alongside entryChangedTick on every patch.
export const lastChangedEntry = writable<Entry | null>(null);
