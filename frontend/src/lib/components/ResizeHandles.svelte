<script lang="ts">
	import { windowStartResize } from '$lib/platform';

	const { maximized = false }: { maximized?: boolean } = $props();

	type Edge =
		| 'top'
		| 'bottom'
		| 'left'
		| 'right'
		| 'top-left'
		| 'top-right'
		| 'bottom-left'
		| 'bottom-right';

	function startResize(edge: Edge) {
		return (e: MouseEvent) => {
			if (e.button !== 0) return;
			e.preventDefault();
			windowStartResize(edge).catch(() => {});
		};
	}
</script>

{#if !maximized}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="rh rh-n" onmousedown={startResize('top')}></div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="rh rh-s" onmousedown={startResize('bottom')}></div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="rh rh-w" onmousedown={startResize('left')}></div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="rh rh-e" onmousedown={startResize('right')}></div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="rh rh-nw" onmousedown={startResize('top-left')}></div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="rh rh-ne" onmousedown={startResize('top-right')}></div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="rh rh-sw" onmousedown={startResize('bottom-left')}></div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="rh rh-se" onmousedown={startResize('bottom-right')}></div>
{/if}

<style>
	.rh {
		position: fixed;
		z-index: 9999;
	}

	/* Edge handles */
	.rh-n {
		top: 0;
		left: 8px;
		right: 8px;
		height: 5px;
		cursor: n-resize;
	}
	.rh-s {
		bottom: 0;
		left: 8px;
		right: 8px;
		height: 5px;
		cursor: s-resize;
	}
	.rh-w {
		left: 0;
		top: 8px;
		bottom: 8px;
		width: 5px;
		cursor: w-resize;
	}
	.rh-e {
		right: 0;
		top: 8px;
		bottom: 8px;
		width: 5px;
		cursor: e-resize;
	}

	/* Corner handles (override edges, so corners come last) */
	.rh-nw {
		top: 0;
		left: 0;
		width: 12px;
		height: 12px;
		cursor: nw-resize;
	}
	.rh-ne {
		top: 0;
		right: 0;
		width: 12px;
		height: 12px;
		cursor: ne-resize;
	}
	.rh-sw {
		bottom: 0;
		left: 0;
		width: 12px;
		height: 12px;
		cursor: sw-resize;
	}
	.rh-se {
		bottom: 0;
		right: 0;
		width: 12px;
		height: 12px;
		cursor: se-resize;
	}
</style>
