import { writable } from 'svelte/store';

export const selectedTag = writable<string | null>(null);
