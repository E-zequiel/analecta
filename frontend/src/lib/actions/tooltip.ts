const SHOW_DELAY_MS = 300;
const GAP_PX = 6;

let tooltipEl: HTMLDivElement | null = null;
let activeNode: HTMLElement | null = null;
let showTimer: ReturnType<typeof setTimeout> | null = null;

function ensureEl(): HTMLDivElement {
	if (!tooltipEl) {
		tooltipEl = document.createElement('div');
		tooltipEl.id = 'analecta-tooltip';
		document.body.appendChild(tooltipEl);
	}
	return tooltipEl;
}

function show(node: HTMLElement, label: string): void {
	if (!label) return;
	const el = ensureEl();
	el.textContent = label;
	el.style.visibility = 'hidden';
	el.classList.add('visible');

	const nodeRect = node.getBoundingClientRect();
	const tipRect = el.getBoundingClientRect();
	const vw = window.innerWidth;
	const vh = window.innerHeight;

	let x = nodeRect.left + nodeRect.width / 2 - tipRect.width / 2;
	let y = nodeRect.bottom + GAP_PX;

	if (y + tipRect.height > vh - GAP_PX) {
		y = nodeRect.top - tipRect.height - GAP_PX;
	}

	x = Math.max(GAP_PX, Math.min(vw - tipRect.width - GAP_PX, x));
	y = Math.max(GAP_PX, y);

	el.style.left = `${x}px`;
	el.style.top = `${y}px`;
	el.style.visibility = '';
}

function hide(): void {
	if (showTimer !== null) {
		clearTimeout(showTimer);
		showTimer = null;
	}
	activeNode = null;
	if (tooltipEl) tooltipEl.classList.remove('visible');
}

export function tooltip(node: HTMLElement, label: string) {
	let currentLabel = label;

	function onEnter(): void {
		activeNode = node;
		if (showTimer !== null) clearTimeout(showTimer);
		showTimer = setTimeout(() => {
			if (activeNode === node) show(node, currentLabel);
		}, SHOW_DELAY_MS);
	}

	function onLeave(): void {
		hide();
	}

	node.addEventListener('mouseenter', onEnter);
	node.addEventListener('mouseleave', onLeave);
	node.addEventListener('click', hide);

	return {
		update(newLabel: string): void {
			currentLabel = newLabel;
			if (activeNode === node && tooltipEl?.classList.contains('visible')) {
				show(node, currentLabel);
			}
		},
		destroy(): void {
			node.removeEventListener('mouseenter', onEnter);
			node.removeEventListener('mouseleave', onLeave);
			node.removeEventListener('click', hide);
			if (activeNode === node) hide();
		},
	};
}
