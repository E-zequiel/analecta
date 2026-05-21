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
│   │   │   └── routes/             # entries, search, tags, extract, config, security, system, pkm
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
│   │   ├── lib/api/                # typed HTTP client
│   │   ├── lib/stores/             # sidecar, sse, ui (selectedTag, sidebarCollapsed, libraryOpen,
│   │   │                           #   expandedSections, activeSection, searchOpen)
│   │   ├── lib/markdown/           # markdown-it config
│   │   ├── lib/platform/           # platform shim (window.electronAPI wrappers)
│   │   ├── lib/components/
│   │   │   ├── Sidebar.svelte      # Obsidian-style navigator (collapsible rail, expandable sections)
│   │   │   ├── SearchDialog.svelte # modal search (Ctrl+K)
│   │   │   ├── EntryList.svelte
│   │   │   ├── MarkdownEditor.svelte
│   │   │   ├── SidecarLoadingScreen.svelte
│   │   │   ├── TagTree.svelte      # rendered inside Sidebar.svelte
│   │   │   └── UpdateBanner.svelte
│   │   └── routes/                 # +page.svelte, viewer/[id], editor/[id], settings, first-run
│   ├── static/fonts/               # JetBrainsMono bundled
│   └── package.json
├── electron/                       # Electron shell
│   ├── main/                       # index.ts, sidecar.ts, vault-state.ts, ipc.ts, protocols.ts, tray.ts, updater.ts
│   ├── preload/                    # index.ts — contextBridge, ALLOWED_CHANNELS whitelist
│   ├── build-resources/            # icons/ (7 sizes + tray)
│   ├── package.json
│   └── tsconfig.json
├── binaries/                       # sidecar output — gitignored (PyInstaller --onedir)
├── scripts/
│   ├── build_sidecar.py            # PyInstaller build → binaries/
│   ├── dev.py                      # sidecar standalone
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
| **HTTP loopback** | All data: frontend ↔ `GET/POST/PATCH/DELETE /api/v1/...` |
| **SSE** | Backend → frontend push via `GET /api/v1/system/events` |

### Sidecar lifecycle

1. Electron spawns `binaries/analecta-sidecar` on app start (`child_process.spawn`).
2. Sidecar binds a dynamic port (`socket.bind(("", 0))`), prints `LISTENING_ON_PORT:<n>` to stdout.
3. Electron parses stdout, caches the port, and emits `sidecar-ready` IPC event to the renderer.
4. Frontend renders a loading screen until `sidecar-ready` is received (timeout: 10 s → error state).
5. On window close, Electron kills the sidecar child process (`SIGTERM`, 3 s SIGKILL fallback).

### Sidecar packaging

- **PyInstaller `--onedir`** (not `--onefile` — PID/lifecycle issue documented in `docs/python-tauri.md` §6).
- Built by `scripts/build_sidecar.py`.
- Output path: `binaries/` (repo root).

### Frontend rules

- **Palette** (Tokyo Night): `bg=#1a1b26` · `fg=#c0caf5` · `accent=#ff757f` (red). CSS variables only. Never hardcode hex — always use the CSS custom properties defined in `app.css`.
- **Font**: JetBrains Mono. Bundled in `frontend/static/fonts/`. `@font-face` in `app.css`. Base font-size: **16.33px**.
- **Icons**: `@lucide/svelte`. Import by PascalCase name (`import { Settings, SquareLibrary } from '@lucide/svelte'`). Always use the named exports — do not import raw SVG. Verify icon names against `node_modules/@lucide/svelte/dist/icons/index.d.ts`.
- **Sidebar**: Obsidian-style file-explorer navigator. Collapsible (44px rail / 260px full, `Ctrl+B`). Sections: all, unread, read, favorite, recommend, Tags — each expandable with `ChevronRight`. Settings gear icon at bottom. Search opens via `ScanSearch` icon or `Ctrl+K`.
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
- **Wayland native**: Analecta runs Wayland-native by default (no `--ozone-platform=x11`). `dialog.showOpenDialog()` has an 8 s timeout + text-input fallback (FileChooser portal SIGSEGV on COSMIC — cosmic-epoch#3467). `win.focus()` is best-effort on Wayland; always call `show()` first.

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
    tags_json   TEXT    NOT NULL DEFAULT '[]'
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

- VirusTotal API key: stored in system keyring via `keyring` library exclusively. Never in `config.toml`, never in env vars, never logged.
- VirusTotal Public API rate limits: **4 requests/min · 500 requests/day** (per ToS). The polling loop in `security/virustotal.py` must enforce a minimum of **15 s between consecutive API calls**. Exceeding limits causes permanent account ban.
- VirusTotal privacy: every URL submitted to VT is indexed in their public database. The Settings UI **must display a one-time opt-in disclaimer** before the first scan. Never submit URLs silently.
- VirusTotal ToS: Public API is non-commercial only. The app must remain free and open-source. Any monetisation path requires a Premium API licence.
- `analecta://` URL scheme handler: validate and sanitize the `id` parameter before any DB query. Treat it as untrusted input.
- Asset downloader: validate `Content-Type` header before writing. Reject non-image MIME types.
- playwright: headless only, no persistent profile, sandbox flag enabled.
- No `.env` files anywhere in the project tree. Configuration is TOML only.
- Frontend never accesses the keyring directly. API key management goes through `PUT /api/v1/security/virustotal/key`.

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

---

## Secret Management

Two-layer model:
- **BSM** (Web App / `bws`): Developer-level single source of truth.
- **System keyring** (`keyring` library): Runtime user-level. The app reads secrets from here at runtime.

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
             3. (Optional) If testing locally without BSM injection, add to local keyring via Python:
                `import keyring; keyring.set_password("analecta", "<secret_name>", "<VALUE>")`
    Runtime: App reads via `keyring.get_password("analecta", "<secret_name>")`
```

### Known secrets

| Secret | BSM Key | Runtime storage |
|--------|---------|-----------------|
| VirusTotal API key | `VIRUSTOTAL_API_KEY` | `keyring.get_password("analecta", "VIRUSTOTAL_API_KEY")` |

---

## Hard Constraints

- Do not implement Nitter integration. It is defunct. Mark X/Twitter extraction as `NotImplementedError` with a docstring note and move on.
- Do not make architectural or design decisions autonomously. Stop and ask.
- Do not use `requests`. Do not use PyQt6. Do not use PostgreSQL.
- Do not use `npm`. Use `pnpm` exclusively for all Node.js package management.
- Do not read `~/.config/analecta/config.toml` or any file matching the global deny rules in `~/.claude/settings.json`.
