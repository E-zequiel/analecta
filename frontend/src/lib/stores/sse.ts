import { writable } from 'svelte/store';

// Incremented each time the backend emits an `entry_added` SSE event.
// Components subscribe to detect new entries without polling.
export const entryAddedTick = writable(0);

// Incremented when an entry is mutated (status change, delete) in the viewer.
// Sidebar subscribes to refresh counts and section lists.
export const entryChangedTick = writable(0);
