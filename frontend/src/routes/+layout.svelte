<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { invoke } from '@tauri-apps/api/core';
	import { listen } from '@tauri-apps/api/event';
	import { getCurrent, onOpenUrl } from '@tauri-apps/plugin-deep-link';
	import { check, type Update } from '@tauri-apps/plugin-updater';
	import { port } from '$lib/stores/sidecar';
	import { entryAddedTick } from '$lib/stores/sse';
	import { pkm } from '$lib/api/client';
	import SidecarLoadingScreen from '$lib/components/SidecarLoadingScreen.svelte';
	import TagTree from '$lib/components/TagTree.svelte';
	import UpdateBanner from '$lib/components/UpdateBanner.svelte';

	let { children } = $props();
	let timedOut = $state(false);
	let pendingDeepLink = $state<string | null>(null);
	let pendingUpdate = $state<Update | null>(null);

	async function handleDeepLink(rawUrl: string) {
		pendingDeepLink = rawUrl;
	}

	onMount(() => {
		const timeout = setTimeout(() => {
			timedOut = true;
		}, 10_000);

		// Sidecar may have started before the frontend mounted — poll once.
		invoke<number | null>('get_sidecar_port').then((existingPort) => {
			if (existingPort !== null) {
				clearTimeout(timeout);
				port.set(existingPort);
			}
		});

		const unlistenSidecar = listen<{ port: number }>('sidecar-ready', (event) => {
			clearTimeout(timeout);
			port.set(event.payload.port);
		});

		// "App not running" case: URL that launched the app
		getCurrent().then((urls) => {
			if (urls && urls.length > 0) handleDeepLink(urls[0]);
		});

		// "App not running" case: subsequent URLs while app is open via plugin
		const unlistenDeepLink = onOpenUrl((urls) => {
			if (urls.length > 0) handleDeepLink(urls[0]);
		});

		// "App already running" case: relayed via single-instance Rust emit
		const unlistenRelay = listen<string>('deep-link', (event) => {
			handleDeepLink(event.payload);
		});

		return () => {
			clearTimeout(timeout);
			unlistenSidecar.then((u) => u());
			unlistenDeepLink.then((u) => u());
			unlistenRelay.then((u) => u());
		};
	});

	$effect(() => {
		const p = $port;
		const url = pendingDeepLink;
		if (p === null || url === null) return;

		pendingDeepLink = null;
		pkm.parseUrl(url).then((result) => {
			if (result.entry_id !== null) {
				goto(`/viewer/${result.entry_id}`);
			}
		});
	});

	$effect(() => {
		if ($port === null) return;
		check().then((update) => {
			if (update?.available) pendingUpdate = update;
		}).catch(() => {});
	});

	$effect(() => {
		const p = $port;
		if (p === null) return;

		const source = new EventSource(`http://localhost:${p}/api/v1/system/events`);
		source.addEventListener('message', async (ev) => {
			try {
				const data = JSON.parse(ev.data) as { type: string };
				if (data.type === 'entry_added') {
					entryAddedTick.update((n) => n + 1);
					await invoke('notify_success', { title: 'Analecta', body: 'New entry saved.' });
				}
			} catch {
				// ignore malformed events
			}
		});

		return () => source.close();
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
	{#if pendingUpdate}
		<UpdateBanner update={pendingUpdate} />
	{/if}
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
				<TagTree />
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
