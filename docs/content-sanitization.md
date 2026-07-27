# Content Sanitization: Extraction-to-Render Trust Boundary

This document traces how HTML extracted from an arbitrary, untrusted third-party web page becomes the Markdown a user reads in Analecta, and states the specific invariants that keep that path safe. It complements `docs/electron-shell-security.md`, which covers the Electron IPC/protocol/CSP surface; this document covers the ground that doc doesn't — the Python HTML→Markdown converter and the frontend Markdown→HTML renderer, neither of which is Electron-shell-specific.

**Scope.** Analecta's entire purpose is to fetch and display content from URLs the user does not control the origin of. The extracted HTML — from `readability-lxml` or `trafilatura`, see `docs/extraction.md` — must be treated as attacker-influenced input. `docs/electron-shell-security.md`'s Threat Model section already names this directly: "a crafted article that exploits a markdown-it XSS vulnerability or an injected script via a remote image payload." This document is the evidence for the first half of that claim.

**Out of scope.** Remote-image loading is an egress/privacy concern, not an injection concern — see `docs/privacy.md` § Image Egress for how it's handled. Do not fold that work into this document; the invariants here are about code/markup injection.

---

## The Pipeline

```
Extracted HTML  →  converter.py (Markdown)  →  saved .md file  →  renderer.ts (HTML)  →  {@html} in Svelte
  (untrusted)       (Python, backend)                              (TypeScript, frontend)
```

Four stages. Each has a specific job; the actual injection boundary is Stage 3, not Stage 2 — this is deliberate, not an oversight, and is called out below.

### Stage 1 — HTML acquisition

Extraction fetches the page as a single, un-executed HTTP response (`extraction/article.py`) — see `docs/extraction.md` for how the resulting HTML is picked apart by `readability-lxml`/`trafilatura`. Nothing about acquisition itself mitigates injection; the fetched HTML is attacker-influenced input from this point on. That's what Stage 3 below is for.

### Stage 2 — HTML → Markdown (`backend/src/analecta/markdown/converter.py`)

`_Converter` (a `markdownify.MarkdownConverter` subclass) walks the **parsed DOM tree**, not the raw HTML string. This matters: an unrecognized tag contributes only the converted text of its children to the output — its own markup is never copied through. There is no code path in `markdownify` that reproduces an arbitrary source tag verbatim in the Markdown output.

- `<script>` / `<style>`: `markdownify` itself defines `convert_script`/`convert_style` returning `''` — the tag **and its text content** are dropped entirely, not just detagged. This is upstream `markdownify` behavior (`pyproject.toml` pins `markdownify>=0.14.1`), not Analecta code — **re-verify this on any `markdownify` version bump**, the same convention already applied in-code to `article.py`'s `_readability_class_weight`, a manually maintained replica of `readability.readability.class_weight` (comment: "re-verify against `readability.readability.class_weight` on any readability-lxml bump").
- `converter.py`'s own `_STRIP_RE` (line 13) additionally regex-strips `<script>`/`<style>` blocks before the DOM parse even runs. This is redundant with the point above — defense-in-depth on top of an already-safe default, not the load-bearing control.
- **Text nodes are not HTML-escaped at this stage.** `markdownify`'s `process_text` escapes Markdown-syntax characters (`*`, `_`, backslash, …) but not `<`/`>`/`"`. A literal `<script>` appearing as prose text in the source page (not a real tag) passes through into the `.md` file unescaped. **This is safe by design, not a gap** — see Stage 3, which is what actually neutralizes it. Do not "fix" this in `converter.py` by adding HTML-escaping; it would double-escape legitimate Markdown-significant text and the real boundary belongs in Stage 3 regardless.
- `<a href>` / `<img src>` values are preserved verbatim into `[text](href)` / `![alt](src)` Markdown syntax — no URL-scheme filtering happens here either. Same reasoning: Stage 3 owns scheme validation.

