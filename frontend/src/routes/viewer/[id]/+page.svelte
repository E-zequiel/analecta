<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
</script>

<div class="viewer-page">
	<button onclick={() => goto('/')}>← Back</button>
	<p class="hint">Viewer for entry {$page.params.id} — D6</p>
</div>

<style>
	.viewer-page {
		padding: 2rem;
		color: var(--fg);
	}

	button {
		background: none;
		border: none;
		color: var(--accent);
		font-family: inherit;
		font-size: 13px;
		cursor: pointer;
		padding: 0;
		margin-bottom: 1rem;
		display: block;
	}

	.hint {
		color: var(--fg-muted);
		font-size: 13px;
	}
</style>
