<script lang="ts">
	import type { Update } from '@tauri-apps/plugin-updater';
	import { relaunch } from '@tauri-apps/plugin-process';

	let { update }: { update: Update } = $props();
	let installing = $state(false);

	async function installUpdate() {
		installing = true;
		await update.downloadAndInstall();
		await relaunch();
	}
</script>

<div class="update-banner">
	{#if installing}
		<span class="message">Installing update…</span>
	{:else}
		<span class="message">Update available: <strong>v{update.version}</strong></span>
		<button onclick={installUpdate}>Install &amp; restart</button>
	{/if}
</div>

<style>
	.update-banner {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.5rem 1rem;
		background: var(--bg-dark);
		border-bottom: 1px solid var(--accent);
		color: var(--fg);
		font-size: 0.875rem;
	}

	.message {
		flex: 1;
	}

	button {
		padding: 0.25rem 0.75rem;
		background: var(--accent);
		color: var(--bg);
		border: none;
		border-radius: 4px;
		font-size: 0.8125rem;
		cursor: pointer;
		font-family: inherit;
		transition: opacity 0.15s;
	}

	button:hover {
		opacity: 0.85;
	}
</style>
