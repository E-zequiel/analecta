// Unit tests for the renderer-side update-check plumbing — closes the
// deferred coverage advisories R3-coverage-updatecheck-1 and
// R3-unavailable-branch-untested from feat/settings-version-update-check:
// the runtime narrow (isUpdateCheckResult) against payload drift, and the
// 30 s timeout guard against a hung IPC.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { checkUpdate, isUpdateCheckResult } from './electron';

const invoke = vi.fn<(channel: string, ...args: unknown[]) => Promise<unknown>>();
const electronAPI = { invoke, on: vi.fn() };

beforeEach(() => {
	vi.clearAllMocks();
	window.electronAPI = electronAPI;
});

afterEach(() => {
	vi.useRealTimers();
	delete (window as { electronAPI?: unknown }).electronAPI;
	vi.restoreAllMocks();
});

describe('isUpdateCheckResult', () => {
	test.each([
		[{ status: 'available', version: '1.2.3' }, true],
		[{ status: 'available' }, false],
		[{ status: 'available', version: 7 }, false],
		[{ status: 'up-to-date' }, true],
		[{ status: 'unavailable' }, true],
		[{ status: 'error' }, true],
		[{ status: 'unexpected' }, false],
		[{ status: null }, false],
		[{}, false],
		[null, false],
		['available', false],
		[42, false],
	])('%j → %s', (value, expected) => {
		expect(isUpdateCheckResult(value)).toBe(expected);
	});
});

describe('checkUpdate', () => {
	test('passes a valid payload through', async () => {
		const payload = { status: 'available', version: '1.2.3' };
		invoke.mockResolvedValueOnce(payload);
		await expect(checkUpdate()).resolves.toEqual(payload);
		expect(invoke).toHaveBeenCalledWith('check-update');
	});

	test('passes the unavailable status through', async () => {
		invoke.mockResolvedValueOnce({ status: 'unavailable' });
		await expect(checkUpdate()).resolves.toEqual({ status: 'unavailable' });
	});

	test('malformed payload → error status, with a warning', async () => {
		const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
		// available without a string version is the drift the runtime narrow
		// exists to catch.
		invoke.mockResolvedValueOnce({ status: 'available' });
		await expect(checkUpdate()).resolves.toEqual({ status: 'error' });
		expect(warnSpy).toHaveBeenCalledOnce();
	});

	test('a hung IPC times out after 30 s', async () => {
		vi.useFakeTimers();
		invoke.mockImplementation(() => new Promise(() => {}));
		const pending = checkUpdate();
		vi.advanceTimersByTime(30_000);
		await expect(pending).rejects.toThrow('Update check timed out');
	});

	test('a successful check clears the timeout guard', async () => {
		const clearSpy = vi.spyOn(globalThis, 'clearTimeout');
		invoke.mockResolvedValueOnce({ status: 'up-to-date' });
		await checkUpdate();
		expect(clearSpy).toHaveBeenCalledOnce();
	});
});
