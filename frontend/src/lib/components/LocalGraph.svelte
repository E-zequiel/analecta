<script lang="ts">
	import { untrack } from 'svelte';
	import {
		forceCenter,
		forceCollide,
		forceLink,
		forceManyBody,
		forceSimulation,
		forceX,
		forceY,
	} from 'd3-force';
	import type { SimulationLinkDatum, SimulationNodeDatum } from 'd3-force';
	import { entries as entriesApi, type GraphEdge, type GraphNode } from '$lib/api/client';
	import { tooltip } from '$lib/actions/tooltip';
	import { showContextMenu } from '$lib/stores/contextMenu';
	import { openEntryTab } from '$lib/stores/tabs';

	const {
		nodes,
		edges,
		focusNodeId = undefined,
		height = 200,
		onopen,
		ontagclick,
	}: {
		nodes: GraphNode[];
		edges: GraphEdge[];
		focusNodeId?: string;
		height?: number;
		onopen?: (id: number, title: string, sourceType?: string) => void;
		ontagclick?: (tagName: string) => void;
	} = $props();

	type SimNode = SimulationNodeDatum & GraphNode & { fx?: number | null; fy?: number | null };
	type SimLink = SimulationLinkDatum<SimNode>;

	let width = $state(200);
	let nodePositions = $state<{ id: string; x: number; y: number }[]>([]);
	let edgePositions = $state<{ x1: number; y1: number; x2: number; y2: number }[]>([]);
	let svgEl = $state<SVGSVGElement | undefined>(undefined);

	// Live refs for drag interaction (not reactive — updated by $effect)
	let currentSim: ReturnType<typeof forceSimulation<SimNode>> | null = null;
	let currentSimNodes: SimNode[] = [];

	// Mutable bounds read by the tick handler at tick time so resize updates clamp correctly
	const bounds = { w: 200, h: 200 };

	const nodeById = $derived(new Map(nodes.map((n) => [n.node_id, n])));

	$effect(() => {
		const _nodes = nodes;
		const _edges = edges;

		if (_edges.length === 0) {
			if (_nodes.length > 0) {
				const w = width; // tracked — recenters on resize
				const h = untrack(() => height);
				bounds.w = w;
				bounds.h = h;
				const fid = untrack(() => focusNodeId);
				const focusNode = _nodes.find((n) => n.node_id === fid) ?? _nodes[0];
				nodePositions = [{ id: focusNode.node_id, x: w / 2, y: h / 2 }];
			} else {
				nodePositions = [];
			}
			edgePositions = [];
			currentSim = null;
			currentSimNodes = [];
			return;
		}

		const w = untrack(() => width);
		const h = untrack(() => height);
		bounds.w = w;
		bounds.h = h;
		// Untracked so that clearing focus (on tag click) doesn't rebuild the sim
		const fid = untrack(() => focusNodeId);

		// Scale forces with available area so nodes spread proportionally
		const area = Math.sqrt(w * h);
		const linkDist = Math.min(area * 0.28, 220);
		const chargeStr = -Math.min(area * 1.1, 600);

		const simNodes: SimNode[] = _nodes.map((n) => ({
			...n,
			x: w / 2 + (Math.random() - 0.5) * 80,
			y: h / 2 + (Math.random() - 0.5) * 80,
		}));
		const simLinks: SimLink[] = _edges.map((e) => ({
			source: e.source,
			target: e.target,
		}));

		const sim = forceSimulation<SimNode>(simNodes)
			.force(
				'link',
				forceLink<SimNode, SimLink>(simLinks)
					.id((d) => d.node_id)
					.distance(linkDist)
			)
			.force('charge', forceManyBody<SimNode>().strength(chargeStr))
			.force('center', forceCenter<SimNode>(w / 2, h / 2))
			.force(
				'collide',
				forceCollide<SimNode>().radius((d) => (d.node_id === fid ? 24 : d.kind === 'tag' ? 15 : 18))
			)
			.force('x', forceX<SimNode>(w / 2).strength(0.05))
			.force('y', forceY<SimNode>(h / 2).strength(0.05));

		currentSim = sim;
		currentSimNodes = simNodes;

		sim.on('tick', () => {
			for (const n of simNodes) {
				const r = n.node_id === fid ? 14 : n.kind === 'tag' ? 7 : 10;
				n.x = Math.max(r + 10, Math.min(n.x ?? 0, bounds.w - r - 10));
				// extra bottom margin keeps labels (rendered 13–25px below center) inside the SVG
				n.y = Math.max(r + 10, Math.min(n.y ?? 0, bounds.h - r - 26));
			}
			nodePositions = simNodes.map((n) => ({ id: n.node_id, x: n.x ?? 0, y: n.y ?? 0 }));
			edgePositions = simLinks.map((link) => {
				const s = link.source as SimNode;
				const t = link.target as SimNode;
				return { x1: s.x ?? 0, y1: s.y ?? 0, x2: t.x ?? 0, y2: t.y ?? 0 };
			});
		});

		return () => {
			sim.stop();
			if (currentSim === sim) {
				currentSim = null;
				currentSimNodes = [];
			}
		};
	});

	// Refit the graph when the container is resized (e.g., fullscreen toggle)
	$effect(() => {
		const w = width; // tracked
		const h = height; // tracked

		const timer = setTimeout(() => {
			untrack(() => {
				if (bounds.w === w && bounds.h === h) return;
				bounds.w = w;
				bounds.h = h;

				const sim = currentSim;
				if (sim) {
					sim
						.force('center', forceCenter<SimNode>(w / 2, h / 2))
						.force('x', forceX<SimNode>(w / 2).strength(0.05))
						.force('y', forceY<SimNode>(h / 2).strength(0.05))
						.alpha(0.4)
						.restart();
				} else if (nodePositions.length > 0) {
					// Single-node case: recenter
					const pos = nodePositions[0];
					nodePositions = [{ id: pos.id, x: w / 2, y: h / 2 }];
				}
			});
		}, 50);

		return () => clearTimeout(timer);
	});

	function nodeClass(node: GraphNode, isFocus: boolean): string {
		if (isFocus) return 'node node-focus';
		if (node.kind === 'tag') return 'node node-tag';
		return `node node-${node.source_type ?? 'default'}`;
	}

	function truncate(s: string, max: number = 24): string {
		return s.length > max ? s.slice(0, max - 1) + '…' : s;
	}

	function handleClick(node: GraphNode) {
		if (node.node_id === focusNodeId) return;
		if (node.kind === 'tag') {
			const tagName = node.label.startsWith('#') ? node.label.slice(1) : node.label;
			ontagclick?.(tagName);
		} else if (node.kind === 'entry') {
			const id = parseInt(node.node_id.slice(6));
			if (!isNaN(id)) onopen?.(id, node.label, node.source_type ?? undefined);
		}
	}

	function handleMiddleClick(e: MouseEvent, node: GraphNode) {
		if (e.button !== 1 || node.kind !== 'entry') return;
		e.preventDefault();
		const id = parseInt(node.node_id.slice(6));
		if (!isNaN(id)) openEntryTab(id, node.label, true, node.source_type ?? undefined);
	}

	async function handleContextMenu(e: MouseEvent, node: GraphNode) {
		e.preventDefault();
		if (node.kind !== 'entry') return;
		const id = parseInt(node.node_id.slice(6));
		if (isNaN(id)) return;
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

	function handlePointerDown(e: PointerEvent, node: GraphNode) {
		if (e.button !== 0) return;
		const found = currentSimNodes.find((n) => n.node_id === node.node_id);
		if (!svgEl || !found || !currentSim) return;

		e.preventDefault();

		// Typed aliases so TypeScript tracks non-nullability inside closures
		const sn: SimNode = found;
		const sim = currentSim;

		// Fix at current simulated position — do NOT snap to cursor
		sn.fx = sn.x ?? 0;
		sn.fy = sn.y ?? 0;
		sim.alphaTarget(0.3).restart();

		let moved = false;
		const startX = e.clientX;
		const startY = e.clientY;
		const rect = svgEl.getBoundingClientRect();
		const svgW = svgEl.clientWidth;
		const svgH = svgEl.clientHeight;

		function onMove(ev: PointerEvent) {
			const dx = ev.clientX - startX;
			const dy = ev.clientY - startY;
			if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
			sn.fx = Math.max(12, Math.min(ev.clientX - rect.left, svgW - 12));
			sn.fy = Math.max(12, Math.min(ev.clientY - rect.top, svgH - 12));
		}

		function onUp() {
			sn.fx = null;
			sn.fy = null;
			sim.alphaTarget(0);
			window.removeEventListener('pointermove', onMove);
			window.removeEventListener('pointerup', onUp);
			if (!moved) handleClick(node);
		}

		window.addEventListener('pointermove', onMove);
		window.addEventListener('pointerup', onUp);
	}
</script>

<div class="graph-wrap" bind:clientWidth={width}>
	{#if nodes.length === 0}
		<p class="graph-empty">No connections.</p>
	{:else}
		<svg bind:this={svgEl} {width} {height}>
			{#each edgePositions as ep, i (i)}
				<line class="edge" x1={ep.x1} y1={ep.y1} x2={ep.x2} y2={ep.y2} />
			{/each}
			{#each nodePositions as pos (pos.id)}
				{@const node = nodeById.get(pos.id)}
				{#if node}
					{@const isFocus = pos.id === focusNodeId}
					{@const r = isFocus ? 14 : node.kind === 'tag' ? 7 : 10}
					<g
						class="node-group"
						class:clickable={!isFocus}
						role="button"
						tabindex={0}
						use:tooltip={node.label}
						onpointerdown={(e) => handlePointerDown(e, node)}
						onmousedown={(e) => handleMiddleClick(e, node)}
						oncontextmenu={(e) => void handleContextMenu(e, node)}
						onkeydown={(e) => {
							if (e.key === 'Enter' || e.key === ' ') {
								e.preventDefault();
								handleClick(node);
							}
						}}
					>
						<circle class={nodeClass(node, isFocus)} cx={pos.x} cy={pos.y} {r} />
						<text class="label" class:label-focus={isFocus} x={pos.x} y={pos.y + r + 13}>
							{truncate(node.label)}
						</text>
					</g>
				{/if}
			{/each}
			{#if edges.length === 0}
				<text class="caption" x={width / 2} y={height / 2 + 44}>No connections.</text>
			{/if}
		</svg>
	{/if}
</div>

<style>
	.graph-wrap {
		width: 100%;
		overflow: hidden;
		touch-action: none;
	}

	.graph-empty {
		padding: 6px 10px 8px;
		font-size: 12px;
		color: var(--fg-muted);
		font-style: italic;
		margin: 0;
	}

	svg {
		display: block;
		overflow: hidden;
	}

	.edge {
		stroke: var(--border);
		stroke-width: 1;
	}

	.node-group {
		outline: none;
	}

	.node-group.clickable {
		cursor: pointer;
	}

	.node-group:not(.clickable) {
		cursor: grab;
	}

	.node-group:not(.clickable):active {
		cursor: grabbing;
	}

	.node {
		transition: opacity 0.15s;
	}

	.node-group:hover .node {
		opacity: 0.75;
	}

	.node-focus {
		fill: var(--green);
		stroke: var(--bg);
		stroke-width: 2;
	}

	.node-article {
		fill: var(--cyan);
	}

	.node-youtube {
		fill: var(--red);
	}

	.node-substack {
		fill: var(--accent-warm);
	}

	.node-tag {
		fill: var(--magenta);
		cursor: pointer;
	}

	.node-default {
		fill: var(--fg-muted);
	}

	.label {
		fill: var(--fg-muted);
		font-size: 12px;
		text-anchor: middle;
		font-family: inherit;
		pointer-events: none;
		user-select: none;
		opacity: 1;
	}

	.caption {
		fill: var(--fg-muted);
		font-size: 11px;
		text-anchor: middle;
		font-family: inherit;
		font-style: italic;
		pointer-events: none;
		user-select: none;
	}

	.node-group:hover .label {
		fill: var(--fg);
	}

	.label-focus {
		fill: var(--fg);
		font-weight: 600;
	}
</style>
