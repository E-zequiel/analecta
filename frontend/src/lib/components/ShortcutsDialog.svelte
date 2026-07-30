<script lang="ts">
	import { shortcutsOpen } from '$lib/stores/ui';

	function close() {
		shortcutsOpen.set(false);
	}

	function handleBackdropKey(e: KeyboardEvent) {
		if (e.key === 'Enter' || e.key === ' ') close();
	}

	$effect(() => {
		if (!$shortcutsOpen) return;
		function onKey(e: KeyboardEvent) {
			if (e.key === 'Escape') {
				close();
				e.stopPropagation();
			}
		}
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	});
</script>

{#if $shortcutsOpen}
	<div class="backdrop" onclick={close} onkeydown={handleBackdropKey} role="button" tabindex="-1">
		<div
			class="dialog"
			role="dialog"
			aria-modal="true"
			aria-label="Keyboard shortcuts"
			tabindex="-1"
			onclick={(e) => e.stopPropagation()}
			onkeydown={(e) => e.stopPropagation()}
		>
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
		width: 280px;
		background: var(--bg-alt);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 1.1rem 1.2rem;
		box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
	}

	h2 {
		font-size: 0.85rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: var(--fg-muted);
		margin: 0 0 0.9rem;
	}

	.sp-group {
		margin-bottom: 1rem;
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
		font-size: var(--font-size-count);
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
		font-size: var(--font-size-count);
		color: var(--fg-dark);
	}

	kbd {
		font-family: var(--font-ui-family);
		font-size: var(--font-size-count);
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
</style>
