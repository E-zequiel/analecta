<script lang="ts">
	import { onMount } from 'svelte';
	import Graph from 'graphology';
	import Sigma from 'sigma';
	import FA2LayoutSupervisor from 'graphology-layout-forceatlas2/worker';
	import { entries as entriesApi, type GraphNode } from '$lib/api/client';

	interface Props {
		onentryopen?: (id: number, title: string, sourceType: string) => void;
	}

	const { onentryopen }: Props = $props();

	let container = $state<HTMLDivElement | undefined>(undefined);
	let nodeCount = $state(0);
	let edgeCount = $state(0);
	let loading = $state(true);
	let empty = $state(false);
	let error = $state(false);

	onMount(() => {
		if (!container) return;

		const graph = new Graph();
		let sigma: Sigma | null = null;
		let layout: FA2LayoutSupervisor | null = null;

		entriesApi
			.getGraph()
			.then((data) => {
				if (data.nodes.length === 0) {
					empty = true;
					loading = false;
					return;
				}

				for (const node of data.nodes) {
					const isEntry = node.kind === 'entry';
					graph.addNode(node.node_id, {
						label: node.label,
						size: isEntry ? 8 : 5,
						color: isEntry ? nodeColor(node) : 'var(--accent-dark)',
						x: Math.random() * 100,
						y: Math.random() * 100,
						kind: node.kind,
						source_type: node.source_type,
					});
				}

				for (const edge of data.edges) {
					if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
						graph.addEdge(edge.source, edge.target, { weight: edge.weight });
					}
				}

				nodeCount = data.nodes.length;
				edgeCount = data.edges.length;

				sigma = new Sigma(graph, container!, {
					renderEdgeLabels: false,
					defaultEdgeColor: '#3a3a4a',
					labelColor: { color: '#c0caf5' },
					labelSize: 11,
					labelFont: 'JetBrains Mono, monospace',
					minCameraRatio: 0.05,
					maxCameraRatio: 10,
				});

				sigma.on('clickNode', ({ node }: { node: string }) => {
					const attrs = graph.getNodeAttributes(node);
					if (attrs['kind'] !== 'entry') return;
					const rawId = node.replace('entry:', '');
					const id = parseInt(rawId, 10);
					if (!isNaN(id)) {
						onentryopen?.(
							id,
							attrs['label'] as string,
							(attrs['source_type'] as string) ?? 'article'
						);
					}
				});

				layout = new FA2LayoutSupervisor(graph, {
					settings: {
						gravity: 1,
						scalingRatio: 2,
						slowDown: 10,
						barnesHutOptimize: data.nodes.length > 150,
					},
					getEdgeWeight: 'weight',
				});
				layout.start();

				// Stop after layout stabilises — avoids burning CPU indefinitely
				setTimeout(() => {
					layout?.stop();
				}, 4000);

				loading = false;
			})
			.catch(() => {
				error = true;
				loading = false;
			});

		return () => {
			layout?.kill();
			sigma?.kill();
		};
	});

	function nodeColor(node: GraphNode): string {
		switch (node.source_type) {
			case 'youtube':
				return '#f7768e';
			case 'substack':
				return '#e0af68';
			case 'article':
			default:
				return '#7aa2f7';
		}
	}
</script>

<div class="vault-graph">
	{#if loading}
		<div class="graph-state">
			<span class="hint">Loading graph…</span>
		</div>
	{:else if error}
		<div class="graph-state">
			<span class="hint">Could not load graph.</span>
		</div>
	{:else if empty}
		<div class="graph-state">
			<span class="hint">No connections yet. Add [[wikilinks]] or #hashtags to your entries.</span>
		</div>
	{:else}
		<div class="graph-meta">
			<span class="meta-item">{nodeCount} nodes</span>
			<span class="meta-sep">·</span>
			<span class="meta-item">{edgeCount} edges</span>
		</div>
	{/if}
	<div bind:this={container} class="graph-canvas" class:hidden={loading || error || empty}></div>
</div>

<style>
	.vault-graph {
		display: flex;
		flex-direction: column;
		height: 100%;
		position: relative;
		background: var(--bg);
	}

	.graph-canvas {
		flex: 1;
		min-height: 0;
	}

	.graph-canvas.hidden {
		display: none;
	}

	.graph-state {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 100%;
	}

	.hint {
		color: var(--fg-muted);
		font-size: 13px;
		text-align: center;
		padding: 1rem;
		max-width: 340px;
	}

	.graph-meta {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.4rem 0.75rem;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.meta-item {
		font-size: 11px;
		color: var(--fg-muted);
	}

	.meta-sep {
		font-size: 11px;
		color: var(--border);
	}
</style>
