# CLAUDE.md — Analecta

> Loaded in addition to the global `~/.claude/CLAUDE.md`.
> Rules here override global rules where they conflict.

---

## Project

Local desktop app: URL → extraction → clean Markdown → local PKM vault.
Native bundle distribution for Linux (`.deb` / `.rpm` / `.AppImage`) via Electron.
Architecture: Electron shell (TypeScript) + Python sidecar (FastAPI) + SvelteKit frontend.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Shell | **Electron 42.x** (TypeScript + Chromium) |
| Frontend | **SvelteKit + TypeScript + Vite** |
| Icons | **@lucide/svelte** |
| Markdown render | **markdown-it** (client-side) |
| Editor | **CodeMirror 6** + `@uiw/codemirror-theme-tokyo-night` |
| Backend sidecar | **Python 3.14 · FastAPI · uvicorn** |
| Database | **SQLite** with FTS5. No ORM. No Alembic. |
| HTTP client | **httpx** (async). `requests` is forbidden. |
| Package managers | Python: **uv** · Node: **pnpm** · Toolchain: **mise** |
| Testing | **pytest** (backend) |
| IDE | **Zed** |
| Distribution | **Electron bundle only** — no PyPI, no `uv tool` |

Global default overrides: no PostgreSQL, no Docker, no PySide6, no PyQt6.

---

## Source Layout

```
analecta/
├── backend/                        # Python sidecar
│   ├── src/analecta/
│   │   ├── api/                    # FastAPI routes + deps + SSE bus
│   │   │   ├── deps.py
│   │   │   ├── events.py
│   │   │   └── routes/             # entries, search, tags, extract, config, system, pkm
│   │   ├── server.py               # FastAPI + uvicorn entrypoint
│   │   ├── config.py
│   │   ├── extraction/
│   │   ├── markdown/
│   │   ├── storage/
│   │   ├── migrations/
│   │   ├── pkm/
│   │   └── security/
│   ├── tests/
│   ├── pyproject.toml
│   ├── backend.spec                # PyInstaller
│   └── .python-version             # 3.14
├── frontend/                       # SvelteKit
│   ├── src/
│   │   ├── lib/api/                # typed HTTP client (client.ts)
│   │   ├── lib/stores/             # sidecar, sse, ui, tabs, toolbar, contextMenu
│   │   ├── lib/markdown/           # markdown-it config
│   │   ├── lib/platform/           # platform shim (window.electronAPI wrappers)
│   │   ├── lib/actions/            # Svelte actions: flash.ts, tooltip.ts
│   │   ├── lib/font.ts             # applyFont(): --font-family, accent, theme toggle
│   │   ├── lib/components/
│   │   │   ├── Sidebar.svelte      # Obsidian-style navigator (collapsible rail, expandable sections)
│   │   │   ├── RightSidebar.svelte # backlinks + local graph panel
│   │   │   ├── TitleBar.svelte
│   │   │   ├── SearchDialog.svelte # modal search (Ctrl+K)
│   │   │   ├── SearchInput.svelte
│   │   │   ├── ShortcutsDialog.svelte
│   │   │   ├── EntryList.svelte
│   │   │   ├── FilterBar.svelte
│   │   │   ├── SortBar.svelte
│   │   │   ├── MarkdownEditor.svelte
│   │   │   ├── SidecarLoadingScreen.svelte
│   │   │   ├── TagTree.svelte      # rendered inside Sidebar.svelte
│   │   │   ├── UpdateBanner.svelte
│   │   │   ├── ContextMenu.svelte
│   │   │   ├── ResizeHandles.svelte
│   │   │   ├── LocalGraph.svelte   # per-entry subgraph (Sigma.js)
│   │   │   └── VaultGraph.svelte   # full vault graph (Sigma.js + graphology)
│   │   └── routes/                 # +page.svelte, viewer/[id], editor/[id], settings, first-run
│   ├── static/fonts/               # JetBrains Mono, Bricolage Grotesque, Symbols Nerd Font + Symbols Nerd Font Mono bundled
│   └── package.json
├── electron/                       # Electron shell
│   ├── main/                       # index.ts, sidecar.ts, vault-state.ts, ipc.ts, protocols.ts, scraper.ts, tray.ts, updater.ts
│   ├── preload/                    # index.ts — contextBridge, ALLOWED_CHANNELS whitelist
│   ├── build-resources/            # icons/ (9 sizes: 16–1024) · tray-icon-dark.png · tray-icon-light.png
│   ├── package.json
│   └── tsconfig.json
├── binaries/                       # sidecar output — gitignored (PyInstaller --onedir)
├── scripts/
│   ├── build_sidecar.py            # PyInstaller build → binaries/
│   ├── check.sh                    # quality gate: Python + TS/Svelte + frontend scripts
│   ├── deps_update.py              # dependency update helper
│   ├── verify-provenance.py        # dependency provenance verification
│   ├── socket-audit.sh             # local Socket dependency scan via BSM
│   └── system_deps.sh
├── docs/
├── .github/workflows/              # ci.yml, release.yml
├── electron-builder.yml            # packaging: deb, rpm, AppImage
├── .mise.toml                      # Python 3.14 · Node LTS · pnpm latest
├── pnpm-workspace.yaml
├── package.json                    # root: electron:dev / electron:build / dist
└── CLAUDE.md
```