**Known non-security gap** (tracked separately, not a sanitization issue): `convert_pre` (line 134) drops raw code-block text via `code.get_text()` into a fenced block with no escaping of backtick runs. A code sample containing a longer backtick run than the fence can break out of the fence into the surrounding document. Content-integrity/cosmetic only — anything injected this way still has to pass through Stage 3, which treats it as inert text or a rejected link/image, same as everything else on this page.

### Stage 3 — Markdown → HTML render (`frontend/src/lib/markdown/renderer.ts`) — the actual trust boundary

This is the stage that makes Stage 2's lack of HTML-escaping safe. Four invariants, each with its enforcement point:

| Invariant | Enforced at | Why it matters |
|---|---|---|
| `html: false` on the `MarkdownIt` instance | `renderer.ts:67` | Disables the `html_block`/`html_inline` parser rules entirely. Any literal `<...>`-shaped text in the Markdown source — whether it leaked from Stage 2 or was typed by the user in the editor — is parsed as plain text and HTML-entity-escaped on output. **Never set this to `true`.** |
| Default `validateLink` (unmodified) | `renderer.ts` — no override exists; only `md.renderer.rules['image']` is overridden, and only for local-path resolution, not validation | markdown-it's own default link/image-URL validator rejects `javascript:`, `vbscript:`, `file:`, and `data:` (except a small allowlist of image MIME types) for both explicit `[text](url)`/`![alt](src)` syntax and `linkify`-autodetected bare URLs. This applies to Stage 2's unfiltered `href`/`src` passthrough. |
| Content-derived text must go through `md.utils.escapeHtml()` before being concatenated into an HTML string | `wikilink.ts:57`, `hashtag.ts:47` | The two custom markdown-it inline plugins render HTML directly (`<a class="wikilink">...`, `<a class="hashtag">...`) and therefore bypass `html: false` — it only governs the parser, not renderer rules a plugin defines itself. Both escape their label text. `data-hashtag`/`data-entry-id` attribute values are additionally safe by construction: the hashtag charset (`hashtag.ts:12`) cannot produce `"`/`<`/`>`, and `entryId` is a `number`, never attacker-controlled text. |
| `{@html}` may only render `createRenderer`'s own output | `viewer/[id]/+page.svelte:903` consumes `html` set at `:364`; `editor/[id]/+page.svelte:147` consumes `previewHtml` set at `:93`/`:128` — both assigned from `createRenderer(...)(source)` | These are the only two `{@html}` sites in the codebase (verified by grep). Never wire one to raw extractor content or any string that hasn't gone through `createRenderer`. |

**Scope note on the plugin-escaping invariant**: this table covers the sinks that were actually opened and checked this session — `wikilink.ts`, `hashtag.ts`, and the Shiki `style-to-class` transformer (`shiki-style-to-class.ts`, which operates only on Shiki's own already-generated HAST properties, never on extractor content, and never concatenates raw text into HTML — it computes a hash-derived class name). The third-party `markdown-it-footnote` and `markdown-it-task-lists` plugins were not independently audited; they are mainstream, widely-used plugins that render through markdown-it's own escaped token path rather than ad hoc string concatenation, which is why they weren't flagged as a priority — but that is an inference from their design, not a line-by-line read the way the two first-party plugins got.

**Verified empirically, 2026-07-16**, via a disposable Node script exercising the real `createRenderer` configuration (deleted after use, not part of the repo): `[x](javascript:alert(1))`, `![x](javascript:alert(1))`, `![x](data:text/html,<script>...)`, a raw `<script>...</script>` line, a raw `<img onerror=...>` line, a wikilink title/alias containing `<b onclick="...">`/`<img onerror=...>`, and a bare `javascript:alert(1)` autolink candidate — all eight rendered as inert escaped text or were rejected outright, never as live markup or a functioning link. This was confirmed against this repository's actual code, not asserted from general knowledge of markdown-it's defaults.

### Stage 4 — Local image path resolution (`electron/main/protocols.ts`)

A relative (non-`http`, non-`asset:`) `src` surviving Stage 2's verbatim passthrough gets resolved to an absolute local path (`renderer.ts`'s `resolveImagePath`, naive `..`-popping) and converted to an `analecta-file://` URL. Two invariants at the protocol handler close this off:

