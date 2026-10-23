// Unit tests for the updater's update-check status mapping — closes the
// deferred coverage advisories R3-untested-status-mapping,
// R3-coverage-updatecheck-1, R3-unavailable-branch-untested and U4-review
// R3-2 from feat/settings-version-update-check.
//
// electron-updater semantics (see updater.ts): checkForUpdates() resolves
// null only when the updater is inactive (unpacked/dev); "not available"
// resolves with isUpdateAvailable: false; errors reject and also fire the
// 'error' event, which initUpdater's listener logs.
import { app } from 'electron';
import type { BrowserWindow } from 'electron';
import { autoUpdater } from 'electron-updater';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { downloadUpdate, initUpdater, quitAndInstall, runUpdateCheck } from './updater';

vi.mock('electron', () => ({
	app: { isPackaged: true },
}));

vi.mock('electron-updater', () => ({
	autoUpdater: {
		autoDownload: undefined,
		on: vi.fn(),
		checkForUpdates: vi.fn(),
		downloadUpdate: vi.fn(),
		quitAndInstall: vi.fn(),
	},
}));

type CheckResult = Awaited<ReturnType<typeof autoUpdater.checkForUpdates>>;

function fakeResult(fields: { isUpdateAvailable: boolean; version: string }): CheckResult {
	return {
		isUpdateAvailable: fields.isUpdateAvailable,
		updateInfo: { version: fields.version },
	} as unknown as CheckResult;
}

const win = { webContents: { send: vi.fn() } } as unknown as BrowserWindow;

// on()'s listener signature is provider-typed (UpdateInfo); the test feeds
// plain payloads, so read the recorded calls through an unknown-typed view.
const onCalls = vi.mocked(autoUpdater.on).mock.calls as Array<[string, (info: unknown) => void]>;

beforeEach(() => {
	vi.clearAllMocks();
	(autoUpdater as { autoDownload: boolean | undefined }).autoDownload = undefined;
	app.isPackaged = true;
});

describe('runUpdateCheck', () => {
	test('null result means an inactive updater → unavailable', async () => {
		vi.mocked(autoUpdater.checkForUpdates).mockResolvedValue(null);
		await expect(runUpdateCheck()).resolves.toEqual({ status: 'unavailable' });
	});

	test('isUpdateAvailable: true → available with the update version', async () => {
		vi.mocked(autoUpdater.checkForUpdates).mockResolvedValue(
			fakeResult({ isUpdateAvailable: true, version: '1.2.3' })
		);
		await expect(runUpdateCheck()).resolves.toEqual({
			status: 'available',
			version: '1.2.3',
		});
	});

	test('isUpdateAvailable: false → up-to-date', async () => {
		vi.mocked(autoUpdater.checkForUpdates).mockResolvedValue(
			fakeResult({ isUpdateAvailable: false, version: '0.6.0' })
		);
		await expect(runUpdateCheck()).resolves.toEqual({ status: 'up-to-date' });
	});

	test('rejection → error (the error event already logged the cause)', async () => {
		vi.mocked(autoUpdater.checkForUpdates).mockRejectedValue(new Error('network down'));
		await expect(runUpdateCheck()).resolves.toEqual({ status: 'error' });
	});
});

describe('initUpdater', () => {
	test('unpackaged build: no configuration, no listeners', () => {
		app.isPackaged = false;
		initUpdater(win);
		expect(autoUpdater.on).not.toHaveBeenCalled();
		expect(autoUpdater.autoDownload).toBeUndefined();
	});

	test('packaged build: disables auto-download and forwards update-available', () => {
		initUpdater(win);
		expect(autoUpdater.autoDownload).toBe(false);
		expect(onCalls.map(([event]) => event)).toEqual(['update-available', 'error']);

		const forwardEntry = onCalls.find(([event]) => event === 'update-available');
		expect(forwardEntry).toBeDefined();
		forwardEntry![1]('1.2.3');
		expect(win.webContents.send).toHaveBeenCalledWith('update-available', '1.2.3');
	});

	test('packaged build: the error listener logs once', () => {
		const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
		initUpdater(win);
		// Look the listener up by event name instead of positional index —
		// registration order must not decide which hook each test exercises.
		const errorEntry = onCalls.find(([event]) => event === 'error');
		expect(errorEntry).toBeDefined();
		errorEntry![1](new Error('boom'));
		expect(errorSpy).toHaveBeenCalledWith('[updater]', expect.any(Error));
	});

	test('downloadUpdate delegates and resolves void', async () => {
		vi.mocked(autoUpdater.downloadUpdate).mockResolvedValue(null as never);
		await expect(downloadUpdate()).resolves.toBeUndefined();
	});

	test('quitAndInstall delegates', () => {
		quitAndInstall();
		expect(autoUpdater.quitAndInstall).toHaveBeenCalledOnce();
	});
});
