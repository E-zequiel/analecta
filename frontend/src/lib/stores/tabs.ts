import { writable, get } from 'svelte/store';
import { goto } from '$app/navigation';
import { activeSection } from './ui';

export type TabKind = 'section' | 'viewer';

export interface AppTab {
	id: string;
	kind: TabKind;
	title: string;
	path: string;
	sectionId?: string;
	entryId?: number;
}

const SECTION_LABELS: Record<string, string> = {
	library: 'LIBRARY',
	unread: 'UNREAD',
	read: 'READ',
	bookmark: 'BOOKMARK',
	gem: 'GEM'
};

function makeSectionTab(sectionId: string): AppTab {
	return {
		id: `section-${sectionId}`,
		kind: 'section',
		title: SECTION_LABELS[sectionId] ?? sectionId.toUpperCase(),
		path: '/',
		sectionId
	};
}

export const tabs = writable<AppTab[]>([makeSectionTab('library')]);
export const activeTabId = writable<string>('section-library');

export function openEntryTab(entryId: number, title: string, background = false): void {
	const tabId = `viewer-${entryId}`;
	tabs.update((ts) => {
		if (ts.find((t) => t.id === tabId)) return ts;
		return [...ts, { id: tabId, kind: 'viewer', title, path: `/viewer/${entryId}`, entryId }];
	});
	if (!background) {
		activeTabId.set(tabId);
		goto(`/viewer/${entryId}`);
	}
}

export function openSectionTab(sectionId: string): void {
	const tabId = `section-${sectionId}`;
	if (!get(tabs).find((t) => t.id === tabId)) {
		tabs.update((ts) => [...ts, makeSectionTab(sectionId)]);
	}
	activeTabId.set(tabId);
	activeSection.set(sectionId);
	goto('/');
}

export function activateTab(tabId: string): void {
	const tab = get(tabs).find((t) => t.id === tabId);
	if (!tab) return;
	activeTabId.set(tabId);
	if (tab.kind === 'section' && tab.sectionId) activeSection.set(tab.sectionId);
	goto(tab.path);
}

export function closeTab(tabId: string): void {
	const $tabs = get(tabs);
	const idx = $tabs.findIndex((t) => t.id === tabId);
	if (idx === -1) return;

	if ($tabs.length <= 1) {
		// Last tab — if it's a viewer, replace with the default library tab
		const only = $tabs[0];
		if (only && only.kind === 'viewer') {
			const lib = makeSectionTab('library');
			tabs.set([lib]);
			activeTabId.set(lib.id);
			activeSection.set('library');
			goto('/');
		}
		return;
	}

	const newTabs = $tabs.filter((t) => t.id !== tabId);
	tabs.set(newTabs);
	if (get(activeTabId) === tabId) {
		const next = newTabs[Math.min(idx, newTabs.length - 1)];
		activeTabId.set(next.id);
		if (next.kind === 'section' && next.sectionId) activeSection.set(next.sectionId);
		goto(next.path);
	}
}

export function navigateInTab(entryId: number, title: string): void {
	const tabId = `viewer-${entryId}`;
	const $tabs = get(tabs);
	const $activeId = get(activeTabId);

	// If this entry already has its own tab open, just activate it
	const existing = $tabs.find((t) => t.id === tabId);
	if (existing) {
		activateTab(tabId);
		return;
	}

	// Replace the current tab in-place with the new entry
	tabs.update((ts) =>
		ts.map((t) =>
			t.id === $activeId
				? { id: tabId, kind: 'viewer' as TabKind, title, path: `/viewer/${entryId}`, entryId }
				: t
		)
	);
	activeTabId.set(tabId);
	goto(`/viewer/${entryId}`);
}

export function ensureEntryTab(entryId: number, title: string): void {
	const tabId = `viewer-${entryId}`;
	tabs.update((ts) => {
		const existing = ts.find((t) => t.id === tabId);
		if (existing) return ts.map((t) => (t.id === tabId ? { ...t, title } : t));
		return [...ts, { id: tabId, kind: 'viewer', title, path: `/viewer/${entryId}`, entryId }];
	});
	activeTabId.set(tabId);
}

export function syncActiveTabFromPath(pathname: string): void {
	const $tabs = get(tabs);
	if (pathname.startsWith('/viewer/')) {
		const id = parseInt(pathname.split('/').pop() ?? '', 10);
		const tabId = `viewer-${id}`;
		if ($tabs.find((t) => t.id === tabId)) activeTabId.set(tabId);
	} else if (pathname === '/') {
		const secId = get(activeSection);
		const tabId = `section-${secId}`;
		if ($tabs.find((t) => t.id === tabId)) activeTabId.set(tabId);
	}
}
