import { writable } from 'svelte/store';

export const selectedTag = writable<string | null>(null);
export const sidebarCollapsed = writable(false);
export const sidebarWidth = writable(260);
export const libraryOpen = writable(true);
export const expandedSections = writable<Set<string>>(new Set());
export const activeSection = writable<string>('all');
export const searchOpen = writable(false);
