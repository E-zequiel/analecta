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
	import type { GraphEdge, GraphNode } from '$lib/api/client';
	import { tooltip } from '$lib/actions/tooltip';

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

	const nodeById = $derived(new Map(nodes.map((n) => [n.node_id, n])));

	$effect(() => {
		const _nodes = nodes;
		const _edges = edges;

		if (_edges.length === 0) {
			nodePositions = [];
			edgePositions = [];
			currentSim = null;
			currentSimNodes = [];
			return;
		}

		const w = untrack(() => width);
		const h = untrack(() => height);
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
				n.x = Math.max(r + 10, Math.min(n.x ?? 0, w - r - 10));
				// extra bottom margin keeps labels (rendered 13–25px below center) inside the SVG
				n.y = Math.max(r + 10, Math.min(n.y ?? 0, h - r - 26));
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
			const tagName = node.node_id.startsWith('tag:') ? node.node_id.slice(4) : node.label;
			ontagclick?.(tagName);
		} else if (node.kind === 'entry') {
			const id = parseInt(node.node_id.slice(6));
			if (!isNaN(id)) onopen?.(id, node.label, node.source_type ?? undefined);
		}
	}

	function handlePointerDown(e: PointerEvent, node: GraphNode) {
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
	{#if edges.length === 0}
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
		fill: var(--accent);
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
		fill: #9ece6a;
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

	.node-group:hover .label {
		fill: var(--fg);
	}

	.label-focus {
		fill: var(--fg);
		font-weight: 600;
	}
</style>
