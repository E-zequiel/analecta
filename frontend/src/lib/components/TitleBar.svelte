<script lang="ts">
	import { onMount } from 'svelte';
	import { Minus, Square, X } from '@lucide/svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import {
		windowMinimize,
		windowMaximize,
		windowClose,
		windowStartMove,
		windowIsMaximized,
		onWindowMaximized
	} from '$lib/platform';

	let maximized = $state(false);

	function onTitlebarMouseDown(e: MouseEvent) {
		if (e.button !== 0) return;
		if ((e.target as HTMLElement).closest('button, [role="tab"]')) return;
		e.preventDefault();
		windowStartMove().catch(() => {});
	}

	onMount(() => {
		windowIsMaximized()
			.then((v) => {
				maximized = v;
			})
			.catch(() => {});
		return onWindowMaximized((v) => {
			maximized = v;
		});
	});
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="titlebar" onmousedown={onTitlebarMouseDown}>
	<div class="tabs-area">
		<TabBar />
	</div>
	<div class="window-controls">
		<button class="wc-btn" onclick={() => windowMinimize().catch(() => {})} title="Minimize">
			<Minus size={12} />
		</button>
		<button
			class="wc-btn"
			onclick={() => windowMaximize().catch(() => {})}
			title={maximized ? 'Restore' : 'Maximize'}
		>
			<Square size={12} />
		</button>
		<button class="wc-btn wc-close" onclick={() => windowClose().catch(() => {})} title="Close">
			<X size={12} />
		</button>
	</div>
</div>

<style>
	.titlebar {
		display: flex;
		align-items: stretch;
		background: var(--bg-dark);
		flex-shrink: 0;
		-webkit-app-region: no-drag;
	}

	.tabs-area {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: stretch;
	}

	.window-controls {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 6px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.wc-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 28px;
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--fg-muted);
		cursor: pointer;
		transition: background 0.12s, color 0.12s;
		flex-shrink: 0;
	}

	.wc-btn:hover {
		background: var(--bg-alt);
		color: var(--fg);
	}

	.wc-close:hover {
		background: rgba(255, 117, 127, 0.15);
		color: var(--accent);
	}
</style>
