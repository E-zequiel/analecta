import { writable } from 'svelte/store';

// Incremented each time the backend emits an `entry_added` SSE event.
// Components subscribe to detect new entries without polling.
export const entryAddedTick = writable(0);
