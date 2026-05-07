<script lang="ts">
	let { timedOut = false }: { timedOut?: boolean } = $props();
</script>

<div class="loading-screen">
	{#if timedOut}
		<p class="error">Sidecar did not start within 10 seconds.</p>
		<p class="hint">Check the application logs for details.</p>
	{:else}
		<div class="spinner"></div>
		<p>Starting…</p>
	{/if}
</div>

<style>
	.loading-screen {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		height: 100vh;
		gap: 1rem;
		color: var(--fg-muted);
	}

	.spinner {
		width: 24px;
		height: 24px;
		border: 2px solid var(--border);
		border-top-color: var(--accent);
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	p {
		margin: 0;
		font-size: 13px;
	}

	.error {
		color: var(--red);
	}
</style>