---

## Hybrid Architecture

### Layout rules

- Python business logic lives exclusively under `backend/src/analecta/`.
- Frontend lives under `frontend/`. No Python imports, no direct file I/O — use Electron IPC (`window.electronAPI`).
- Electron shell in `electron/` manages the window and sidecar lifecycle only.

### IPC channels

| Channel | Purpose |
|---------|---------|
| **stdin/stdout** | Sidecar lifecycle signals only: `LISTENING_ON_PORT:<n>`, `SIDECAR_READY` |
| **HTTP loopback (api)** | All data: frontend ↔ `GET/POST/PATCH/DELETE /api/v1/...` |
| **HTTP loopback (render)** | Tier 2 extraction: sidecar → `POST http://127.0.0.1:{ANALECTA_RENDER_PORT}/render` → Electron main (`scraper.ts`). Token auth via `X-Render-Token`. Returns Defuddle result or `outer_html` fallback. |
| **SSE** | Backend → frontend push via `GET /api/v1/system/events` |

### Sidecar lifecycle

1. Electron calls `startRenderServer()` → binds a random loopback port, generates `ANALECTA_RENDER_TOKEN`.
2. Electron spawns `binaries/analecta-sidecar` with `ANALECTA_RENDER_PORT` and `ANALECTA_RENDER_TOKEN` in env.
3. Sidecar binds a dynamic port (`socket.bind(("", 0))`), prints `LISTENING_ON_PORT:<n>` to stdout.
4. Electron parses stdout and caches the port. Does **not** emit `sidecar-ready` yet — uvicorn has not started accepting connections at this point (100–500 ms gap). Resolving here causes ECONNREFUSED on the first API call, silently skipping first-run checks.
5. Sidecar prints `SIDECAR_READY` once uvicorn is serving. Electron emits `sidecar-ready` IPC event to the renderer.
6. Frontend renders a loading screen until `sidecar-ready` is received (timeout: 10 s → error state).
7. On window close, Electron kills the sidecar child process (`SIGTERM`, 3 s SIGKILL fallback) and closes the render server.

### Sidecar packaging

- **PyInstaller `--onedir`** (not `--onefile` — PID/lifecycle issue documented in `docs/python-tauri.md` §6).
- Built by `scripts/build_sidecar.py`.
- Output path: `binaries/` (repo root).

### Frontend rules

- **Palette** (Tokyo Night): `bg=#1a1b26` · `fg=#d9e0f2` · default `accent=#e0af68` (yellow; selectable: red `#ff757f`, yellow, green, cyan). CSS variables only. Never hardcode hex — always use the CSS custom properties defined in `app.css`.
- **Fonts** (two-family split):
  - `--font-ui-family`: `'Bricolage Grotesque'` (variable font) — UI chrome. Fixed; never user-selectable. `font.ts` does not touch this.
  - `--font-family`: reading/content font — user-selectable. Variants: `'JetBrains Mono', 'Symbols Nerd Font Mono'` (default, `regular`) or `'Bricolage Grotesque', 'Symbols Nerd Font'` (`bricolage`). `font.ts` sets this variable, along with `--accent`/`--accent-dark` and `.theme-light`.
  - Bundled in `frontend/static/fonts/`: JetBrains Mono, Bricolage Grotesque, SymbolsNerdFont-Regular.ttf, SymbolsNerdFontMono-Regular.ttf. `@font-face` declarations in `app.css`. Symbols Nerd Font provides PUA glyph coverage as a silent fallback for both reading-font stacks.
  - Base font-size: **17px** (UI body via `--font-ui-size` CSS variable, set by `applyFont()`). Reading font default: **18px**, user-adjustable.
