import { writable } from 'svelte/store';
import { browser } from '$app/environment';

function persisted<T>(key: string, initialValue: T) {
	const stored = browser ? localStorage.getItem(key) : null;
	const initial: T = stored !== null ? (JSON.parse(stored) as T) : initialValue;
	const store = writable<T>(initial);
	if (browser) {
		store.subscribe((value) => {
			localStorage.setItem(key, JSON.stringify(value));
		});
	}
	return store;
}

export const selectedTag = writable<string | null>(null);
export const sidebarCollapsed = persisted('sidebar-collapsed', false);
export const sidebarWidth = persisted('sidebar-width', 240);
export const libraryOpen = writable(true);
export const expandedSections = writable<Set<string>>(new Set());
export const activeSection = writable<string>('library');
export const searchOpen = writable(false);
export const shortcutsOpen = writable(false);
export const lastViewedId = writable<number | null>(null);
export const expandAllSignal = writable(0);
export const pasteUrlSignal = writable(0);

export const rightSidebarOpen = persisted('right-sidebar-open', true);
export const rightSidebarWidth = persisted('right-sidebar-width', 240);

export const sidebarTagPreview = writable<string | null>(null);
export const dashboardPreviewEntryId = writable<number | null>(null);
