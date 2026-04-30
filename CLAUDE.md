# CLAUDE.md — Analecta

> Loaded in addition to the global `~/.claude/CLAUDE.md`.
> Rules here override global rules where they conflict.

---

## Project

Local desktop app: URL → extraction → clean Markdown → local PKM vault.
Functional priority: extraction + storage pipeline **before** any UI module.

---

## Stack Overrides

| Global default | Analecta override |
|----------------|-------------------|
| PostgreSQL | **SQLite** with FTS5. No ORM. No Alembic. |
| Docker | Not used. Local-only app. |
| — | **PySide6** (LGPL). PyQt6 is forbidden (licensing). |
| — | **httpx** (async). `requests` is forbidden. |

Full stack: Python 3 · PySide6 · SQLite/FTS5 · uv · mise · pytest · httpx · trafilatura · markdownify · markdown-it-py · Pydantic · playwright (optional, JS-heavy sites only).

---

## Source Layout

```
src/analecta/
├── __init__.py
├── __main__.py
├── config.py
├── extraction/
│   ├── core.py          # SourceExtractor base + type detection
│   ├── article.py
│   ├── youtube.py
│   ├── social.py
│   └── assets.py
├── markdown/
│   ├── converter.py
│   ├── frontmatter.py
│   └── hashtags.py
├── storage/
│   ├── vault.py
│   └── index.py
├── pkm/
│   ├── tags.py
│   ├── templates.py
│   └── url_scheme.py
├── security/
│   └── virustotal.py
├── updater/
│   └── checker.py
└── ui/
    ├── theme.py
    ├── fonts.py
    ├── main_window.py
    ├── dashboard.py
    ├── viewer.py
    ├── editor.py
    ├── settings.py
    └── tray.py
migrations/
    001_init.sql
    ...
tests/
.claude/commands/
    fetch.md · dev.md · test.md · build.md
```

---

## Module Development Order

```
M1 → M5 → M2 → M3 → M4 → M6 → M7 → M8 → M9 → M10 → M11 → M12 → M13 → M14
```

**Do not touch M8+ until M1–M6 pass their tests.**

---

## Canonical Data Contracts

### `ExtractedContent` (M2 output, consumed by M3 + M4)

```python
@dataclass
class ExtractedContent:
    title: str
    html: str
    url: str
    source_type: Literal["article", "youtube", "substack", "x"]
    metadata: dict[str, Any]
```

### SQLite Schema (M5 — authoritative)

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

Schema changes go in a new `migrations/NNN_description.sql` file. Never mutate existing migration files.

---

## Naming Conventions

| Artifact | Format |
|----------|--------|
| Vault Markdown file | `YYYY-MM-DD-{slug}.md` |
| Asset file | `{sha256[:16]}.{ext}` — content-addressed, not sequential |
| Asset directory | `{vault}/assets/{slug}/` |
| Logseq image embed | `![{original_name}](../assets/{slug}/{sha256[:16]}.{ext})` |
| Hashtag | `snake_case` · line-suffix only · never `##heading`-style |
| Migration | `migrations/NNN_description.sql` (3-digit zero-padded) |
| Config file | `~/.config/analecta/config.toml` |
| Vault default | `~/.local/share/analecta/vault/` |

---

## UI Rules (PySide6)

- **Palette** (Tokyo Night): `bg=#1a1b26` · `fg=#c0caf5` · `accent=#7aa2f7`. Implement in QSS only.
- **Font**: JetBrains Mono. Load via `QFontDatabase.addApplicationFont()`. Bundle the `.ttf`.
- **Root layout**: `QSplitter(sidebar | QStackedWidget)`.
- **Threading**: never block the main thread. Use `QThread` or `asyncio` + `qasync` for all I/O.
- **Markdown render**: `markdown-it-py` → HTML string → `QWebEngineView`. Do not use `QTextEdit` for rendering.

---

## Security Constraints

- VirusTotal API key: stored in system keyring via `keyring` library exclusively. Never in `config.toml`, never in env vars, never logged.
- `analecta://` URL scheme handler: validate and sanitize the `id` parameter before any DB query. Treat it as untrusted input.
- Asset downloader: validate `Content-Type` header before writing. Reject non-image MIME types.
- playwright: headless only, no persistent profile, sandbox flag enabled.
- No `.env` files anywhere in the project tree. Configuration is TOML only.

---

## Distribution & Updater (M14)

Two separate distribution channels exist. The updater **must not conflate them**:

| Channel | Build command | Update mechanism |
|---------|---------------|------------------|
| PyPI / `uv tool` | `uv build` | `subprocess.run(["uv", "tool", "upgrade", "analecta"])` |
| Standalone binary | PyInstaller | **Not yet defined — do not implement. Stop and ask.** |

Do not implement a standalone update path until explicitly instructed.

---

## Custom Commands

| Command | Behavior |
|---------|----------|
| `/fetch <url>` | Run M2 + M3 + M4 pipeline and print resulting Markdown to stdout |
| `/dev` | `mise exec -- uv run python -m analecta --dev --vault /tmp/analecta-dev-vault` |
| `/test` | `mise exec -- uv run pytest -v` (config centralizada en `pyproject.toml`) |
| `/build` | `mise exec -- uv build` (wheel + sdist). Runs smoke tests before PyInstaller step. |

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
| VirusTotal API key | `virustotal_api_key` | `keyring.get_password("analecta", "virustotal_api_key")` |

---

## Hard Constraints

- Do not implement Nitter integration. It is defunct. Mark X/Twitter extraction as `NotImplementedError` with a docstring note and move on.
- Do not make architectural or design decisions autonomously. Stop and ask.
- Do not use `requests`. Do not use PyQt6. Do not use PostgreSQL.
- Do not read `~/.config/analecta/config.toml` or any file matching the global deny rules in `~/.claude/settings.json`.
