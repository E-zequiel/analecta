# Syntax Highlighting — Analecta

Fenced code blocks in the markdown viewer are highlighted by
[Shiki](https://shiki.style/) using the **tokyo-night** theme.

---

## Package choice

Shiki was chosen over highlight.js and Prism because it uses TextMate grammars
(the same engine as VS Code), producing accurate token boundaries for complex
languages. The fine-grained `@shikijs/*` bundle lets the app import only the
needed language grammars instead of the full multi-MB registry.

The **JavaScript RegExp engine** (`shiki/engine/javascript`) is used instead of
the default WASM engine. This keeps the Electron bundle simpler — no `.wasm`
asset to package or load asynchronously — and the JS engine is fast enough for
the document sizes the viewer handles.

The highlighter is created once at module load with `createHighlighterCoreSync`
(no async init, no loading spinner).

---

## Supported languages

Python · Bash · Rust · TypeScript · JavaScript · HTML · CSS · Go · Java · C ·
C++ · SQL · YAML · JSON

---

## CSP constraint and the HAST transformer

Shiki's default output places token colors in `style=""` attributes on every
`<span>` and `<pre>`. The app's Content Security Policy has
`style-src-attr 'none'` (applied in packaged builds), which would silently strip
all token colors in production while appearing to work in dev.

The solution is a two-part pattern:

### 1. Custom HAST transformer (`shiki-style-to-class.ts`)

A transformer runs at render time and replaces every inline `style=` attribute
with a deterministic CSS class name derived from a `cyrb53` hash of the style
string. Classes look like `.__s_<hex>`.

The HAST `span()` and `pre()` hooks are used — **not** the `tokens()` hook.
This distinction matters: see the [sync API limitation](#sync-api-limitation)
section below.

### 2. Pre-generated static CSS (`shiki-classes.css`)

A build-time script (`frontend/scripts/gen-shiki-css.mjs`) runs the same
transformer over representative code samples, collects every style → class
mapping into a registry, and writes `frontend/src/lib/markdown/shiki-classes.css`
as a committed static file.

Because `cyrb53` is a pure function of the style string, the classes emitted at
render time and the selectors in the static CSS file are always identical.

The CSS file is loaded via `import '$lib/markdown/shiki-classes.css'` in the
viewer and editor pages — covered by `style-src-elem 'self' app:`.

### 3. Light-theme overrides (`DARK_TO_LIGHT` map)

`gen-shiki-css.mjs` also emits a set of `.theme-light .__s_<hash>` override rules.
For each entry in the dark CSS registry, `translateToLight()` replaces known
dark-theme hex values using a hardcoded `DARK_TO_LIGHT` lookup table keyed on
uppercase hex without `#`.

Each replacement is one of two kinds:

- **CSS variable reference** (`var(--xxx)`) — used when the dark hex is an exact
  match for one of the project's existing CSS custom properties. The light value
  of that variable is already contrast-checked in `app.css`.
- **Literal hex** — sourced from the official
  [Tokyo Night Light VS Code theme](https://github.com/enkia/tokyo-night-vscode-theme)
  for colors that have no project variable equivalent.

Exception: comment tokens use `var(--fg-muted)` instead of the official `#888B94`,
which fails the 3:1 WCAG minimum against the light background.

Entries with no matching key in `DARK_TO_LIGHT` are not emitted — the dark rule
then applies in both themes (intentional for structural colors that need no
light-mode override).

The `shiki-classes.css` output has two sections: dark rules (byte-identical to
before) followed by light overrides. Both sections are committed; neither is
edited manually.

---

## Sync API limitation — why `transformerStyleToClass` is not used

`@shikijs/transformers` ships `transformerStyleToClass`, which is the intended
upstream solution to the same problem. It does not work with
`createHighlighterCoreSync`.

The transformer's `tokens()` hook checks `token.htmlStyle`, which is only
populated on the async code path. With the sync API, tokens expose only
`token.color` and `token.fontStyle`. The transformer silently produces zero
class mappings for all token spans.

The custom `shiki-style-to-class.ts` reimplements the same logic using the
`span()` and `pre()` HAST hooks, where `t.properties.style` is correctly
populated regardless of sync vs async. The `cyrb53` hash implementation is
identical to the one inside `@shikijs/transformers` — class names are
inter-compatible if the upstream transformer is ever fixed.

`@shikijs/transformers` is not a declared dependency (removed as unused) — re-add it if
reviving this path.

---

## File map

| File | Role |
|------|------|
| `frontend/src/lib/markdown/renderer.ts` | Markdown-it instance; loads Shiki highlighter + transformer |
| `frontend/src/lib/markdown/shiki-style-to-class.ts` | Runtime HAST transformer (cyrb53 hash) |
| `frontend/src/lib/markdown/shiki-classes.css` | Pre-generated token CSS — committed, do not edit manually; two sections: dark rules + `.theme-light` overrides |
| `frontend/scripts/gen-shiki-css.mjs` | Generator script — run at upgrade time; emits dark rules and `.theme-light .__s_*` overrides via `DARK_TO_LIGHT` map |

---

## Upgrading Shiki

`shiki-classes.css` is regenerated automatically before every production build.
The frontend `build` script is `node scripts/gen-shiki-css.mjs && vite build`
in `frontend/package.json` — the generator runs inline, independent of pnpm's
`enable-pre-post-scripts` setting.

When upgrading `shiki`, `@shikijs/themes`, or `@shikijs/langs`, the new CSS is
produced during the next `pnpm build` (step 10 of `check.sh`). Commit the
updated `shiki-classes.css` alongside the package changes.

`@shikijs/markdown-it` pins `markdown-it` as a direct (non-peer) dependency —
`^14.3.0` as of `@shikijs/markdown-it@4.4.3` (`pnpm view @shikijs/markdown-it@<version>
dependencies` shows the exact range for a given release). `markdown-it` cannot
be upgraded past that ceiling until `@shikijs/markdown-it` itself widens it —
expect `markdown-it` to keep appearing under "Blocked" in automated
dependency-update PRs for this reason specifically, not as a `check.sh`
regression to chase.

To regenerate manually without a full build:

```bash
mise exec -- pnpm --filter frontend run gen-shiki-css
```

> **Package version policy:** observe the 10-day minimum cooldown from the
> release date before adopting a new Shiki version.

After upgrading `shiki` or `@shikijs/themes`, diff the dark section of the
regenerated `shiki-classes.css` against the previous version. If a hex value
that was a key in `DARK_TO_LIGHT` no longer appears, its light override silently
disappears (the dark rule then applies in both themes). Update `DARK_TO_LIGHT`
in `gen-shiki-css.mjs` accordingly and regenerate.

---

## Adding a language

1. Import the grammar in `frontend/scripts/gen-shiki-css.mjs` and add a
   representative code sample to `SAMPLES`.
2. Import the same grammar in `frontend/src/lib/markdown/renderer.ts` and add
   it to the `langs` array.
3. Run `check.sh` — the `prebuild` step regenerates `shiki-classes.css` before
   the Vite build.
4. Commit `renderer.ts`, `gen-shiki-css.mjs`, and the updated `shiki-classes.css`.