- **Icons**: `@lucide/svelte`. Import by PascalCase name (`import { Settings, SquareLibrary } from '@lucide/svelte'`). Always use the named exports — do not import raw SVG. Verify icon names against `node_modules/@lucide/svelte/dist/icons/index.d.ts`.
- **Sidebar**: Obsidian-style file-explorer navigator. Collapsible (44px rail / 260px full, `Ctrl+B`). Sections: library, unread, read, bookmark, gem, archive, Tags — each expandable with `ChevronRight`. Settings gear icon at bottom. Search opens via `ScanSearch` icon or `Ctrl+K`.
- **Markdown render**: `markdown-it` + plugins, client-side. No round-trips to the sidecar.
- **Editor**: CodeMirror 6 with `@uiw/codemirror-theme-tokyo-night`.
- **Sidecar bootstrap**: never render content before `sidecar-ready` event is received.
- All I/O via the typed `apiFetch()` wrapper or Electron IPC (`window.electronAPI.invoke`). Never block the main thread.

### Distribution

- **Electron bundle only**: `.deb`, `.rpm`, `.AppImage`. No PyPI, no `uv tool`.
- Updates via `electron-updater`. Signing key stored in **Bitwarden Secrets Manager** — injected at CI runtime by `bitwarden/sm-action`. `BWS_ACCESS_TOKEN` is the only GitHub Secret in the repo.
- CI release builds triggered by version tag (`v*`) via `.github/workflows/release.yml`.

### OS integration notes

