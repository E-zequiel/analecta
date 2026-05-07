<script lang="ts">
	import { onMount } from 'svelte';
	import { invoke } from '@tauri-apps/api/core';
	import { open as openDialog } from '@tauri-apps/plugin-dialog';
	import { config as configApi, security } from '$lib/api/client';

	let form = $state({
		vault_path: '',
		font_variant: 'regular' as 'regular' | 'nerd',
		update_channel: 'stable' as 'stable' | 'dev',
		virustotal_enabled: false
	});
	let initialVaultPath = $state('');
	let vtApiKey = $state('');
	let vtKeyExists = $state(false);
	let showDisclaimer = $state(false);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state('');

	onMount(async () => {
		try {
			const [cfg, keyStatus] = await Promise.all([configApi.get(), security.keyExists()]);
			form = {
				vault_path: cfg.vault_path,
				font_variant: cfg.font_variant,
				update_channel: cfg.update_channel,
				virustotal_enabled: cfg.virustotal_enabled
			};
			initialVaultPath = cfg.vault_path;
			vtKeyExists = keyStatus.exists;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		}
	});

	async function browseVault() {
		const selected = await openDialog({ directory: true, multiple: false });
		if (typeof selected === 'string') form.vault_path = selected;
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

	async function save() {
		if (saving) return;
		saving = true;
		error = '';
		try {
			await configApi.update({
				vault_path: form.vault_path,
				font_variant: form.font_variant,
				update_channel: form.update_channel,
				virustotal_enabled: form.virustotal_enabled
			});
			if (form.vault_path !== initialVaultPath) {
				await invoke('update_vault_scope', { vaultPath: form.vault_path });
				initialVaultPath = form.vault_path;
			}
			if (vtApiKey) {
				await security.setKey(vtApiKey);
				vtApiKey = '';
				vtKeyExists = true;
			}
			saved = true;
			setTimeout(() => {
				saved = false;
			}, 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			saving = false;
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
			<label for="vault-path">Path</label>
			<div class="path-row">
				<input id="vault-path" type="text" bind:value={form.vault_path} />
				<button onclick={browseVault}>Browse…</button>
			</div>
		</div>
		<div class="field">
			<label for="font-variant">Font</label>
			<select id="font-variant" bind:value={form.font_variant}>
				<option value="regular">JetBrains Mono</option>
				<option value="nerd">JetBrains Mono Nerd Font</option>
			</select>
		</div>
	</section>

	<section>
		<h2>Updates</h2>
		<div class="field">
			<label for="update-channel">Channel</label>
			<select id="update-channel" bind:value={form.update_channel}>
				<option value="stable">Stable</option>
				<option value="dev">Dev</option>
			</select>
		</div>
	</section>

	<section>
		<h2>VirusTotal</h2>
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
	</section>

	<div class="actions">
		<button class="btn-save" onclick={save} disabled={saving}>
			{saving ? 'Saving…' : saved ? 'Saved ✓' : 'Save'}
		</button>
	</div>
</div>

<!-- VirusTotal disclaimer modal -->
{#if showDisclaimer}
	<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
	<div class="modal-backdrop" onclick={() => (showDisclaimer = false)}>
		<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
		<div class="modal" onclick={(e) => e.stopPropagation()}>
			<h3>VirusTotal — Privacy Notice</h3>
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

	.path-row button,
	.actions button,
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
	.actions button:hover:not(:disabled),
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

	.actions {
		margin-top: 0.5rem;
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
