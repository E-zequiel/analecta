import { get } from 'svelte/store';
import { port } from '$lib/stores/sidecar';

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

export class ApiError extends Error {
	constructor(
		public readonly status: number,
		message: string
	) {
		super(message);
		this.name = 'ApiError';
	}
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Entry {
	id: number;
	title: string;
	url: string;
	file_path: string;
	source_type: string;
	created_at: string;
	updated_at: string;
	status: string;
	tags: string[];
	flags: string[];
}

export interface FtsPatch {
	title: string;
	content: string;
}

export interface EntryPatch {
	status?: string;
	tags?: string[];
	flags?: string[];
	fts?: FtsPatch;
}

export interface Tag {
	name: string;
	count: number;
}

export interface AppConfig {
	vault_path: string;
	font_variant: 'regular' | 'nerd' | 'custom';
	ui_font_size: number;
	reading_font_size: number;
	custom_font_path: string | null;
	update_channel: 'stable' | 'dev';
	virustotal_enabled: boolean;
	theme: 'dark' | 'light';
	accent_color: 'red' | 'yellow' | 'green' | 'cyan';
}

export interface AppConfigUpdate {
	vault_path?: string;
	font_variant?: 'regular' | 'nerd' | 'custom';
	ui_font_size?: number;
	reading_font_size?: number;
	custom_font_path?: string | null;
	update_channel?: 'stable' | 'dev';
	virustotal_enabled?: boolean;
	theme?: 'dark' | 'light';
	accent_color?: 'red' | 'yellow' | 'green' | 'cyan';
}

export interface ScanResult {
	entry_id: number;
	verdict: string;
	malicious: number;
	suspicious: number;
	undetected: number;
	harmless: number;
	total: number;
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string, opts: RequestInit = {}): Promise<T> {
	const p = get(port);
	if (p === null) throw new ApiError(0, 'Sidecar not ready');

	const hasBody = opts.body !== undefined;
	const res = await fetch(`http://localhost:${p}/api/v1${path}`, {
		...opts,
		headers: {
			...(hasBody ? { 'Content-Type': 'application/json' } : {}),
			...opts.headers
		}
	});

	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			if (typeof body.detail === 'string') detail = body.detail;
		} catch {
			// ignore parse errors
		}
		throw new ApiError(res.status, detail);
	}

	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Resource helpers
// ---------------------------------------------------------------------------

export const entries = {
	list(params?: { status?: string; flag?: string; exclude_flag?: string; tag?: string; q?: string; sort_by?: 'title' | 'created_at'; sort_dir?: 'asc' | 'desc' }): Promise<Entry[]> {
		const filtered = Object.entries(params ?? {}).filter(
			(pair): pair is [string, string] => pair[1] !== undefined
		);
		const qs = filtered.length > 0 ? '?' + new URLSearchParams(filtered).toString() : '';
		return apiFetch<Entry[]>(`/entries${qs}`);
	},

	get(id: number): Promise<Entry> {
		return apiFetch<Entry>(`/entries/${id}`);
	},

	getCounts(): Promise<Record<string, number>> {
		return apiFetch<Record<string, number>>('/entries/counts');
	},

	patch(id: number, body: EntryPatch): Promise<Entry> {
		return apiFetch<Entry>(`/entries/${id}`, {
			method: 'PATCH',
			body: JSON.stringify(body)
		});
	},

	delete(id: number): Promise<void> {
		return apiFetch<void>(`/entries/${id}`, { method: 'DELETE' });
	}
};

export const tags = {
	list(): Promise<Tag[]> {
		return apiFetch<Tag[]>('/tags');
	},

	create(name: string): Promise<Tag> {
		return apiFetch<Tag>('/tags', {
			method: 'POST',
			body: JSON.stringify({ name })
		});
	},

	rename(name: string, newName: string): Promise<Tag> {
		return apiFetch<Tag>(`/tags/${encodeURIComponent(name)}`, {
			method: 'PUT',
			body: JSON.stringify({ new_name: newName })
		});
	},

	delete(name: string): Promise<void> {
		return apiFetch<void>(`/tags/${encodeURIComponent(name)}`, { method: 'DELETE' });
	}
};

export const extract = {
	url(url: string): Promise<Entry> {
		return apiFetch<Entry>('/extract', {
			method: 'POST',
			body: JSON.stringify({ url })
		});
	}
};

export const config = {
	get(): Promise<AppConfig> {
		return apiFetch<AppConfig>('/config');
	},

	update(body: AppConfigUpdate): Promise<AppConfig> {
		return apiFetch<AppConfig>('/config', {
			method: 'PUT',
			body: JSON.stringify(body)
		});
	}
};

export const security = {
	keyExists(): Promise<{ exists: boolean }> {
		return apiFetch<{ exists: boolean }>('/security/virustotal/key/exists');
	},

	setKey(value: string): Promise<void> {
		return apiFetch<void>('/security/virustotal/key', {
			method: 'PUT',
			body: JSON.stringify({ value })
		});
	},

	scan(entry_id: number): Promise<ScanResult> {
		return apiFetch<ScanResult>('/security/virustotal/scan', {
			method: 'POST',
			body: JSON.stringify({ entry_id })
		});
	}
};

export const pkm = {
	parseUrl(url: string): Promise<{ entry_id: number | null }> {
		return apiFetch<{ entry_id: number | null }>(
			`/pkm/parse-url?url=${encodeURIComponent(url)}`
		);
	}
};