- **Tray on GNOME/Wayland** (Pop!_OS 24.04): Electron tray uses `StatusNotifierItem` via Chromium. GNOME does not display these natively — requires the **AppIndicator and KStatusNotifierItem Support** GNOME extension. Document in README as a user-side dependency. KDE, i3, and Sway work out of the box.
- **Wayland native**: Analecta runs Wayland-native by default (no `--ozone-platform=x11`). `dialog.showOpenDialog()` has a **30 s** timeout + text-input fallback (FileChooser portal SIGSEGV on COSMIC — cosmic-epoch#3467; GNOME/Pop!_OS XDG portal takes 10–20 s — 8 s silently dropped the selection). `win.focus()` is best-effort on Wayland; always call `show()` first.

---

## Canonical Data Contracts

### `ExtractedContent` (extraction output, consumed by `api/routes/extract.py`)

```python
@dataclass
class ExtractedContent:
    title: str
    html: str
    url: str
    source_type: Literal["article", "youtube", "substack", "x"]
    metadata: dict[str, Any]
```

### SQLite Schema (authoritative)

```sql
CREATE TABLE entries (
    id          INTEGER PRIMARY KEY,
    title       TEXT    NOT NULL,
    url         TEXT    NOT NULL UNIQUE,
    file_path   TEXT    NOT NULL,
    source_type TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,  -- ISO 8601
    updated_at  TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'unread',
    tags_json   TEXT    NOT NULL DEFAULT '[]',
    flags_json  TEXT    NOT NULL DEFAULT '[]',  -- bookmark, gem, archive
    read_at     TEXT                            -- ISO 8601, NULL until first read
);
CREATE TABLE tags (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    count INTEGER DEFAULT 0
);
CREATE TABLE entry_tags (
    entry_id INTEGER REFERENCES entries(id),
    tag_id   INTEGER REFERENCES tags(id),
    PRIMARY KEY (entry_id, tag_id)
);
CREATE VIRTUAL TABLE entries_fts USING fts5(
    title, content, tokenize='unicode61'
);
CREATE TABLE backlink_refs (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    target_text TEXT    NOT NULL,
    is_hashtag  INTEGER NOT NULL DEFAULT 0,
    heading     TEXT,
    pre         TEXT    NOT NULL DEFAULT '',
    highlight   TEXT    NOT NULL DEFAULT '',
    post        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX idx_backlink_refs_source ON backlink_refs(source_id);
CREATE INDEX idx_backlink_refs_target ON backlink_refs(target_text);
```

Schema changes go in a new `backend/src/analecta/migrations/NNN_description.sql`. Never mutate existing migration files.

---

## Naming Conventions

| Artifact | Format |
|----------|--------|
| Vault Markdown file | `YYYY-MM-DD-{slug}.md` |
| Asset file | `{sha256[:16]}.{ext}` — content-addressed, not sequential |
| Asset directory | `{vault}/assets/{slug}/` |
| Logseq image embed | `![{original_name}](../assets/{slug}/{sha256[:16]}.{ext})` |
| Hashtag | `snake_case` · line-suffix only · never `##heading`-style |
| Migration | `backend/src/analecta/migrations/NNN_description.sql` (3-digit zero-padded) |
| Config file | `~/.config/analecta/config.toml` |
| Vault default | `~/.local/share/Analecta/vault/` |

---

## Security Constraints

- `analecta://` URL scheme handler: validate and sanitize the `id` parameter before any DB query. Treat it as untrusted input.
- Asset downloader: validate `Content-Type` header before writing. Reject non-image MIME types.
- playwright: headless only, no persistent profile, sandbox flag enabled.
- No `.env` files anywhere in the project tree. Configuration is TOML only.
- **CORS**: `CORSMiddleware` in `server.py` must include `"app://index.html"` in `allow_origins` for packaged builds — Electron's custom scheme sends this origin on CORS preflights. Without it, GETs succeed but POST/PATCH/PUT/DELETE silently return 400. The dev server is covered by `allow_origin_regex=r"http://localhost(:\d+)?"`. Do not add Tauri origins (`tauri://localhost`, `http://tauri.localhost`) — removed in E9.
- **CSP**: Never write `'unsafe-inline'` in `style-src`, `style-src-elem`, or `style-src-attr`. Use the `style:property` Svelte directive instead of inline `style=""` attributes (the latter compiles to `setAttribute('style', ...)`, blocked by `style-src-attr 'none'`). Third-party libs that emit inline styles need a HAST transformer to convert them to pre-generated CSS classes. See global skill `/electron-svelte-csp`.

---

## Distribution & Updater

Single distribution channel: **Electron bundle** (`.deb` / `.rpm` / `.AppImage`).

- Build: `mise exec -- pnpm dist` (runs `scripts/build_sidecar.py`, then `pnpm --filter frontend build`, then `electron-builder`).
- Updates: `electron-updater` with signed releases. Private key stored in BSM — injected via `sm-action`.
- PyPI / `uv tool` channel: **discontinued**. Do not implement or reference.

---

## Custom Commands

| Command | Behavior |
|---------|----------|
| `/fetch <url>` | Run extraction pipeline and print resulting Markdown to stdout |
| `/dev` | `cd backend && mise exec -- uv run python -m analecta` (sidecar standalone) |
| `/test` | `cd backend && mise exec -- uv run pytest -v` |
| `/build` | `mise exec -- pnpm dist` |

**Task workflow:** after completing each planned task, run `mise exec -- ./scripts/check.sh` to verify the quality gate, then suggest a conventional commit message.

---

## Secret Management

Single source of truth: **BSM** (Web App / `bws`).

### Policy for Claude Code

- Never generate, log, print, or hardcode secret values.
- The local `bws` CLI environment operates with a Read-only Machine Account. Therefore, do not attempt to run `bws secret create` automatically.
- When code requires a secret to exist, emit this exact warning block to the developer and halt execution:

```
⚠️  SECRET REQUIRED
    Name   : analecta/<secret_name>
    Purpose: <what it's used for>
    Action : 1. Open Bitwarden Secrets Manager Web App.
             2. Create a new secret with Key: "<secret_name>" in the active Project.
```

---

## Hard Constraints

- Do not implement Nitter integration. It is defunct. Mark X/Twitter extraction as `NotImplementedError` with a docstring note and move on.
- Do not make architectural or design decisions autonomously. Stop and ask.
- Do not use `requests`. Do not use PyQt6. Do not use PostgreSQL.
- Do not use `npm`. Use `pnpm` exclusively for all Node.js package management.
- Every direct npm dependency is pinned to an exact version (never a range). `.npmrc` enforces this via `save-exact=true`. Verify with `mise exec -- pnpm view <pkg>@<version> dist.integrity` before install, then cross-check against `pnpm-lock.yaml` after. See `docs/dependency-verification.md`.
- For CVE-driven patches of transitive npm deps, use `overrides:` in `pnpm-workspace.yaml` with exact versions, same SHA-512 verification. Do **not** use `pnpm.overrides` in `package.json` — that field is not tracked by pnpm's staleness check and will be silently ignored. Use scoped syntax (`'parent>dep': 'version'`) when the same package is pulled in at different majors by different consumers. See `docs/dependency-verification.md`.
- For CVE-driven patches of transitive Python deps, use `[tool.uv] constraint-dependencies` in `backend/pyproject.toml` with a version floor (e.g. `["starlette>=1.3.1"]`), then `uv lock` — no manual SHA step; `uv.lock` records and verifies hashes itself. See `docs/dependency-verification.md`.
- Do not read `~/.config/analecta/config.toml`.
- **Python tests**: every change to `backend/src/analecta/**` must include tests in `backend/tests/` in the same commit. A `check.sh` report showing 0% coverage on new code is a blocker — add tests before moving on. TypeScript and Electron code are excluded (manual QA only).
