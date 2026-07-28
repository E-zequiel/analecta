<script lang="ts">
	import { onMount } from 'svelte';
	import { untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import Sigma from 'sigma';
	import { UndirectedGraph } from 'graphology';
	import forceAtlas2 from 'graphology-layout-forceatlas2';
	import { BowArrow, Focus, Maximize2, Pause, Play, Waypoints, X } from '@lucide/svelte';
	import { entries as entriesApi, type GraphResult } from '$lib/api/client';
	import { showContextMenu } from '$lib/stores/contextMenu';
	import { openEntryTab } from '$lib/stores/tabs';
	import { tooltip } from '$lib/actions/tooltip';
	import { graphAnimationEnabled } from '$lib/stores/ui';

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

	// Node radius grows with degree (fixed curve, not normalized against the graph's max
	// degree — otherwise a leaf node's size would shift depending on whether some unrelated
	// hub exists elsewhere in the vault). Tags keep a lower ceiling than entries, mirroring
	// the old uniform 7 vs 10 split.
	const ENTRY_SIZE_RANGE = { min: 6, max: 20 } as const;
	const TAG_SIZE_RANGE = { min: 4, max: 14 } as const;
	const SIZE_GROWTH_PER_SQRT_DEGREE = 3;

	function sizeForDegree(kind: 'entry' | 'tag', degree: number): number {
		const range = kind === 'tag' ? TAG_SIZE_RANGE : ENTRY_SIZE_RANGE;
		return Math.min(range.max, range.min + SIZE_GROWTH_PER_SQRT_DEGREE * Math.sqrt(degree));
	}

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
	let searchOpen = $state(false);
	let searchQuery = $state('');
	let searchInputEl = $state<HTMLInputElement | undefined>(undefined);
	let matchedNodeKeys = $state<SvelteSet<string> | null>(null);
	let selectedNodeKey = $state<string | null>(null);

	// Non-reactive handles — updated by $effect, never tracked.
	let sigmaInstance: InstanceType<typeof Sigma<VaultNodeAttrs>> | null = null;
	let sigmaGraph: UndirectedGraph<VaultNodeAttrs> | null = null;
	// Start/stop the live forceAtlas2 simulation — set by the Sigma-build $effect, called by
	// the pause/resume button so the toggle has an immediate visible effect rather than
	// waiting on the next drag/reset to notice the preference changed.
	let startLiveSimulation: (() => void) | null = null;
	let stopLiveSimulation: (() => void) | null = null;

	// Incremented by MutationObserver when .theme-light toggles, forcing a Sigma rebuild
	// with colors re-read from the new computed styles.
	let themeVersion = $state(0);

	const nodeCount = $derived(graphData?.nodes.length ?? 0);
	const edgeCount = $derived(graphData?.edges.length ?? 0);
	const matchedCount = $derived(matchedNodeKeys?.size ?? 0);

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

		const observer = new MutationObserver(() => {
			themeVersion++;
		});
		observer.observe(document.documentElement, { attributeFilter: ['class'] });
		return () => observer.disconnect();
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
			tag: get('--green', '#9ece6a'),
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

	// Run FA2 in isolation per connected component, then place components on a circle.
	// Isolation prevents gravity from pulling disconnected clusters into the same center.
	function positionByComponent(graph: UndirectedGraph<VaultNodeAttrs>, iterations: number) {
		const visited = new SvelteSet<string>();
		const components: string[][] = [];
		for (const node of graph.nodes()) {
			if (visited.has(node)) continue;
			const component: string[] = [];
			const queue = [node];
			visited.add(node);
			while (queue.length > 0) {
				const current = queue.shift()!;
				component.push(current);
				for (const neighbor of graph.neighbors(current)) {
					if (!visited.has(neighbor)) {
						visited.add(neighbor);
						queue.push(neighbor);
					}
				}
			}
			components.push(component);
		}

		components.sort((a, b) => b.length - a.length);

		const positions = new SvelteMap<string, { x: number; y: number }>();

		for (const component of components) {
			const nodeSet = new Set(component);
			const subGraph = new UndirectedGraph<VaultNodeAttrs>();
			for (const node of component) {
				subGraph.addNode(node, {
					...graph.getNodeAttributes(node),
					x: Math.random() * 2 - 1,
					y: Math.random() * 2 - 1,
				});
			}
			for (const node of component) {
				graph.forEachEdge(node, (key, attrs, source, target) => {
					if (nodeSet.has(source) && nodeSet.has(target) && !subGraph.hasEdge(key)) {
						subGraph.addEdgeWithKey(key, source, target, attrs);
					}
				});
			}
			if (subGraph.order > 1) {
				forceAtlas2.assign(subGraph, {
					iterations,
					settings: {
						...forceAtlas2.inferSettings(subGraph),
						barnesHutOptimize: subGraph.order > 150,
						scalingRatio: 25,
						gravity: 0.0575,
						adjustSizes: true,
					},
				});
			}
			for (const node of component) {
				const { x, y } = subGraph.getNodeAttributes(node);
				positions.set(node, { x, y });
			}
		}

		if (components.length === 1) {
			for (const [node, pos] of positions) {
				graph.setNodeAttribute(node, 'x', pos.x);
				graph.setNodeAttribute(node, 'y', pos.y);
			}
			return;
		}

		const k = components.length;
		const infos = components.map((component) => {
			let sumX = 0,
				sumY = 0;
			for (const node of component) {
				const { x, y } = positions.get(node)!;
				sumX += x;
				sumY += y;
			}
			const cx = sumX / component.length;
			const cy = sumY / component.length;
			let maxR = 0;
			for (const node of component) {
				const { x, y } = positions.get(node)!;
				maxR = Math.max(maxR, Math.hypot(x - cx, y - cy));
			}
			return { cx, cy, r: Math.max(maxR, 2) };
		});

		const circleR = (infos[0].r + infos[1].r + 4) * Math.max(2, Math.sqrt(k));

		components.forEach((component, i) => {
			const angle = (2 * Math.PI * i) / k;
			const targetX = circleR * Math.cos(angle);
			const targetY = circleR * Math.sin(angle);
			const { cx, cy } = infos[i];
			for (const node of component) {
				const { x, y } = positions.get(node)!;
				graph.setNodeAttribute(node, 'x', x - cx + targetX);
				graph.setNodeAttribute(node, 'y', y - cy + targetY);
			}
		});
	}

	function runLayout(graph: UndirectedGraph<VaultNodeAttrs>, iterations?: number) {
		const n = graph.order;
		if (n === 0) return;
		const iters = iterations ?? Math.min(600, Math.max(300, 200 + n * 2));
		positionByComponent(graph, iters);
		if (n < 2) return;
		// positionByComponent only prevents disconnected clusters from overlapping at
		// start. Filling the available space is a separate step: run FA2 jointly across
		// every node so universal repulsion (it acts between every pair, not just linked
		// nodes) pushes separate clusters apart. This used to happen live, spread over
		// the 900-frame heat() after render — now it runs synchronously before first paint.
		forceAtlas2.assign(graph, {
			iterations: iters,
			settings: {
				...forceAtlas2.inferSettings(graph),
				barnesHutOptimize: n > 150,
				scalingRatio: 15,
				gravity: 0.0575,
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

	// Both directions take effect immediately rather than waiting on the next drag/reset:
	// pausing cancels mid-flight ticks, resuming re-heats so the toggle has a visible effect
	// even when the layout is already settled.
	function toggleAnimation() {
		const next = !get(graphAnimationEnabled);
		graphAnimationEnabled.set(next);
		if (next) startLiveSimulation?.();
		else stopLiveSimulation?.();
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
	// Tracks `themeVersion` so a MutationObserver can force a rebuild on theme switch.
	$effect(() => {
		const el = sigmaEl;
		const data = graphData;
		void themeVersion;
		if (!el || !data) return;

		const colors = resolveColors();

		const graph = new UndirectedGraph<VaultNodeAttrs>();

		for (const node of data.nodes) {
			graph.addNode(node.node_id, {
				label: truncate(node.label),
				fullLabel: node.label,
				color: nodeColor(node.kind, node.source_type, colors),
				size: 0, // placeholder — replaced below once degree is known
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
				// UndirectedGraph rejects a second addEdge() between the same
				// pair — hit whenever a bidirectional connection (mutual
				// wikilinks, or an explicit `linked:` connection, which is
				// always written to both entries) produces both directed
				// refs. The first call already created the (correct, single)
				// undirected edge, so the reciprocal is a harmless no-op, not
				// a dropped connection.
			}
		}

		// Size by degree now that every edge is in — must run before layout since
		// forceAtlas2's adjustSizes reads node size to space hubs apart.
		graph.forEachNode((node, attrs) => {
			graph.setNodeAttribute(node, 'size', sizeForDegree(attrs.kind, graph.degree(node)));
		});

		// Full layout before sigma creation — nodes render already settled, no visible
		// assemble animation on load (matches resetLayout's convergence).
		runLayout(graph);

		const sigma = new Sigma<VaultNodeAttrs>(graph, el, {
			allowInvalidContainer: true,
			defaultNodeColor: colors.fallback,
			defaultEdgeColor: colors.edge,
			renderLabels: true,
			defaultDrawNodeHover: () => {},
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
			gravity: 0.0575,
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

		// Gated on the persisted preference — read via get(), not $graphAnimationEnabled,
		// so toggling it doesn't retrigger this outer $effect and rebuild the whole graph.
		function heat(ticks = 2000) {
			if (!get(graphAnimationEnabled)) return;
			liveTicksLeft = ticks;
			if (liveRafId === 0) liveRafId = requestAnimationFrame(tick);
		}

		function stopHeat() {
			liveTicksLeft = 0;
			cancelAnimationFrame(liveRafId);
			liveRafId = 0;
		}
		startLiveSimulation = () => heat(2000);
		stopLiveSimulation = stopHeat;

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
		sigma.on('clickStage', () => {
			selectedNodeKey = null;
		});

		sigma.on('clickNode', ({ node }) => {
			selectedNodeKey = node;
			const attrs = graph.getNodeAttributes(node);
			if (attrs.kind === 'tag') {
				const tagName = attrs.fullLabel.startsWith('#')
					? attrs.fullLabel.slice(1)
					: attrs.fullLabel;
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

		// Only an actual node drag re-heats the simulation — panning the camera is a
		// viewport operation and must not visibly re-agitate the settled layout.
		sigma.on('moveBody', ({ preventSigmaDefault, event }) => {
			if (isDragging && draggedNode) {
				preventSigmaDefault();
				const pos = sigma.viewportToGraph(event);
				graph.setNodeAttribute(draggedNode, 'x', pos.x);
				graph.setNodeAttribute(draggedNode, 'y', pos.y);
				heat(2000);
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
		// heat() continues the live simulation from the already-settled layout above —
		// a gentle ongoing motion, not the "assemble from clumped" animation this once was.
		const rafId = requestAnimationFrame(() => {
			sigma.refresh();
			sigma.getCamera().setState({ x: 0.5, y: 0.5, angle: 0, ratio: 1 });
			heat(2000);
		});

		return () => {
			cancelAnimationFrame(rafId);
			cancelAnimationFrame(liveRafId);
			window.removeEventListener('mouseup', endDrag);
			document.getElementById('analecta-tooltip')?.classList.remove('visible');
			sigma.kill();
			sigmaInstance = null;
			sigmaGraph = null;
			startLiveSimulation = null;
			stopLiveSimulation = null;
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

	// Node search — filters graphData client-side, drives Sigma nodeReducer + camera.
	// When search is closed but a node was clicked, keeps that node highlighted until
	// the user clicks the canvas background (clickStage → selectedNodeKey = null).
	$effect(() => {
		const q = searchQuery.trim().toLowerCase();
		const data = graphData;
		const selected = selectedNodeKey;
		untrack(() => {
			const sigma = sigmaInstance;
			if (!sigma) return;

			if (!q && !selected) {
				matchedNodeKeys = null;
				sigma.setSetting('nodeReducer', null);
				sigma.refresh();
				return;
			}

			const s = getComputedStyle(document.documentElement);
			const accentColor = s.getPropertyValue('--accent').trim() || '#ff757f';
			const dimColor = s.getPropertyValue('--border').trim() || '#292e42';

			if (q && data) {
				const keys = new SvelteSet<string>();
				let firstKey: string | null = null;
				for (const node of data.nodes) {
					if (node.label.toLowerCase().includes(q)) {
						keys.add(node.node_id);
						if (!firstKey) firstKey = node.node_id;
					}
				}

				matchedNodeKeys = keys.size > 0 ? keys : null;

				if (keys.size === 0) {
					sigma.setSetting('nodeReducer', null);
					sigma.refresh();
					return;
				}

				sigma.setSetting('nodeReducer', (nodeKey: string, nodeData: VaultNodeAttrs) => {
					const { x, y, size, label } = nodeData;
					if (keys.has(nodeKey))
						return { x, y, color: accentColor, size: Math.max(size * 1.3, 12), label };
					return { x, y, color: dimColor, size: Math.min(size * 0.5, 4), label: '' };
				});
				sigma.refresh();

				if (firstKey && keys.size <= 5) {
					const displayData = sigma.getNodeDisplayData(firstKey);
					if (displayData) {
						sigma
							.getCamera()
							.animate({ x: displayData.x, y: displayData.y, ratio: 0.35 }, { duration: 600 });
					}
				}
			} else if (selected) {
				matchedNodeKeys = null;
				sigma.setSetting('nodeReducer', (nodeKey: string, nodeData: VaultNodeAttrs) => {
					const { x, y, color, size, label } = nodeData;
					if (nodeKey === selected)
						return { x, y, color: accentColor, size: Math.max(size * 1.3, 12) };
					return { x, y, color, size, label };
				});
				sigma.refresh();
			}
		});
	});

	// Focus search input when opened.
	$effect(() => {
		if (searchOpen) {
			requestAnimationFrame(() => searchInputEl?.focus());
		}
	});

	// Escape closes search first; then closes fullscreen. Ctrl+F opens search when fullscreen.
	$effect(() => {
		if (!expanded && !searchOpen) return;
		function onKey(e: KeyboardEvent) {
			if (e.key === 'Escape') {
				if (searchOpen) {
					searchOpen = false;
					searchQuery = '';
				} else {
					expanded = false;
				}
			} else if (e.key === 'f' && (e.ctrlKey || e.metaKey) && expanded && !searchOpen) {
				e.preventDefault();
				searchOpen = true;
			}
		}
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	});
</script>

<div class="vault-graph-wrap" class:fullscreen={expanded}>
	<div class="vault-graph-header">
		<span class="graph-title">Vault Graph</span>
		{#if !loading && !error && nodeCount > 0}
			{#if searchOpen}
				<input
					bind:this={searchInputEl}
					bind:value={searchQuery}
					class="graph-search-input"
					placeholder="Search nodes…"
					onkeydown={(e) => {
						if (e.key === 'Escape') {
							searchOpen = false;
							searchQuery = '';
							e.stopPropagation();
						}
					}}
				/>
				{#if searchQuery.trim()}
					<span class="search-match-count">{matchedCount}</span>
				{/if}
				<button
					class="graph-toggle"
					onclick={() => {
						searchOpen = false;
						searchQuery = '';
					}}
					use:tooltip={'Clear search'}
					aria-label="Clear search"
				>
					<X size={18} />
				</button>
			{:else}
				<span class="graph-stats">{nodeCount} nodes · {edgeCount} edges</span>
				<button
					class="graph-toggle"
					onclick={() => (searchOpen = true)}
					use:tooltip={'Hunt nodes'}
					aria-label="Hunt nodes"
				>
					<BowArrow size={18} />
				</button>
			{/if}
			<button
				class="graph-toggle"
				onclick={resetLayout}
				use:tooltip={'Reset layout'}
				aria-label="Reset layout"
			>
				<Waypoints size={18} />
			</button>
			<button
				class="graph-toggle"
				onclick={() => sigmaInstance?.getCamera().animatedReset()}
				use:tooltip={'Fit to viewport'}
				aria-label="Fit to viewport"
			>
				<Focus size={18} />
			</button>
			<button
				class="graph-toggle"
				onclick={toggleAnimation}
				use:tooltip={$graphAnimationEnabled ? 'Pause animation' : 'Resume animation'}
				aria-label={$graphAnimationEnabled ? 'Pause animation' : 'Resume animation'}
			>
				{#if $graphAnimationEnabled}
					<Pause size={18} />
				{:else}
					<Play size={18} />
				{/if}
			</button>
		{:else if !loading && !error}
			<span class="graph-stats">{nodeCount} nodes · {edgeCount} edges</span>
		{/if}
		<button
			class="graph-toggle"
			onclick={() => (expanded = !expanded)}
			use:tooltip={expanded ? 'Collapse' : 'Expand graph'}
			aria-label={expanded ? 'Collapse' : 'Expand graph'}
		>
			{#if expanded}
				<X size={18} />
			{:else}
				<Maximize2 size={18} />
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
		display: flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		padding: 0;
		background: none;
		border: 1px solid transparent;
		border-radius: 4px;
		cursor: pointer;
		color: var(--fg-muted);
		transition:
			color 0.15s,
			background 0.15s,
			border-color 0.15s;
		flex-shrink: 0;
	}

	.graph-toggle:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	.graph-search-input {
		flex: 1;
		min-width: 0;
		background: var(--bg-highlight);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--fg);
		font-size: 11px;
		font-family: inherit;
		padding: 3px 8px;
		outline: none;
	}

	.graph-search-input:focus {
		border-color: var(--accent);
	}

	.graph-search-input::placeholder {
		color: var(--fg-muted);
		opacity: 0.6;
	}

	.search-match-count {
		font-size: 11px;
		color: var(--fg-muted);
		opacity: 0.7;
		flex-shrink: 0;
		white-space: nowrap;
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
