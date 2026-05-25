import { writable, get } from 'svelte/store';
import { goto } from '$app/navigation';
import { activeSection } from './ui';
import { entries, config } from '$lib/api/client';

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
	gem: 'GEM',
	archive: 'ARCHIVE',
	tags: 'TAGS',
	collecta: 'COLLECTA'
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

export function navigateInSectionTab(sectionId: string): void {
	const tabId = `section-${sectionId}`;
	const label = SECTION_LABELS[sectionId] ?? sectionId.toUpperCase();
	const $tabs = get(tabs);

	// 1. Exact section tab already exists → activate it
	if ($tabs.find((t) => t.id === tabId)) {
		activeTabId.set(tabId);
		activeSection.set(sectionId);
		goto('/');
		return;
	}

	// 2. Any other section tab exists → replace it in-place (preserves tab bar order)
	const anySection = $tabs.find((t) => t.kind === 'section');
	if (anySection) {
		tabs.update((ts) =>
			ts.map((t) =>
				t.id === anySection.id
					? { id: tabId, kind: 'section' as TabKind, title: label, path: '/', sectionId }
					: t
			)
		);
		activeTabId.set(tabId);
		activeSection.set(sectionId);
		goto('/');
		return;
	}

	// 3. No section tabs at all → create a new one
	tabs.update((ts) => [...ts, makeSectionTab(sectionId)]);
	activeTabId.set(tabId);
	activeSection.set(sectionId);
	goto('/');
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
		const only = $tabs[0];
		if (only && only.kind === 'viewer') {
			const fallback = makeSectionTab('collecta');
			tabs.set([fallback]);
			activeTabId.set(fallback.id);
			activeSection.set('collecta');
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

	const existing = $tabs.find((t) => t.id === tabId);
	if (existing) {
		activateTab(tabId);
		return;
	}

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

export function reorderTabs(fromId: string, toId: string): void {
	tabs.update((ts) => {
		const from = ts.findIndex((t) => t.id === fromId);
		const to = ts.findIndex((t) => t.id === toId);
		if (from === -1 || to === -1 || from === to) return ts;
		const result = [...ts];
		const [moved] = result.splice(from, 1);
		result.splice(to, 0, moved);
		return result;
	});
}

export function saveTabs(): void {
	config.update({
		open_tab_ids: get(tabs).map((t) => t.id),
		active_tab_id: get(activeTabId)
	});
}

export async function restoreTabsFromConfig(
	tabIds: string[],
	activeId: string
): Promise<void> {
	const restored: AppTab[] = [];
	for (const id of tabIds) {
		if (id.startsWith('section-')) {
			restored.push(makeSectionTab(id.slice('section-'.length)));
		} else if (id.startsWith('viewer-')) {
			const entryId = parseInt(id.slice('viewer-'.length), 10);
			if (!isNaN(entryId)) {
				try {
					const entry = await entries.get(entryId);
					restored.push({
						id,
						kind: 'viewer',
						title: entry.title,
						path: `/viewer/${entryId}`,
						entryId
					});
				} catch {
					// entry was deleted — skip silently
				}
			}
		}
	}
	if (restored.length === 0) restored.push(makeSectionTab('collecta'));
	tabs.set(restored);
	const valid = restored.find((t) => t.id === activeId) ?? restored[0];
	activeTabId.set(valid.id);
	if (valid.kind === 'section' && valid.sectionId) activeSection.set(valid.sectionId);
}
