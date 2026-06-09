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

	const {
		nodes,
		edges,
		focusNodeId = undefined,
		height = 180,
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

	type SimNode = SimulationNodeDatum & GraphNode;
	type SimLink = SimulationLinkDatum<SimNode>;

	let width = $state(200);
	let nodePositions = $state<{ id: string; x: number; y: number }[]>([]);
	let edgePositions = $state<{ x1: number; y1: number; x2: number; y2: number }[]>([]);

	const nodeById = $derived(new Map(nodes.map((n) => [n.node_id, n])));

	$effect(() => {
		const _nodes = nodes;
		const _edges = edges;

		if (_edges.length === 0) {
			nodePositions = [];
			edgePositions = [];
			return;
		}

		const w = untrack(() => width);
		const h = untrack(() => height);
		const fid = focusNodeId;

		const simNodes: SimNode[] = _nodes.map((n) => ({
			...n,
			x: w / 2 + (Math.random() - 0.5) * 60,
			y: h / 2 + (Math.random() - 0.5) * 60,
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
					.distance(65)
			)
			.force('charge', forceManyBody<SimNode>().strength(-140))
			.force('center', forceCenter<SimNode>(w / 2, h / 2))
			.force(
				'collide',
				forceCollide<SimNode>().radius((d) => (d.node_id === fid ? 18 : d.kind === 'tag' ? 9 : 12))
			)
			.force('x', forceX<SimNode>(w / 2).strength(0.05))
			.force('y', forceY<SimNode>(h / 2).strength(0.05));

		sim.on('tick', () => {
			for (const n of simNodes) {
				const r = n.node_id === fid ? 16 : n.kind === 'tag' ? 7 : 10;
				n.x = Math.max(r + 4, Math.min(n.x ?? 0, w - r - 4));
				n.y = Math.max(r + 4, Math.min(n.y ?? 0, h - r - 4));
			}
			nodePositions = simNodes.map((n) => ({ id: n.node_id, x: n.x ?? 0, y: n.y ?? 0 }));
			edgePositions = simLinks.map((link) => {
				const s = link.source as SimNode;
				const t = link.target as SimNode;
				return { x1: s.x ?? 0, y1: s.y ?? 0, x2: t.x ?? 0, y2: t.y ?? 0 };
			});
		});

		return () => sim.stop();
	});

	function nodeClass(node: GraphNode, isFocus: boolean): string {
		if (isFocus) return 'node node-focus';
		if (node.kind === 'tag') return 'node node-tag';
		return `node node-${node.source_type ?? 'default'}`;
	}

	function truncate(s: string, max: number): string {
		return s.length > max ? s.slice(0, max - 1) + '…' : s;
	}

	function handleClick(node: GraphNode) {
		if (node.kind === 'tag') {
			const tagName = node.node_id.startsWith('tag:') ? node.node_id.slice(4) : node.label;
			ontagclick?.(tagName);
		} else if (node.kind === 'entry') {
			const id = parseInt(node.node_id.slice(6));
			if (!isNaN(id)) onopen?.(id, node.label, node.source_type ?? undefined);
		}
	}
</script>

<div class="graph-wrap" bind:clientWidth={width}>
	{#if edges.length === 0}
		<p class="graph-empty">No connections.</p>
	{:else}
		<svg {width} {height}>
			{#each edgePositions as ep, i (i)}
				<line class="edge" x1={ep.x1} y1={ep.y1} x2={ep.x2} y2={ep.y2} />
			{/each}
			{#each nodePositions as pos (pos.id)}
				{@const node = nodeById.get(pos.id)}
				{#if node}
					{@const isFocus = pos.id === focusNodeId}
					{@const r = isFocus ? 12 : node.kind === 'tag' ? 6 : 9}
					{@const isClickable = (node.kind === 'entry' && !isFocus) || node.kind === 'tag'}
					{#if isClickable}
						<g
							class="node-group clickable"
							role="button"
							tabindex={0}
							onclick={() => handleClick(node)}
							onkeydown={(e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									handleClick(node);
								}
							}}
						>
							<circle class={nodeClass(node, false)} cx={pos.x} cy={pos.y} {r} />
							<text class="label" x={pos.x} y={pos.y + r + 13}>
								{truncate(node.label, 16)}
							</text>
						</g>
					{:else}
						<g class="node-group">
							<circle class={nodeClass(node, isFocus)} cx={pos.x} cy={pos.y} {r} />
							<text class="label label-focus" x={pos.x} y={pos.y + r + 13}>
								{truncate(node.label, 16)}
							</text>
						</g>
					{/if}
				{/if}
			{/each}
		</svg>
	{/if}
</div>

<style>
	.graph-wrap {
		width: 100%;
		overflow: hidden;
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

	.node {
		transition: opacity 0.15s;
	}

	.node:hover {
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
		fill: var(--fg-muted);
		cursor: pointer;
	}

	.node-default {
		fill: var(--fg-muted);
	}

	.clickable {
		cursor: pointer;
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
