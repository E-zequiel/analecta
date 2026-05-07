import { writable } from 'svelte/store';

export const port = writable<number | null>(null);
