/** Triggers a one-shot accent-color background flash on `node` via WAAPI.
 * Re-triggers on every `key` change (including when key stays non-null but changes value).
 * Pass `undefined` / `null` to suppress the flash while the element is inactive.
 */
export function flash(node: HTMLElement, key: unknown) {
	function trigger() {
		const accent =
			getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#ff757f';
		const hex = accent.replace('#', '');
		const r = parseInt(hex.slice(0, 2), 16);
		const g = parseInt(hex.slice(2, 4), 16);
		const b = parseInt(hex.slice(4, 6), 16);
		node.animate(
			[
				{ backgroundColor: `rgba(${r}, ${g}, ${b}, 0.22)` },
				{ backgroundColor: 'rgba(0, 0, 0, 0)' },
			],
			{ duration: 500, easing: 'ease-out', fill: 'none' }
		);
	}

	if (key != null) trigger();

	return {
		update(newKey: unknown) {
			if (newKey != null) trigger();
		},
	};
}
