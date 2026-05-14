<script lang="ts">
	import { goto } from '$app/navigation';
	import { invoke } from '@tauri-apps/api/core';
	import { open as openDialog } from '@tauri-apps/plugin-dialog';
	import { config as configApi } from '$lib/api/client';

	let vaultPath = $state('');
	let submitting = $state(false);
	let error = $state('');

	async function browseVault() {
		const selected = await openDialog({ directory: true, multiple: false });
		if (typeof selected === 'string') vaultPath = selected;
	}

	async function submit() {
		if (!vaultPath || submitting) return;
		submitting = true;
		error = '';
		try {
			await configApi.update({ vault_path: vaultPath });
			await invoke('update_vault_scope', { vaultPath });
			goto('/');
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
			submitting = false;
		}
	}
</script>

<div class="first-run">
	<div class="card">
		<h1>Welcome to Analecta</h1>
		<p class="subtitle">Choose where your vault will be stored.</p>

		<div class="field">
			<label for="vault-path">Vault location</label>
			<div class="path-row">
				<input
					id="vault-path"
					type="text"
					placeholder="/home/user/vault"
					bind:value={vaultPath}
				/>
				<button onclick={browseVault}>Browse…</button>
			</div>
		</div>

		{#if error}
			<p class="error">{error}</p>
		{/if}

		<button class="btn-start" onclick={submit} disabled={!vaultPath || submitting}>
			{submitting ? 'Setting up…' : 'Get started'}
		</button>
	</div>
</div>

<style>
	.first-run {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 100%;
		background: var(--bg);
	}

	.card {
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 10px;
		padding: 2.5rem;
		width: 420px;
	}

	h1 {
		margin: 0 0 0.5rem;
		font-size: 1.3rem;
		font-weight: 600;
		color: var(--fg);
	}

	.subtitle {
		margin: 0 0 1.75rem;
		font-size: 13px;
		color: var(--fg-muted);
	}

	.field {
		margin-bottom: 1.25rem;
	}

	label {
		display: block;
		font-size: 12px;
		color: var(--fg-muted);
		margin-bottom: 0.35rem;
	}

	.path-row {
		display: flex;
		gap: 0.5rem;
	}

	.path-row input {
		flex: 1;
		padding: 0.4rem 0.6rem;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg);
		font-family: inherit;
		font-size: 13px;
		outline: none;
	}

	.path-row input:focus {
		border-color: var(--accent-dark);
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
		white-space: nowrap;
	}

	.path-row button:hover {
		border-color: var(--accent-dark);
		color: var(--accent);
	}

	.error {
		font-size: 12px;
		color: var(--red);
		margin-bottom: 1rem;
	}

	.btn-start {
		width: 100%;
		padding: 0.6rem;
		background: var(--accent-dark);
		border: none;
		border-radius: 6px;
		color: var(--fg);
		font-family: inherit;
		font-size: 14px;
		font-weight: 600;
		cursor: pointer;
		transition: background 0.15s;
	}

	.btn-start:hover:not(:disabled) {
		background: var(--accent);
		color: var(--bg);
	}

	.btn-start:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
</style>
