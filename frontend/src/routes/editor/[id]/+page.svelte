<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
</script>

<div class="editor-page">
	<button onclick={() => goto(`/viewer/${$page.params.id}`)}>← Back</button>
	<p class="hint">Editor for entry {$page.params.id} — D7</p>
</div>

<style>
	.editor-page {
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
