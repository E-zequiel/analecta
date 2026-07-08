<script lang="ts">
	import { onMount } from 'svelte';
	import { openDialog, updateVaultScope, relaunch, setCloseToTray } from '$lib/platform';
	import { config as configApi, system as systemApi } from '$lib/api/client';
	import { applyFont } from '$lib/font';
	import { tooltip } from '$lib/actions/tooltip';

	const ACCENT_OPTIONS = [
		{ id: 'red', label: 'Red' },
		{ id: 'yellow', label: 'Yellow' },
		{ id: 'green', label: 'Green' },
		{ id: 'cyan', label: 'Cyan' },
	] as const satisfies { id: 'red' | 'yellow' | 'green' | 'cyan'; label: string }[];

	let form = $state({
		vault_path: '',
		font_variant: 'regular' as 'regular' | 'bricolage',
		ui_font_size: 17.0,
		reading_font_size: 18.0,
		theme: 'dark' as 'dark' | 'light',
		accent_color: 'yellow' as 'red' | 'yellow' | 'green' | 'cyan',
		close_to_tray: false,
	});
	let initialVaultPath = $state('');
	let error = $state('');

	// Per-field saved indicators
	let vaultSaved = $state(false);
	let fontVariantSaved = $state(false);
	let uiFontSaved = $state(false);
	let readingFontSaved = $state(false);
	let themeSaved = $state(false);
	let accentSaved = $state(false);
	let closeToTraySaved = $state(false);

	let browsing = $state(false);
	let rescanning = $state(false);
	let rescanResult = $state('');

	let uiFontTimer: ReturnType<typeof setTimeout> | null = null;
	let readingFontTimer: ReturnType<typeof setTimeout> | null = null;

	// Inline editing of font size chips
	let editingUiFont = $state(false);
	let editingReadingFont = $state(false);
	let origUiFont = 0;
	let origReadingFont = 0;

	function focusOnMount(node: HTMLInputElement) {
		node.focus();
		node.select();
	}

	function applyCurrentFont(): void {
		applyFont(
			form.font_variant,
			form.ui_font_size,
			form.reading_font_size,
			form.theme,
			form.accent_color
		);
	}

	function startEditUiFont() {
		origUiFont = form.ui_font_size;
		editingUiFont = true;
	}
	function commitUiFont() {
		form.ui_font_size = Math.min(20, Math.max(10, form.ui_font_size));
		editingUiFont = false;
		onUiFontInput();
	}
	function cancelUiFont() {
		form.ui_font_size = origUiFont;
		applyCurrentFont();
		editingUiFont = false;
	}

	function startEditReadingFont() {
		origReadingFont = form.reading_font_size;
		editingReadingFont = true;
	}
	function commitReadingFont() {
		form.reading_font_size = Math.min(24, Math.max(12, form.reading_font_size));
		editingReadingFont = false;
		onReadingFontInput();
	}
	function cancelReadingFont() {
		form.reading_font_size = origReadingFont;
		applyCurrentFont();
		editingReadingFont = false;
	}

	onMount(async () => {
		try {
			const cfg = await configApi.get();
			form = {
				vault_path: cfg.vault_path,
				font_variant: cfg.font_variant,
				ui_font_size: cfg.ui_font_size,
				reading_font_size: cfg.reading_font_size,
				theme: cfg.theme,
				accent_color: cfg.accent_color,
				close_to_tray: cfg.close_to_tray,
			};
			initialVaultPath = cfg.vault_path;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	});

	function flash(set: (v: boolean) => void) {
		set(true);
		setTimeout(() => set(false), 2500);
	}

	async function autoSaveVaultPath() {
		try {
			await configApi.update({ vault_path: form.vault_path });
			if (form.vault_path !== initialVaultPath) {
				await updateVaultScope(form.vault_path);
				// Sidecar VaultIndex is not hot-reloadable; restart to apply new vault.
				await relaunch();
				return;
			}
			flash((v) => (vaultSaved = v));
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	async function browseVault() {
		browsing = true;
		error = '';
		try {
			const selected = await openDialog({ properties: ['openDirectory'] });
			if (typeof selected === 'string') {
				form.vault_path = selected;
				await autoSaveVaultPath();
			}
		} catch {
			error = 'File picker unavailable — type the path directly in the field below.';
		} finally {
			browsing = false;
		}
	}

	async function rescanVault() {
		rescanning = true;
		error = '';
		rescanResult = '';
		try {
			// Every entry in the vault is re-derived from its file regardless
			// of this count — `updated` reports how many were actually found
			// out of sync, not how many were touched.
			const { updated } = await systemApi.rescan();
			rescanResult =
				updated === 0
					? 'No entries needed updating.'
					: `Updated ${updated} ${updated === 1 ? 'entry' : 'entries'}.`;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			rescanning = false;
		}
	}

	async function autoSaveFontVariant() {
		try {
			await applyCurrentFont();
			await configApi.update({ font_variant: form.font_variant });
			flash((v) => (fontVariantSaved = v));
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	function onUiFontInput() {
		applyCurrentFont();
		if (uiFontTimer) clearTimeout(uiFontTimer);
		uiFontTimer = setTimeout(async () => {
			try {
				await configApi.update({ ui_font_size: form.ui_font_size });
				flash((v) => (uiFontSaved = v));
			} catch (err) {
				error = err instanceof Error ? err.message : String(err);
			}
		}, 300);
	}

	function onReadingFontInput() {
		applyCurrentFont();
		if (readingFontTimer) clearTimeout(readingFontTimer);
		readingFontTimer = setTimeout(async () => {
			try {
				await configApi.update({ reading_font_size: form.reading_font_size });
				flash((v) => (readingFontSaved = v));
			} catch (err) {
				error = err instanceof Error ? err.message : String(err);
			}
		}, 300);
	}

	function toggleTheme() {
		form.theme = form.theme === 'dark' ? 'light' : 'dark';
		autoSaveTheme();
	}

	async function autoSaveTheme() {
		try {
			await applyCurrentFont();
			await configApi.update({ theme: form.theme });
			flash((v) => (themeSaved = v));
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	function selectAccent(id: 'red' | 'yellow' | 'green' | 'cyan') {
		form.accent_color = id;
		autoSaveAccent();
	}

	async function autoSaveAccent() {
		try {
			await applyCurrentFont();
			await configApi.update({ accent_color: form.accent_color });
			flash((v) => (accentSaved = v));
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	async function autoSaveCloseToTray() {
		try {
			await configApi.update({ close_to_tray: form.close_to_tray });
			await setCloseToTray(form.close_to_tray).catch(() => {});
			flash((v) => (closeToTraySaved = v));
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	function toggleCloseToTray() {
		form.close_to_tray = !form.close_to_tray;
		autoSaveCloseToTray();
	}
</script>

<div class="settings-page">
	<div class="settings-main">
		<h1>Settings</h1>

		{#if error}
			<p class="error">{error}</p>
		{/if}

		<section>
			<h2>Vault</h2>
			<div class="field">
				<label for="vault-path">
					Path {#if vaultSaved}<span class="saved-tag">✓</span>{/if}
				</label>
				<div class="path-row">
					<input
						id="vault-path"
						type="text"
						bind:value={form.vault_path}
						onblur={autoSaveVaultPath}
					/>
					<button onclick={browseVault} disabled={browsing}
						>{browsing ? 'Opening…' : 'Browse…'}</button
					>
				</div>
			</div>
			<div class="field">
				<label for="font-variant">
					Font {#if fontVariantSaved}<span class="saved-tag">✓</span>{/if}
				</label>
				<select id="font-variant" bind:value={form.font_variant} onchange={autoSaveFontVariant}>
					<option value="regular">JetBrains Mono</option>
					<option value="bricolage">Bricolage Grotesque</option>
				</select>
			</div>
			<div class="field">
				<label for="ui-font-size">
					UI font size {#if uiFontSaved}<span class="saved-tag">✓</span>{/if}
				</label>
				<div class="range-row">
					<span class="range-min">10</span>
					<input
						id="ui-font-size"
						type="range"
						min="10"
						max="20"
						step="0.5"
						bind:value={form.ui_font_size}
						oninput={onUiFontInput}
					/>
					<span class="range-max">20</span>
					{#if editingUiFont}
						<input
							use:focusOnMount
							class="range-value-input"
							type="number"
							min="10"
							max="20"
							step="0.5"
							style:font-size="{form.ui_font_size}px"
							bind:value={form.ui_font_size}
							onblur={commitUiFont}
							onkeydown={(e) => {
								if (e.key === 'Enter') commitUiFont();
								else if (e.key === 'Escape') cancelUiFont();
							}}
						/>
					{:else}
						<button
							class="range-value"
							style:font-size="{form.ui_font_size}px"
							onclick={startEditUiFont}
							use:tooltip={'Click to edit'}>{form.ui_font_size}px</button
						>
					{/if}
				</div>
			</div>
			<div class="field">
				<label for="reading-font-size">
					Reading font size {#if readingFontSaved}<span class="saved-tag">✓</span>{/if}
				</label>
				<div class="range-row">
					<span class="range-min">12</span>
					<input
						id="reading-font-size"
						type="range"
						min="12"
						max="24"
						step="0.5"
						bind:value={form.reading_font_size}
						oninput={onReadingFontInput}
					/>
					<span class="range-max">24</span>
					{#if editingReadingFont}
						<input
							use:focusOnMount
							class="range-value-input"
							type="number"
							min="12"
							max="24"
							step="0.5"
							style:font-size="{form.reading_font_size}px"
							bind:value={form.reading_font_size}
							onblur={commitReadingFont}
							onkeydown={(e) => {
								if (e.key === 'Enter') commitReadingFont();
								else if (e.key === 'Escape') cancelReadingFont();
							}}
						/>
					{:else}
						<button
							class="range-value"
							style:font-size="{form.reading_font_size}px"
							onclick={startEditReadingFont}
							use:tooltip={'Click to edit'}>{form.reading_font_size}px</button
						>
					{/if}
				</div>
			</div>
		</section>

		<section>
			<h2>Maintenance</h2>
			<div class="field toggle-field">
				<label
					for="rescan-vault"
					use:tooltip={'Re-derive tags, links, and search content for any file edited outside Analecta'}
				>
					Rescan vault
				</label>
				<button id="rescan-vault" class="action-btn" onclick={rescanVault} disabled={rescanning}>
					{rescanning ? 'Scanning…' : 'Rescan'}
				</button>
			</div>
			{#if rescanResult}<p class="rescan-result">{rescanResult}</p>{/if}
		</section>

		<section>
			<h2>Appearance</h2>
			<div class="field toggle-field">
				<label for="theme-toggle"
					>Theme {#if themeSaved}<span class="saved-tag">✓</span>{/if}</label
				>
				<button
					id="theme-toggle"
					role="switch"
					aria-checked={form.theme === 'light'}
					class="toggle"
					class:on={form.theme === 'light'}
					onclick={toggleTheme}
				>
					{form.theme === 'light' ? 'Light' : 'Dark'}
				</button>
			</div>
			<div class="field" role="group" aria-labelledby="accent-label">
				<span id="accent-label" class="field-caption">
					Accent color {#if accentSaved}<span class="saved-tag">✓</span>{/if}
				</span>
				<div class="accent-swatches">
					{#each ACCENT_OPTIONS as opt (opt.id)}
						<button
							class="swatch swatch-{opt.id}"
							class:active={form.accent_color === opt.id}
							use:tooltip={opt.label}
							aria-label={opt.label}
							onclick={() => selectAccent(opt.id)}
						></button>
					{/each}
				</div>
			</div>
		</section>

		<section>
			<h2>Window</h2>
			<div class="field toggle-field">
				<label for="close-to-tray-toggle">
					Close to tray {#if closeToTraySaved}<span class="saved-tag">✓</span>{/if}
				</label>
				<button
					id="close-to-tray-toggle"
					role="switch"
					aria-checked={form.close_to_tray}
					class="toggle"
					class:on={form.close_to_tray}
					onclick={toggleCloseToTray}
				>
					{form.close_to_tray ? 'On' : 'Off'}
				</button>
			</div>
		</section>
	</div>

	<aside class="shortcuts-panel">
		<h2>Keyboard shortcuts</h2>

		<div class="sp-group">
			<p class="sp-group-label">Navigation</p>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Ctrl+B</kbd></div>
				<div class="sp-desc">Toggle sidebar</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Alt+B</kbd></div>
				<div class="sp-desc">Toggle entry stack</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Ctrl+H</kbd></div>
				<div class="sp-desc">Collecta</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Ctrl+J</kbd></div>
				<div class="sp-desc">Keyboard shortcuts</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Ctrl+K</kbd></div>
				<div class="sp-desc">Search</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Ctrl+L</kbd></div>
				<div class="sp-desc">Add URL from clipboard</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Ctrl+R</kbd></div>
				<div class="sp-desc">Rescan vault</div>
			</div>
		</div>

		<div class="sp-group">
			<p class="sp-group-label">Tabs</p>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Ctrl+Tab</kbd></div>
				<div class="sp-desc">Next tab</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Ctrl+Shift+Tab</kbd></div>
				<div class="sp-desc">Previous tab</div>
			</div>
		</div>

		<div class="sp-group">
			<p class="sp-group-label">Reader</p>
			<div class="sp-row">
				<div class="sp-keys"><kbd>↑</kbd>&thinsp;/&thinsp;<kbd>↓</kbd></div>
				<div class="sp-desc">Scroll</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>PgUp</kbd>&thinsp;/&thinsp;<kbd>PgDn</kbd></div>
				<div class="sp-desc">Fast scroll</div>
			</div>
			<div class="sp-row">
				<div class="sp-keys"><kbd>Home</kbd>&thinsp;/&thinsp;<kbd>End</kbd></div>
				<div class="sp-desc">Top / bottom</div>
			</div>
		</div>
	</aside>
</div>

<style>
	.settings-page {
		container-type: inline-size;
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: 8em;
		padding: 2.33rem;
		max-width: 840px;
		color: var(--fg);
	}

	.settings-main {
		flex: 1;
		max-width: 420px;
		min-width: 320px;
	}

	/* ── Keyboard shortcuts panel ── */
	.shortcuts-panel {
		flex-shrink: 0;
		width: 220px;
		position: sticky;
		top: 2rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 1rem 1.1rem;
	}

	/* Narrow layout: shortcuts float to the top */
	@container (max-width: 670px) {
		.shortcuts-panel {
			order: -1;
			position: static;
			width: 100%;
		}
	}

	.sp-group {
		margin-bottom: 1.1rem;
		display: grid;
		grid-template-columns: auto 1fr;
		column-gap: 0.6rem;
		row-gap: 4px;
		align-items: center;
	}

	.sp-group:last-child {
		margin-bottom: 0;
	}

	.sp-group-label {
		grid-column: 1 / -1;
		font-size: 0.62rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: var(--fg-muted);
		margin: 0 0 0.3rem;
		padding-bottom: 3px;
		border-bottom: 1px solid var(--border);
	}

	.sp-row {
		display: contents;
	}

	.sp-keys {
		display: flex;
		align-items: center;
		gap: 2px;
	}

	.sp-keys:has(> kbd:only-child) kbd {
		flex: 1;
		text-align: center;
	}

	.sp-desc {
		font-size: 0.72rem;
		color: var(--fg-dark);
	}

	kbd {
		font-family: var(--font-ui-family);
		font-size: 0.62rem;
		letter-spacing: 0.05em;
		background: var(--bg-highlight);
		border: 1px solid var(--terminal);
		border-bottom-width: 2px;
		border-radius: 3px;
		padding: 1px 6px;
		color: var(--fg-dark);
		white-space: nowrap;
		display: inline-block;
		line-height: 1.4;
	}

	h1 {
		margin: 0 0 1.5rem;
		font-size: 1.2rem;
		font-weight: 700;
	}

	h2 {
		margin: 0 0 0.75rem;
		font-size: 0.85rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--fg-muted);
	}

	section {
		margin-bottom: 2rem;
		padding-bottom: 1.5rem;
		border-bottom: 1px solid var(--border);
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		margin-bottom: 1rem;
	}

	label {
		font-size: 12px;
		color: var(--fg-muted);
	}

	.saved-tag {
		color: var(--green);
		font-size: 11px;
		margin-left: 0.35rem;
	}

	input[type='text'],
	select {
		padding: 0.4rem 0.6rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg);
		font-family: inherit;
		font-size: 13px;
		outline: none;
	}

	select {
		appearance: none;
		-webkit-appearance: none;
		align-self: flex-start;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23565f89'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 0.6rem center;
		background-size: 8px 5px;
		padding-right: 2rem;
	}

	input[type='text']:focus,
	select:focus {
		border-color: var(--accent-dark);
	}

	.path-row {
		display: flex;
		gap: 0.5rem;
	}

	.path-row input {
		flex: 1;
	}

	.range-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.range-row input[type='range'] {
		flex: 1;
		padding: 0;
		border: none;
		background: none;
		accent-color: var(--accent);
	}

	.range-min,
	.range-max {
		font-size: 11px;
		color: var(--fg-muted);
		flex-shrink: 0;
	}

	.range-value {
		margin-left: 0.75rem;
		flex-shrink: 0;
		color: var(--fg);
		cursor: text;
		line-height: 1;
		padding: 0.1rem 0.25rem;
		border-radius: 3px;
		border: 1px solid transparent;
		background: none;
		font-family: inherit;
		transition: border-color 100ms;
	}

	.range-value:hover {
		border-color: var(--border);
	}

	.range-value-input {
		margin-left: 0.75rem;
		flex-shrink: 0;
		width: 4.5rem;
		padding: 0.1rem 0.25rem;
		background: var(--bg-alt);
		border: 1px solid var(--accent-dark);
		border-radius: 3px;
		color: var(--fg);
		font-family: inherit;
		outline: none;
		line-height: 1;
	}

	.path-row button {
		padding: 0.4rem 0.75rem;
		background: var(--bg-highlight);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg);
		font-family: inherit;
		font-size: 13px;
		cursor: pointer;
	}

	.path-row button:hover {
		border-color: var(--accent-dark);
		color: var(--accent);
	}

	.toggle-field {
		flex-direction: row;
		align-items: center;
		justify-content: space-between;
	}

	.toggle {
		padding: 0.25rem 0.75rem;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--bg-alt);
		color: var(--fg-muted);
		font-family: inherit;
		font-size: 12px;
		cursor: pointer;
	}

	.toggle.on {
		border-color: var(--accent-dark);
		background: var(--bg-highlight);
		color: var(--accent);
	}

	/* Accent colour swatches */
	.field-caption {
		font-size: 12px;
		color: var(--fg-muted);
	}

	.accent-swatches {
		display: flex;
		gap: 0.6rem;
	}

	.swatch {
		width: 22px;
		height: 22px;
		border-radius: 50%;
		border: 2px solid transparent;
		cursor: pointer;
		padding: 0;
		transition:
			border-color 0.12s,
			transform 0.1s;
	}

	.swatch-red {
		background: var(--red);
	}
	.swatch-yellow {
		background: var(--yellow);
	}
	.swatch-green {
		background: var(--green);
	}
	.swatch-cyan {
		background: var(--cyan);
	}

	.swatch.active {
		border-color: var(--fg);
		transform: scale(1.2);
	}

	.swatch:hover:not(.active) {
		transform: scale(1.1);
		border-color: var(--fg-muted);
	}

	.error {
		color: var(--red);
		font-size: 13px;
		margin-bottom: 1rem;
	}

	.rescan-result {
		margin: 0.5rem 0 0;
		font-size: 12px;
		color: var(--green);
	}

	.action-btn {
		padding: 0.4rem 0.75rem;
		background: var(--bg-highlight);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg);
		font-family: inherit;
		font-size: 13px;
		cursor: pointer;
	}

	.action-btn:hover:not(:disabled) {
		border-color: var(--accent-dark);
		color: var(--accent);
	}

	.action-btn:disabled {
		opacity: 0.6;
		cursor: default;
	}
</style>
