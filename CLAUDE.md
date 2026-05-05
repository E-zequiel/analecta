# CLAUDE.md — Analecta

> Loaded in addition to the global `~/.claude/CLAUDE.md`.
> Rules here override global rules where they conflict.

---

## Project

Local desktop app: URL → extraction → clean Markdown → local PKM vault.
Native bundle distribution for Linux (`.deb` / `.rpm` / `.AppImage`) via Tauri 2.0.
Architecture: Tauri shell (Rust) + Python sidecar (FastAPI) + SvelteKit frontend.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Shell | **Tauri 2.0** (Rust + WebKitGTK) |
| Frontend | **SvelteKit + TypeScript + Vite** |
| Markdown render | **markdown-it** (client-side) |
| Editor | **CodeMirror 6** + `@uiw/codemirror-theme-tokyo-night` |
| Backend sidecar | **Python 3.13 · FastAPI · uvicorn** |
| Database | **SQLite** with FTS5. No ORM. No Alembic. |
| HTTP client | **httpx** (async). `requests` is forbidden. |
| Package managers | Python: **uv** · Node: **pnpm** · Toolchain: **mise** |
| Testing | **pytest** (backend) |
| Distribution | **Tauri bundle only** — no PyPI, no `uv tool` |

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
│   │   ├── security/
│   │   ├── ui/                     # PENDING DELETION — block G2
│   │   └── updater/                # PENDING DELETION — block G3
│   ├── tests/
│   ├── pyproject.toml
│   ├── backend.spec                # PyInstaller
│   └── .python-version             # 3.13
├── frontend/                       # SvelteKit
│   ├── src/
│   │   ├── lib/api/                # typed HTTP client
│   │   ├── lib/stores/
│   │   ├── lib/markdown/           # markdown-it config
│   │   ├── lib/components/
│   │   └── routes/                 # +page.svelte, viewer/[id], editor/[id], settings, first-run
│   ├── static/fonts/               # JetBrainsMono bundled
│   └── package.json
├── src-tauri/                      # Rust shell
│   ├── src/                        # lib.rs, main.rs, sidecar.rs, commands.rs
│   ├── capabilities/default.json
│   └── tauri.conf.json
├── scripts/
│   ├── build_sidecar.py            # PyInstaller + target-triple rename
│   ├── dev.py                      # sidecar standalone (without Tauri)
│   └── system_deps.sh
├── docs/
├── .github/workflows/              # ci.yml, release.yml
├── .mise.toml                      # Python 3.13 · Node LTS · Rust stable · pnpm latest
├── pnpm-workspace.yaml
├── package.json                    # root: tauri dev / tauri build
└── CLAUDE.md
```

---

## Hybrid Architecture

### Layout rules

- Python business logic lives exclusively under `backend/src/analecta/`.
- Frontend lives under `frontend/`. No Python imports, no direct file I/O — use Tauri plugins.
- Rust shell in `src-tauri/` manages the window and sidecar lifecycle only.

### IPC channels

| Channel | Purpose |
|---------|---------|
| **stdin/stdout** | Sidecar lifecycle signals only: `LISTENING_ON_PORT:<n>`, `SIDECAR_READY` |
| **HTTP loopback** | All data: frontend ↔ `GET/POST/PATCH/DELETE /api/v1/...` |
| **SSE** | Backend → frontend push via `GET /api/v1/system/events` |

### Sidecar lifecycle

1. Tauri spawns `binaries/analecta-sidecar` on app start.
2. Sidecar binds a dynamic port (`socket.bind(("", 0))`), prints `LISTENING_ON_PORT:<n>` to stdout.
3. Tauri captures the port and emits `sidecar-ready` event to the frontend.
4. Frontend renders a loading screen until `sidecar-ready` is received (timeout: 10 s → error state).
5. On window close, Tauri kills the sidecar child process.

### Sidecar packaging

- **PyInstaller `--onedir`** (not `--onefile` — PID/lifecycle issue documented in `docs/python-tauri.md` §6).
- Built by `scripts/build_sidecar.py`; output binary renamed with target-triple suffix for Tauri.
- Output path: `src-tauri/binaries/` (gitignored).

### Frontend rules

- **Palette** (Tokyo Night): `bg=#1a1b26` · `fg=#c0caf5` · `accent=#7aa2f7`. CSS variables only.
- **Font**: JetBrains Mono. Bundled in `frontend/static/fonts/`. `@font-face` in `app.css`.
- **Markdown render**: `markdown-it` + plugins, client-side. No round-trips to the sidecar.
- **Editor**: CodeMirror 6 with `@uiw/codemirror-theme-tokyo-night`.
- **Sidecar bootstrap**: never render content before `sidecar-ready` event is received.
- All I/O via the typed `apiFetch()` wrapper or Tauri plugins. Never block the main thread.

### Distribution

- **Tauri bundle only**: `.deb`, `.rpm`, `.AppImage`. No PyPI, no `uv tool`.
- Updates via `tauri-plugin-updater`. Private key stored as GitHub secret `TAURI_SIGNING_PRIVATE_KEY`.
- CI release builds triggered by version tag (`v*`) via `.github/workflows/release.yml`.

### OS integration notes

- **Tray on GNOME/Wayland** (Pop!_OS 24.04): `tauri-plugin-tray` uses `StatusNotifierItem`. GNOME does not display these natively — requires the **AppIndicator and KStatusNotifierItem Support** GNOME extension. Document in README as a user-side dependency. KDE, i3, and Sway work out of the box.

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

Single distribution channel: **Tauri bundle** (`.deb` / `.rpm` / `.AppImage`).

- Build: `mise exec -- pnpm tauri build` (runs `scripts/build_sidecar.py` via `beforeBuildCommand`).
- Updates: `tauri-plugin-updater` with signed releases. Private key stored as GitHub secret `TAURI_SIGNING_PRIVATE_KEY`.
- PyPI / `uv tool` channel: **discontinued**. Do not implement or reference.

---

## Custom Commands

| Command | Behavior |
|---------|----------|
| `/fetch <url>` | Run extraction pipeline and print resulting Markdown to stdout |
| `/dev` | `cd backend && mise exec -- uv run python -m analecta` (sidecar standalone) |
| `/test` | `cd backend && mise exec -- uv run pytest -v` |
| `/build` | `mise exec -- pnpm tauri build` |

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
- `backend/src/analecta/ui/` is pending deletion (block G2). Do not add code there.
- `backend/src/analecta/updater/` is pending deletion (block G3). Do not add code there.
