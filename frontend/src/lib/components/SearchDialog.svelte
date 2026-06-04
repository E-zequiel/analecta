<script lang="ts">
	import { entries as entriesApi, type Entry } from '$lib/api/client';
	import { searchOpen } from '$lib/stores/ui';
	import { navigateInTab } from '$lib/stores/tabs';

	let query = $state('');
	let results = $state<Entry[]>([]);
	let loading = $state(false);
	let inputEl = $state<HTMLInputElement | undefined>(undefined);
	let timer: ReturnType<typeof setTimeout>;

	$effect(() => {
		if ($searchOpen) {
			query = '';
			results = [];
			// Focus input on next tick
			setTimeout(() => inputEl?.focus(), 0);
		}
	});

	function handleInput(e: Event) {
		query = (e.target as HTMLInputElement).value;
		clearTimeout(timer);
		const q = query.trim();
		if (!q) {
			results = [];
			return;
		}
		loading = true;
		timer = setTimeout(async () => {
			try {
				results = await entriesApi.list({ q });
			} catch {
				results = [];
			} finally {
				loading = false;
			}
		}, 300);
	}

	function close() {
		searchOpen.set(false);
	}

	function open(entry: Entry) {
		close();
		navigateInTab(entry.id, entry.title, entry.source_type);
	}

	function handleKey(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
	}

	function handleBackdropKey(e: KeyboardEvent) {
		if (e.key === 'Enter' || e.key === ' ') close();
	}
</script>

{#if $searchOpen}
	<div class="backdrop" onclick={close} onkeydown={handleBackdropKey} role="button" tabindex="-1">
		<div
			class="dialog"
			role="dialog"
			aria-modal="true"
			tabindex="-1"
			onclick={(e) => e.stopPropagation()}
			onkeydown={handleKey}
		>
			<input
				bind:this={inputEl}
				class="search-input"
				type="text"
				placeholder="Search entries…"
				value={query}
				oninput={handleInput}
			/>
			<div class="results">
				{#if loading}
					<p class="hint">Searching…</p>
				{:else if query.trim() && results.length === 0}
					<p class="hint">No results.</p>
				{:else}
					{#each results as entry (entry.id)}
						<button class="result-item" onclick={() => open(entry)}>
							<span class="result-title">{entry.title}</span>
							<span class="result-meta">{entry.source_type}</span>
						</button>
					{/each}
				{/if}
			</div>
		</div>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.55);
		display: flex;
		align-items: flex-start;
		justify-content: center;
		padding-top: 28vh;
		z-index: 200;
	}

	.dialog {
		width: 540px;
		max-width: 90vw;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow: hidden;
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
	}

	.search-input {
		width: 100%;
		padding: 14px 16px;
		background: transparent;
		border: none;
		border-bottom: 1px solid var(--border);
		color: var(--fg);
		font-family: inherit;
		font-size: 1rem;
		outline: none;
	}

	.search-input::placeholder {
		color: var(--fg-muted);
	}

	.results {
		max-height: 360px;
		overflow-y: auto;
	}

	.hint {
		padding: 12px 16px;
		margin: 0;
		font-size: 0.85rem;
		color: var(--fg-muted);
	}

	.result-item {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 8px;
		width: 100%;
		padding: 10px 16px;
		background: none;
		border: none;
		border-radius: 0;
		color: var(--fg);
		font-family: inherit;
		font-size: 0.88rem;
		cursor: pointer;
		text-align: left;
		transition: background 0.1s;
	}

	.result-item:hover {
		background: var(--bg-highlight);
	}

	.result-title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex: 1;
	}

	.result-meta {
		font-size: 0.75rem;
		color: var(--fg-muted);
		flex-shrink: 0;
	}
</style>
