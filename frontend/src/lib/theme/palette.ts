export const palette = {
	// Backgrounds
	bg: '#1a1b26',
	bgDark: '#16161e',
	bgDark2: '#121218',
	bgAlt: '#24283b',
	bgHighlight: '#292e42',
	// Foregrounds
	fg: '#c0caf5',
	fgDark: '#a9b1d6',
	fgMuted: '#565f89',
	// Accents
	accent: '#7aa2f7',
	accentDark: '#3d59a1',
	accentWarm: '#ff9e64',
	// Syntax / status
	cyan: '#7dcfff',
	green: '#9ece6a',
	yellow: '#e0af68',
	magenta: '#bb9af7',
	red: '#ff757f',
	redAlt: '#db4b4b',
	teal: '#1abc9c',
	// Structure
	border: '#292e42',
	terminal: '#414868',
} as const;

export type PaletteKey = keyof typeof palette;
