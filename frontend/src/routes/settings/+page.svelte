<script lang="ts">
	import { onMount } from 'svelte';
	import { openDialog, updateVaultScope } from '$lib/platform';
	import { config as configApi, security } from '$lib/api/client';
	import { applyFont } from '$lib/font';

	const ACCENT_OPTIONS = [
		{ id: 'red',    label: 'Red'    },
		{ id: 'yellow', label: 'Yellow' },
		{ id: 'green',  label: 'Green'  },
		{ id: 'cyan',   label: 'Cyan'   },
	] as const satisfies { id: 'red' | 'yellow' | 'green' | 'cyan'; label: string }[];

	let form = $state({
		vault_path: '',
		font_variant: 'regular' as 'regular' | 'nerd' | 'custom',
		ui_font_size: 16.0,
		reading_font_size: 17.0,
		update_channel: 'stable' as 'stable' | 'dev',
		virustotal_enabled: false,
		theme: 'dark' as 'dark' | 'light',
		accent_color: 'yellow' as 'red' | 'yellow' | 'green' | 'cyan',
	});
	let initialVaultPath = $state('');
	let customFontPath = $state('');
	let vtApiKey = $state('');
	let vtKeyExists = $state(false);
	let showDisclaimer = $state(false);
	let error = $state('');

	// Per-field saved indicators
	let vaultSaved = $state(false);
	let fontVariantSaved = $state(false);
	let uiFontSaved = $state(false);
	let readingFontSaved = $state(false);
	let channelSaved = $state(false);
	let themeSaved = $state(false);
	let accentSaved = $state(false);

	// VT section
	let vtSaving = $state(false);
	let vtSaved = $state(false);
	let vtError = $state('');

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

	function applyCurrentFont(): Promise<void> {
		return applyFont(form.font_variant, customFontPath || null, form.ui_font_size, form.reading_font_size, form.theme, form.accent_color);
	}

	function startEditUiFont() { origUiFont = form.ui_font_size; editingUiFont = true; }
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

	function startEditReadingFont() { origReadingFont = form.reading_font_size; editingReadingFont = true; }
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
			const [cfg, keyStatus] = await Promise.all([configApi.get(), security.keyExists()]);
			form = {
				vault_path: cfg.vault_path,
				font_variant: cfg.font_variant,
				ui_font_size: cfg.ui_font_size,
				reading_font_size: cfg.reading_font_size,
				update_channel: cfg.update_channel,
				virustotal_enabled: cfg.virustotal_enabled,
				theme: cfg.theme,
				accent_color: cfg.accent_color,
			};
			initialVaultPath = cfg.vault_path;
			customFontPath = cfg.custom_font_path ?? '';
			vtKeyExists = keyStatus.exists;
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
				initialVaultPath = form.vault_path;
			}
			flash((v) => (vaultSaved = v));
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	async function browseVault() {
		const selected = await openDialog({ properties: ['openDirectory'] });
		if (typeof selected === 'string') {
			form.vault_path = selected;
			await autoSaveVaultPath();
		}
	}

	async function autoSaveFontVariant() {
		try {
			await applyCurrentFont();
			await configApi.update({ font_variant: form.font_variant, custom_font_path: customFontPath || null });
			flash((v) => (fontVariantSaved = v));
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	}

	async function browseFont() {
		const selected = await openDialog({
			filters: [{ name: 'TrueType Font', extensions: ['ttf'] }]
		});
		if (typeof selected === 'string') {
			customFontPath = selected;
			await autoSaveFontVariant();
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

	async function autoSaveChannel() {
		try {
			await configApi.update({ update_channel: form.update_channel });
			flash((v) => (channelSaved = v));
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
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

	function handleVtToggle() {
		if (!form.virustotal_enabled) {
			showDisclaimer = true;
		} else {
			form.virustotal_enabled = false;
		}
	}

	function acceptDisclaimer() {
		form.virustotal_enabled = true;
		showDisclaimer = false;
	}

	async function saveVt() {
		if (vtSaving) return;
		vtSaving = true;
		vtError = '';
		try {
			await configApi.update({ virustotal_enabled: form.virustotal_enabled });
			if (vtApiKey) {
				await security.setKey(vtApiKey);
				vtApiKey = '';
				vtKeyExists = true;
			}
			flash((v) => (vtSaved = v));
		} catch (err) {
			vtError = err instanceof Error ? err.message : String(err);
		} finally {
			vtSaving = false;
		}
	}
</script>

<div class="settings-page">
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
				<button onclick={browseVault}>Browse…</button>
			</div>
		</div>
		<div class="field">
			<label for="font-variant">
				Font {#if fontVariantSaved}<span class="saved-tag">✓</span>{/if}
			</label>
			<select id="font-variant" bind:value={form.font_variant} onchange={autoSaveFontVariant}>
				<option value="regular">JetBrains Mono</option>
				<option value="nerd">JetBrains Mono Nerd Font</option>
				<option value="custom">Custom…</option>
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
						style="font-size: {form.ui_font_size}px"
						bind:value={form.ui_font_size}
						onblur={commitUiFont}
						onkeydown={(e) => { if (e.key === 'Enter') commitUiFont(); else if (e.key === 'Escape') cancelUiFont(); }}
					/>
				{:else}
					<button
						class="range-value"
						style="font-size: {form.ui_font_size}px"
						onclick={startEditUiFont}
						title="Click to edit"
					>{form.ui_font_size}px</button>
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
						style="font-size: {form.reading_font_size}px"
						bind:value={form.reading_font_size}
						onblur={commitReadingFont}
						onkeydown={(e) => { if (e.key === 'Enter') commitReadingFont(); else if (e.key === 'Escape') cancelReadingFont(); }}
					/>
				{:else}
					<button
						class="range-value"
						style="font-size: {form.reading_font_size}px"
						onclick={startEditReadingFont}
						title="Click to edit"
					>{form.reading_font_size}px</button>
				{/if}
			</div>
		</div>
		{#if form.font_variant === 'custom'}
			<div class="field">
				<label for="custom-font-path">Font file (.ttf)</label>
				<div class="path-row">
					<input
						id="custom-font-path"
						type="text"
						readonly
						placeholder="No font selected"
						value={customFontPath}
					/>
					<button onclick={browseFont}>Browse…</button>
				</div>
			</div>
		{/if}
	</section>

	<section>
		<h2>Appearance</h2>
		<div class="field toggle-field">
			<label for="theme-toggle">Theme {#if themeSaved}<span class="saved-tag">✓</span>{/if}</label>
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
				{#each ACCENT_OPTIONS as opt}
					<button
						class="swatch swatch-{opt.id}"
						class:active={form.accent_color === opt.id}
						title={opt.label}
						onclick={() => selectAccent(opt.id)}
					></button>
				{/each}
			</div>
		</div>
	</section>

	<section>
		<h2>Updates</h2>
		<div class="field">
			<label for="update-channel">
				Channel {#if channelSaved}<span class="saved-tag">✓</span>{/if}
			</label>
			<select id="update-channel" bind:value={form.update_channel} onchange={autoSaveChannel}>
				<option value="stable">Stable</option>
				<option value="dev">Dev</option>
			</select>
		</div>
	</section>

	<section class="section-vt">
		<h2>VirusTotal</h2>
		{#if vtError}
			<p class="error">{vtError}</p>
		{/if}
		<div class="field toggle-field">
			<label for="vt-toggle">Enable scanning</label>
			<button
				id="vt-toggle"
				role="switch"
				aria-checked={form.virustotal_enabled}
				class="toggle"
				class:on={form.virustotal_enabled}
				onclick={handleVtToggle}
			>
				{form.virustotal_enabled ? 'On' : 'Off'}
			</button>
		</div>
		{#if form.virustotal_enabled}
			<div class="field">
				<label for="vt-key">
					API Key {vtKeyExists ? '(stored — enter to replace)' : '(not set)'}
				</label>
				<input
					id="vt-key"
					type="password"
					placeholder={vtKeyExists ? '••••••••' : 'Paste API key…'}
					bind:value={vtApiKey}
					autocomplete="off"
				/>
			</div>
		{/if}
		<div class="vt-actions">
			<button class="btn-save" onclick={saveVt} disabled={vtSaving}>
				{vtSaving ? 'Saving…' : vtSaved ? 'Saved ✓' : 'Save'}
			</button>
		</div>
	</section>
</div>

<!-- VirusTotal disclaimer modal -->
{#if showDisclaimer}
	<div
		class="modal-backdrop"
		role="presentation"
		onclick={() => (showDisclaimer = false)}
		onkeydown={(e) => { if (e.key === 'Escape') showDisclaimer = false; }}
	>
		<div
			class="modal"
			role="dialog"
			aria-modal="true"
			aria-labelledby="vt-disclaimer-title"
			tabindex="-1"
			onclick={(e) => e.stopPropagation()}
			onkeydown={(e) => e.stopPropagation()}
		>
			<h3 id="vt-disclaimer-title">VirusTotal — Privacy Notice</h3>
			<ul>
				<li>Every URL you scan is <strong>submitted to VirusTotal and indexed publicly</strong> in their database.</li>
				<li>The Public API is <strong>non-commercial only</strong>. Analecta must remain free and open-source.</li>
				<li>Rate limits: <strong>4 requests/min · 500 requests/day</strong>. Exceeding them risks a permanent account ban.</li>
			</ul>
			<p class="modal-note">You will need a free VirusTotal account to obtain an API key.</p>
			<div class="modal-actions">
				<button onclick={() => (showDisclaimer = false)}>Cancel</button>
				<button class="btn-accept" onclick={acceptDisclaimer}>I understand — Enable</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.settings-page {
		padding: 2rem;
		max-width: 560px;
		color: var(--fg);
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

	.section-vt {
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 1.25rem;
		background: var(--bg-dark);
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
	input[type='password'],
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
	input[type='password']:focus,
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

	.path-row button,
	.vt-actions button,
	.modal-actions button {
		padding: 0.4rem 0.75rem;
		background: var(--bg-highlight);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg);
		font-family: inherit;
		font-size: 13px;
		cursor: pointer;
	}

	.path-row button:hover,
	.vt-actions button:hover:not(:disabled),
	.modal-actions button:hover {
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
		transition: border-color 0.12s, transform 0.1s;
	}

	.swatch-red    { background: var(--red);    }
	.swatch-yellow { background: var(--yellow); }
	.swatch-green  { background: var(--green);  }
	.swatch-cyan   { background: var(--cyan);   }

	.swatch.active {
		border-color: var(--fg);
		transform: scale(1.2);
	}

	.swatch:hover:not(.active) {
		transform: scale(1.1);
		border-color: var(--fg-muted);
	}

	.vt-actions {
		margin-top: 0.75rem;
		display: flex;
		justify-content: flex-end;
	}

	.btn-save {
		min-width: 80px;
	}

	.btn-save:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.error {
		color: var(--red);
		font-size: 13px;
		margin-bottom: 1rem;
	}

	/* Modal */
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.6);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 100;
	}

	.modal {
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 1.5rem;
		max-width: 420px;
		width: 90%;
	}

	.modal h3 {
		margin: 0 0 1rem;
		font-size: 1rem;
		color: var(--yellow);
	}

	.modal ul {
		padding-left: 1.25rem;
		margin: 0 0 1rem;
		font-size: 13px;
		line-height: 1.6;
		color: var(--fg-dark);
	}

	.modal li {
		margin-bottom: 0.5rem;
	}

	.modal-note {
		font-size: 12px;
		color: var(--fg-muted);
		margin: 0 0 1.25rem;
	}

	.modal-actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
	}

	.btn-accept {
		border-color: var(--accent-dark) !important;
		color: var(--accent) !important;
	}
</style>
