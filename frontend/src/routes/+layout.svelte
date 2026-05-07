<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { listen } from '@tauri-apps/api/event';
	import { port } from '$lib/stores/sidecar';
	import SidecarLoadingScreen from '$lib/components/SidecarLoadingScreen.svelte';

	let { children } = $props();
	let timedOut = $state(false);

	onMount(() => {
		const timeout = setTimeout(() => {
			timedOut = true;
		}, 10_000);

		const unlistenPromise = listen<{ port: number }>('sidecar-ready', (event) => {
			clearTimeout(timeout);
			port.set(event.payload.port);
		});

		return () => {
			clearTimeout(timeout);
			unlistenPromise.then((unlisten) => unlisten());
		};
	});

	const navItems = [
		{ href: '/', label: 'Dashboard' },
		{ href: '/settings', label: 'Settings' }
	];

	function isActive(href: string, pathname: string): boolean {
		return href === '/' ? pathname === '/' : pathname.startsWith(href);
	}
</script>

{#if $port === null}
	<SidecarLoadingScreen {timedOut} />
{:else}
	<div class="shell">
		<aside class="sidebar">
			<div class="logo">Analecta</div>
			<nav>
				{#each navItems as item}
					<a href={item.href} class:active={isActive(item.href, $page.url.pathname)}>
						{item.label}
					</a>
				{/each}
			</nav>
			<div class="tag-tree">
				<!-- tag tree: D5 -->
			</div>
		</aside>
		<main>
			{@render children()}
		</main>
	</div>
{/if}

<style>
	.shell {
		display: flex;
		height: 100vh;
		overflow: hidden;
	}

	.sidebar {
		width: 260px;
		flex-shrink: 0;
		background: var(--bg-dark);
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		overflow-y: auto;
	}

	.logo {
		padding: 1.25rem 1rem;
		font-weight: 700;
		color: var(--fg);
		letter-spacing: 0.02em;
		border-bottom: 1px solid var(--border);
	}

	nav {
		padding: 0.5rem 0;
	}

	nav a {
		display: block;
		padding: 0.5rem 1rem;
		margin: 0.125rem 0.5rem;
		color: var(--fg-muted);
		text-decoration: none;
		border-radius: 4px;
		transition: color 0.15s, background 0.15s;
	}

	nav a:hover {
		color: var(--fg);
		background: var(--bg-highlight);
	}

	nav a.active {
		color: var(--accent);
		background: var(--bg-highlight);
	}

	.tag-tree {
		flex: 1;
		padding: 0.5rem 0;
		border-top: 1px solid var(--border);
		margin-top: 0.5rem;
	}

	main {
		flex: 1;
		overflow-y: auto;
		background: var(--bg);
	}
</style>
