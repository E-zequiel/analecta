import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { get } from 'svelte/store';

import { entries } from './client';
import { port } from '#lib/stores/sidecar';

// T1 (read_state rename): the entry listing must serialize the renamed
// read-state query param — the old `status` key is gone.
describe('entries.list read_state query param', () => {
	beforeEach(() => {
		port.set(4242);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
		port.set(null);
	});

	it('serializes read_state as ?read_state=read', async () => {
		const fetchMock = vi.fn(() =>
			Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
		);
		vi.stubGlobal('fetch', fetchMock);

		await entries.list({ read_state: 'read' });

		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(get(port)).toBe(4242);
		const calls = fetchMock.mock.calls as unknown as Array<[string]>;
		const url = calls[0][0];
		expect(url).toContain('/entries?');
		expect(url).toContain('read_state=read');
		expect(url).not.toContain('status=');
	});
});
