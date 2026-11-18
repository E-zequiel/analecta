import { writable } from 'svelte/store';

export const port = writable<number | null>(null);

// Per-run sidecar session token (D5); null in dev/browser runs.
export const sessionToken = writable<string | null>(null);
