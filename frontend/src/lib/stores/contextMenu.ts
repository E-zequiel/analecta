import { writable } from 'svelte/store';

export interface ContextMenuEntry {
	id: number;
	title: string;
	url: string;
	file_path: string;
	flags: string[];
}

export interface ContextMenuState {
	visible: boolean;
	x: number;
	y: number;
	entry: ContextMenuEntry | null;
}

export const contextMenu = writable<ContextMenuState>({
	visible: false,
	x: 0,
	y: 0,
	entry: null
});

export function showContextMenu(e: MouseEvent, entry: ContextMenuEntry): void {
	e.preventDefault();
	contextMenu.set({ visible: true, x: e.clientX, y: e.clientY, entry });
}

export function hideContextMenu(): void {
	contextMenu.update((s) => ({ ...s, visible: false, entry: null }));
}
