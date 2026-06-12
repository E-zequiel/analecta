<script lang="ts">
	import { onMount } from 'svelte';
	import { untrack } from 'svelte';
	import Sigma from 'sigma';
	import { UndirectedGraph } from 'graphology';
	import forceAtlas2 from 'graphology-layout-forceatlas2';
	import { Focus, Maximize2, Waypoints, X } from '@lucide/svelte';
	import { entries as entriesApi, type GraphResult } from '$lib/api/client';
	import { showContextMenu } from '$lib/stores/contextMenu';
	import { openEntryTab } from '$lib/stores/tabs';

	type VaultNodeAttrs = {
		label: string;
		fullLabel: string;
		color: string;
		size: number;
		x: number;
		y: number;
		kind: 'entry' | 'tag';
		source_type: string | null;
	};

	const {
		onopen,
		ontagclick,
	}: {
		onopen?: (id: number, title: string, sourceType?: string) => void;
		ontagclick?: (tagName: string) => void;
	} = $props();

	let sigmaEl = $state<HTMLElement | undefined>(undefined);
	let expanded = $state(false);
	let graphData = $state<GraphResult | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Non-reactive handles — updated by $effect, never tracked.
	let sigmaInstance: InstanceType<typeof Sigma<VaultNodeAttrs>> | null = null;
	let sigmaGraph: UndirectedGraph<VaultNodeAttrs> | null = null;

	const nodeCount = $derived(graphData?.nodes.length ?? 0);
	const edgeCount = $derived(graphData?.edges.length ?? 0);

	onMount(() => {
		entriesApi
			.getGraph()
			.then((data) => {
				graphData = data;
				loading = false;
			})
			.catch((e: unknown) => {
				error = String(e);
				loading = false;
			});
	});

	function resolveColors(): {
		article: string;
		youtube: string;
		substack: string;
		tag: string;
		fallback: string;
		edge: string;
		label: string;
	} {
		const s = getComputedStyle(document.documentElement);
		const get = (v: string, fb: string) => s.getPropertyValue(v).trim() || fb;
		return {
			article: get('--cyan', '#7dcfff'),
			youtube: get('--red', '#f7768e'),
			substack: get('--accent-warm', '#ff9e64'),
			tag: '#9ece6a',
			fallback: get('--fg-muted', '#565f89'),
			edge: get('--border', '#292e42'),
			label: get('--fg-muted', '#565f89'),
		};
	}

	function nodeColor(
		kind: string,
		sourceType: string | null,
		colors: ReturnType<typeof resolveColors>
	): string {
		if (kind === 'tag') return colors.tag;
		if (sourceType === 'youtube') return colors.youtube;
		if (sourceType === 'substack') return colors.substack;
		return colors.article;
	}

	function truncate(s: string, max = 24): string {
		return s.length > max ? s.slice(0, max - 1) + '…' : s;
	}

	// Adaptive FA2 layout. Base settings inferred from graph topology, then overridden
	// to increase repulsion and prevent overlap (adjustSizes = FA2's forceCollide equiv).
	function runLayout(graph: UndirectedGraph<VaultNodeAttrs>, iterations?: number) {
		const n = graph.order;
		if (n === 0) return;
		graph.forEachNode((node) => {
			graph.setNodeAttribute(node, 'x', Math.random() * 2 - 1);
			graph.setNodeAttribute(node, 'y', Math.random() * 2 - 1);
		});
		forceAtlas2.assign(graph, {
			iterations: iterations ?? Math.min(600, Math.max(300, 200 + n * 2)),
			settings: {
				...forceAtlas2.inferSettings(graph),
				barnesHutOptimize: n > 150,
				scalingRatio: 25,
				gravity: 0.5,
				adjustSizes: true,
			},
		});
	}

	// Re-run layout from random positions, then fit camera.
	function resetLayout() {
		const sigma = sigmaInstance;
		const graph = sigmaGraph;
		if (!sigma || !graph) return;
		runLayout(graph);
		sigma.refresh();
		requestAnimationFrame(() => {
			sigma.getCamera().setState({ x: 0.5, y: 0.5, angle: 0, ratio: 1 });
		});
	}

	async function handleNodeContextMenu(id: number, e: MouseEvent) {
		try {
			const entry = await entriesApi.get(id);
			showContextMenu(e, {
				id: entry.id,
				title: entry.title,
				url: entry.url,
				file_path: entry.file_path,
				status: entry.status,
				flags: entry.flags,
			});
		} catch {
			// entry deleted or sidecar not ready
		}
	}

	// Build the Sigma instance whenever the container element and data are both ready.
	// Does NOT track `expanded` — fullscreen toggle repositions via CSS without rebuilding.
	$effect(() => {
		const el = sigmaEl;
		const data = graphData;
		if (!el || !data) return;

		const colors = untrack(() => resolveColors());

		const graph = new UndirectedGraph<VaultNodeAttrs>();

		for (const node of data.nodes) {
			graph.addNode(node.node_id, {
				label: truncate(node.label),
				fullLabel: node.label,
				color: nodeColor(node.kind, node.source_type, colors),
				size: node.kind === 'tag' ? 7 : 10,
				x: Math.random(),
				y: Math.random(),
				kind: node.kind,
				source_type: node.source_type,
			});
		}

		for (const edge of data.edges) {
			try {
				graph.addEdge(edge.source, edge.target, {
					color: colors.edge,
					size: 1,
				});
			} catch {
				// skip edges referencing nodes not in the graph
			}
		}

		// Partial layout before sigma creation — spreads nodes from the initial random
		// cluster but stays under-converged so heat() animates the remaining settling.
		runLayout(graph, Math.min(60, 20 + data.nodes.length));

		const sigma = new Sigma<VaultNodeAttrs>(graph, el, {
			allowInvalidContainer: true,
			defaultNodeColor: colors.fallback,
			defaultEdgeColor: colors.edge,
			renderLabels: true,
			labelColor: { color: colors.label },
			labelSize: 12,
			labelWeight: 'normal',
			minCameraRatio: 0.02,
			maxCameraRatio: 10,
		});

		// Live simulation — gentle FA2 ticks on the main thread, triggered by interaction.
		// Runs without a Web Worker to stay within CSP constraints (no blob: URLs).
		const liveSettings = {
			...forceAtlas2.inferSettings(graph),
			barnesHutOptimize: data.nodes.length > 150,
			scalingRatio: 15,
			gravity: 0.5,
			adjustSizes: true,
		};
		let liveTicksLeft = 0;
		let liveRafId = 0;

		function tick() {
			liveRafId = 0;
			if (liveTicksLeft <= 0) return;
			forceAtlas2.assign(graph, { iterations: 1, settings: liveSettings });
			sigma.refresh();
			liveTicksLeft--;
			liveRafId = requestAnimationFrame(tick);
		}

		function heat(ticks = 150) {
			liveTicksLeft = ticks;
			if (liveRafId === 0) liveRafId = requestAnimationFrame(tick);
		}

		// Hover tooltip — show full label when truncated.
		sigma.on('enterNode', ({ node, event }) => {
			const attrs = graph.getNodeAttributes(node);
			const tooltipEl = document.getElementById('analecta-tooltip');
			if (!tooltipEl) return;
			tooltipEl.textContent = attrs.fullLabel;
			const orig = event.original as MouseEvent;
			tooltipEl.style.left = `${orig.clientX + 14}px`;
			tooltipEl.style.top = `${orig.clientY - 6}px`;
			tooltipEl.classList.add('visible');
		});

		sigma.on('leaveNode', () => {
			document.getElementById('analecta-tooltip')?.classList.remove('visible');
		});

		// Node click — preview entry connections or filter by tag.
		sigma.on('clickNode', ({ node }) => {
			const attrs = graph.getNodeAttributes(node);
			if (attrs.kind === 'tag') {
				const tagName = node.startsWith('tag:') ? node.slice(4) : attrs.fullLabel;
				ontagclick?.(tagName);
			} else if (attrs.kind === 'entry') {
				const rawId = node.startsWith('entry:') ? node.slice(6) : node;
				const id = parseInt(rawId, 10);
				if (!isNaN(id)) {
					onopen?.(id, attrs.fullLabel, attrs.source_type ?? undefined);
				}
			}
		});

		// Cursor management — mirrors LocalGraph behaviour.
		sigma.on('enterNode', () => {
			el.style.cursor = 'pointer';
		});
		sigma.on('leaveNode', () => {
			if (!isDragging) el.style.cursor = '';
		});

		// Drag — move nodes by mouse.
		let isDragging = false;
		let draggedNode: string | null = null;

		sigma.on('downNode', ({ node, event }) => {
			const origEvent = event.original as MouseEvent;
			if (origEvent.button === 1) {
				origEvent.preventDefault();
				const attrs = graph.getNodeAttributes(node);
				if (attrs.kind === 'entry') {
					const rawId = node.startsWith('entry:') ? node.slice(6) : node;
					const id = parseInt(rawId, 10);
					if (!isNaN(id)) openEntryTab(id, attrs.fullLabel, true, attrs.source_type ?? undefined);
				}
				return;
			}
			if (origEvent.button !== 0) return;
			isDragging = true;
			draggedNode = node;
			el.style.cursor = 'grabbing';
		});

		sigma.on('rightClickNode', ({ node, event }) => {
			const origEvent = event.original as MouseEvent;
			const attrs = graph.getNodeAttributes(node);
			if (attrs.kind !== 'entry') return;
			const rawId = node.startsWith('entry:') ? node.slice(6) : node;
			const id = parseInt(rawId, 10);
			if (!isNaN(id)) void handleNodeContextMenu(id, origEvent);
		});

		sigma.on('moveBody', ({ preventSigmaDefault, event }) => {
			if (isDragging && draggedNode) {
				preventSigmaDefault();
				const pos = sigma.viewportToGraph(event);
				graph.setNodeAttribute(draggedNode, 'x', pos.x);
				graph.setNodeAttribute(draggedNode, 'y', pos.y);
				heat(80);
			} else {
				// Canvas pan — heat up so nodes responsively settle around the new viewport.
				heat(150);
			}
		});

		const endDrag = () => {
			isDragging = false;
			draggedNode = null;
			el.style.cursor = '';
		};
		window.addEventListener('mouseup', endDrag);

		sigmaInstance = sigma;
		sigmaGraph = graph;

		// $effect may run before the browser computes layout dimensions.
		// A single rAF ensures offsetWidth/Height are non-zero before Sigma renders.
		// heat() continues the under-converged layout with 1 FA2 iter/frame (~5s settle).
		const rafId = requestAnimationFrame(() => {
			sigma.refresh();
			sigma.getCamera().setState({ x: 0.5, y: 0.5, angle: 0, ratio: 1 });
			heat(300);
		});

		return () => {
			cancelAnimationFrame(rafId);
			cancelAnimationFrame(liveRafId);
			window.removeEventListener('mouseup', endDrag);
			document.getElementById('analecta-tooltip')?.classList.remove('visible');
			sigma.kill();
			sigmaInstance = null;
			sigmaGraph = null;
		};
	});

	// Refresh Sigma dimensions after fullscreen toggle, then re-fit camera to the
	// new container size. The double-rAF ensures normalization is recomputed first.
	$effect(() => {
		// eslint-disable-next-line @typescript-eslint/no-unused-expressions
		expanded;
		untrack(() => {
			const sigma = sigmaInstance;
			if (!sigma) return;
			sigma.refresh({ schedule: true });
			requestAnimationFrame(() => {
				requestAnimationFrame(() => {
					sigma.getCamera().setState({ x: 0.5, y: 0.5, angle: 0, ratio: 1 });
				});
			});
		});
	});

	// Escape closes fullscreen.
	$effect(() => {
		if (!expanded) return;
		function onKey(e: KeyboardEvent) {
			if (e.key === 'Escape') expanded = false;
		}
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	});
</script>

