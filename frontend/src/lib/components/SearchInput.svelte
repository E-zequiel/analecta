<script lang="ts">
	const { onSearch }: { onSearch: (q: string) => void } = $props();

	let value = $state('');
	let timer: ReturnType<typeof setTimeout>;

	function handleInput(e: Event) {
		value = (e.target as HTMLInputElement).value;
		clearTimeout(timer);
		timer = setTimeout(() => onSearch(value.trim()), 300);
	}

	function handleClear() {
		value = '';
		clearTimeout(timer);
		onSearch('');
	}
</script>

<div class="search-wrap">
	<input type="search" placeholder="Search…" {value} oninput={handleInput} class="search-input" />
	{#if value}
		<button class="clear-btn" onclick={handleClear} aria-label="Clear search">✕</button>
	{/if}
</div>

<style>
	.search-wrap {
		position: relative;
		display: flex;
		align-items: center;
	}

	.search-input {
		width: 100%;
		padding: 0.4rem 2rem 0.4rem 0.75rem;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg);
		font-family: inherit;
		font-size: var(--font-size-sublabel);
		outline: none;
	}

	.search-input:focus {
		border-color: var(--accent-dark);
	}

	.search-input::-webkit-search-cancel-button {
		display: none;
	}

	.clear-btn {
		position: absolute;
		right: 0.5rem;
		background: none;
		border: none;
		color: var(--fg-muted);
		cursor: pointer;
		font-size: var(--font-size-sublabel);
		padding: 0;
		line-height: 1;
	}

	.clear-btn:hover {
		color: var(--fg);
	}
</style>