- **`assertVaultPath()`** (`protocols.ts:97`, implemented in `vault-state.ts:20`) — resolves the path and requires it equal the vault path or start with `vaultPath + path.sep`. The trailing separator is deliberate: without it, a sibling directory sharing the vault path as a string prefix (e.g. vault `/home/user/vault` vs. attacker path `/home/user/vault-evil/secret.png`) would bypass a naive `startsWith` check.
- **Extension allowlist** (`ALLOWED_IMAGE_EXTS`, `protocols.ts:7`) — only `.png .jpg .jpeg .gif .webp .svg .avif` are served; a crafted `src` pointing at `.md`, `.ttf`, or any non-image file gets HTTP 403 regardless of path.

Both are already documented as Electron Layer 4 in `docs/electron-shell-security.md`; restated here only to close the loop on where a Stage 2 `![alt](src)` value actually ends up.

### Backstop — Content Security Policy

`script-src` never includes `'unsafe-inline'` (`protocols.ts:57`, `docs/electron-shell-security.md` § Layer 5). Even in the hypothetical where one of the invariants above regressed and a `<script>` or `onerror=` handler reached the live DOM, the CSP would still block its execution. This makes the stack genuine defense-in-depth rather than `html: false` being a single point of failure — but it is a backstop, not a substitute for the invariants above: CSP does not stop content spoofing (fake headings, fake-but-inert links) or an `<img src="https://...">` fetch, since `img-src` permits `https:` by design (see Scope note above).

---

## Known Residual Gaps

Listed for honesty, not alarm — correctly scoped as a non-injection issue and tracked as separate work, not as part of this trust boundary:

- **Fenced code-block backtick breakout** (`converter.py:134`) — content-integrity/cosmetic, not security. See Stage 2 above.

---

## Maintaining These Invariants

When touching any file in the pipeline above:

1. **Never set `html: false` to `true`** in `renderer.ts`, and never add a `validateLink`/`normalizeLink` override that widens the default scheme allowlist.
2. **Any new markdown-it plugin that defines its own `renderer.rules[...]`** (i.e., emits an HTML string directly, the way `wikilink.ts`/`hashtag.ts` do) must run all content-derived text through `md.utils.escapeHtml()` before interpolating it into that string. This is the one place `html: false` provides no protection, because it governs the parser, not renderer rules a plugin defines itself.
3. **Any new `{@html}` site** must consume `createRenderer`'s output only, never a raw string derived from extracted content, entry titles, or user input.
4. **Any new local-file-serving protocol handler or IPC path** must call `assertVaultPath()` (or `assertExistsPath()` for existence-only checks) — see `docs/electron-shell-security.md` § Developer Guidelines, which already covers this in general.
5. **After bumping `markdownify` or adding a new HTML-to-Markdown normalization step in `converter.py`**, re-check that `<script>`/`<style>` are still dropped and that no new `convert_*` method reproduces raw source markup verbatim.

---

## Cross-References

- `docs/extraction.md` — how Stage 1's HTML is produced.
- `docs/electron-shell-security.md` § Layer 4 (Custom Protocol Handler Restrictions), § Layer 5 (Content Security Policy) — the Electron-side half of this trust boundary.
- `docs/wikilinks-and-hashtags.md` — the wikilink/hashtag inline-syntax design (charset choices, fenced-block exclusion); this document only covers their HTML-rendering safety, not their parsing design.
- `docs/syntax-highlighting.md` — Shiki rendering layer; the `style-to-class` transformer referenced above is documented there for CSP purposes, not sanitization.
