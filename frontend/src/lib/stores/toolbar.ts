import { writable } from 'svelte/store';
import type { Entry } from '$lib/api/client';

// ── Viewer ─────────────────────────────────────────────────────────

export const viewerEntry = writable<Entry | null>(null);
export const viewerFontSize = writable<number>(17);
export const viewerTagsOpen = writable<boolean>(false);
export const viewerBacklinksOpen = writable<boolean>(false);

export type ViewerActions = {
	setStatus: (s: string) => Promise<void>;
	toggleFlag: (f: string) => Promise<void>;
	adjustFont: (delta: number) => void;
	copyUrl: () => Promise<void>;
	deleteEntry: () => Promise<void>;
	goBack: () => void;
	goToEditor: () => void;
};
export const viewerActions = writable<ViewerActions | null>(null);

// ── Editor ─────────────────────────────────────────────────────────

export const editorSaving = writable<boolean>(false);
export const editorSaved = writable<boolean>(false);
export const editorShowPreview = writable<boolean>(false);
export const editorIsDirty = writable<boolean>(false);

export type EditorActions = {
	save: () => Promise<void>;
	revert: () => void;
	togglePreview: () => void;
	goBack: () => void;
};
export const editorActions = writable<EditorActions | null>(null);