<div class="vault-graph-wrap" class:fullscreen={expanded}>
	<div class="vault-graph-header">
		<span class="graph-title">Vault Graph</span>
		{#if !loading && !error}
			<span class="graph-stats">{nodeCount} nodes · {edgeCount} edges</span>
		{/if}
		{#if !loading && !error && nodeCount > 0}
			<button class="graph-toggle" onclick={resetLayout} title="Reset layout">
				<Waypoints size={14} />
			</button>
			<button
				class="graph-toggle"
				onclick={() => sigmaInstance?.getCamera().animatedReset()}
				title="Fit to viewport"
			>
				<Focus size={14} />
			</button>
		{/if}
		<button
			class="graph-toggle"
			onclick={() => (expanded = !expanded)}
			title={expanded ? 'Collapse' : 'Expand graph'}
		>
			{#if expanded}
				<X size={14} />
			{:else}
				<Maximize2 size={14} />
			{/if}
		</button>
	</div>

	<div class="graph-canvas-root">
		{#if loading}
			<p class="graph-status">Loading…</p>
		{:else if error}
			<p class="graph-status graph-error">Failed to load graph.</p>
		{:else if nodeCount === 0}
			<p class="graph-status">No connections yet.</p>
		{:else}
			<div bind:this={sigmaEl} class="graph-canvas"></div>
		{/if}
	</div>
</div>

<style>
	.vault-graph-wrap {
		border-top: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		flex: 1;
		min-height: 420px;
	}

	.vault-graph-wrap.fullscreen {
		position: fixed;
		inset: 0;
		z-index: 200;
		background: var(--bg);
		flex: unset;
		min-height: unset;
	}

	.vault-graph-header {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 6px 10px;
		flex-shrink: 0;
	}

	.graph-title {
		font-size: 11px;
		font-weight: 600;
		color: var(--fg-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.graph-stats {
		font-size: 11px;
		color: var(--fg-muted);
		opacity: 0.7;
		flex: 1;
	}

	.graph-toggle {
		background: none;
		border: none;
		cursor: pointer;
		color: var(--fg-muted);
		padding: 2px;
		display: flex;
		align-items: center;
		border-radius: 3px;
	}

	.graph-toggle:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.graph-canvas-root {
		position: relative;
		flex: 1;
		min-height: 0;
	}

	.graph-canvas {
		position: absolute;
		inset: 0;
	}

	.graph-status {
		padding: 10px;
		font-size: 12px;
		color: var(--fg-muted);
		font-style: italic;
		margin: 0;
	}

	.graph-error {
		color: var(--red);
	}
</style>
