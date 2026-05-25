import { writable } from 'svelte/store';

export const selectedTag = writable<string | null>(null);
export const sidebarCollapsed = writable(false);
export const sidebarWidth = writable(200);
export const libraryOpen = writable(true);
export const expandedSections = writable<Set<string>>(new Set());
export const activeSection = writable<string>('library');
export const searchOpen = writable(false);
export const lastViewedId = writable<number | null>(null);
export const tagsExpanded = writable(false);
export const expandAllSignal = writable(0);
export const collapseAllSignal = writable(0);
