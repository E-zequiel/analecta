<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
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
</script>

{#if $port === null}
	<SidecarLoadingScreen {timedOut} />
{:else}
	{@render children()}
{/if}
