// Infra canary: proves the runner, the jsdom environment, and Svelte 5
// compilation (runes + testing-library mount) all work. If this file fails
// after a toolchain bump, suspect the whole suite's silence — fix this
// before debugging individual tests.
import { render } from '@testing-library/svelte';
import { expect, test } from 'vitest';
import Canary from './Canary.svelte';

test('jsdom environment is active', () => {
	expect(typeof document).toBe('object');
});

test('svelte component mounts via testing-library', () => {
	const { getByText } = render(Canary, { props: { label: 'canary-ok' } });
	expect(getByText('canary-ok')).toBeTruthy();
});
