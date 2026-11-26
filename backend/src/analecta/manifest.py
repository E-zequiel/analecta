"""Generated vault-root ``AGENTS.md`` manifest (owner decision D4).

The manifest is the vault's contract with external agents: it documents the
Analecta-specific grammar and fields that live beyond the OKF surface so a
conforming agent can read (and, via the CLI, write) without guessing. It is
**generated content** in perpetual voice — rewritten on every sidecar
startup and CLI vault run, never hand-maintained.
"""

from __future__ import annotations

from pathlib import Path

MANIFEST_FILE_NAME = "AGENTS.md"

_BODY = """\
# Analecta vault — agent manifest

This vault is an Analecta PKM vault that also conforms, by extension, to
Google's Open Knowledge Format (OKF) v0.2: every non-reserved `.md` under
`pages/` is a concept with YAML frontmatter and a required `type` field.
Unknown keys must be preserved and tolerated (OKF conformance). Reserved
files `index.md` / `log.md` are **not** materialized: the equivalent views
are computed on the fly by `analecta index` and `analecta log`.

## Concept types

- `type: reference` — a faithful capture of an external source (the only
  type written by the extraction pipeline).
- `type: synthesis` — a bot-produced synthesis citing its captured sources
  via `sources[]` (resource = the capture's vault-relative page path).

## Linking grammar

- `[[Wikilink]]` — resolution is by **entry title, case-insensitive**. An
  alias (`[[Title|alias]]`) changes the display, not the resolution.
  Unresolved wikilinks are tolerated (phantom titles).
- `#hashtag` — inline topic tags. Identity is the raw text `casefold()`-ed
  **only**: accent- and symbol-sensitive (`café` != `cafe`,
  `well-being` != `well_being`). Must start with a letter; may contain
  letters, digits, `_`, `'`, `~`, `^`, `-`. Not recognized inside inline
  code, fenced code, or as a heading's own `#` marker.
- `linked:` (frontmatter list) — manual connections; once indexed they are
  indistinguishable from typed `[[wikilinks]]`.
- Standard Markdown links between concepts are **not** part of this vault's
  grammar: cross-entry edges are wikilinks, `#hashtags`, and `linked:`.

## Frontmatter beyond OKF

| Key | Meaning |
| --- | --- |
| `source_type` | DB-level origin: `article`, `substack`, `youtube`, `x` for
  captures; `synthesis` for synthesis concepts. |
| `read_state` | The person's reading state: `unread` / `read`. Distinct
  from OKF's `status: draft|stable|deprecated`, which agents may use on
  their own synthesis concepts. |
| `created_at` | Creation timestamp (ISO 8601). |
| `low_confidence` | Extraction quality flag from the pipeline. |
| `summary` | Short bot-written summary, <= 500 characters. |
| `summary_basis` | `sha256[:16]` of the post-frontmatter body bytes
  (UTF-8, as stored). Freshness rule (D9): recompute the hash over the
  current body; if it differs, the `summary` is stale — regenerate, don't
  trust. A synthesis regeneration clears `summary`/`summary_basis`
  entirely: summaries must be re-recorded against the new body. |
| `generated` | Producer block `{by, at}`: the OKF actor id of the bot
  that produced the entry (`<producer>/<version>` or `process:<id>`) and
  the production timestamp. `by: ""` is the documented unknown-actor
  sentinel (actor not recorded at production time). |
| `sources_basis` | Synthesis entries only: one `{resource, basis}` per
  cited capture, `basis` = `sha256[:16]` of that capture's
  post-frontmatter body at synthesis time. A synthesis is stale when a
  cited source's current body hash differs, its file is missing, or a
  cited `sources[]` entry has no matching `sources_basis` record
  (unverifiable). `analecta synthesize --stale` reports stale
  syntheses; `analecta index` marks them. |

## Synthesis update convention

- `analecta synthesize --update <ref>` regenerates a synthesis **in
  place**: the page path, slug, URL, database row id, and title never
  change (the title derives the slug, so `--title` is rejected as a
  conflict). The body, `sources[]`, and `sources_basis[]` are rewritten
  together in `--source` order; `generated.at` is bumped and
  `generated.by` is replaced only when `--by` is given.
- Creation and update both carry a valid `generated.by`; when a page's
  frontmatter lacked the key, update records the `""` sentinel.
- Regeneration clears `summary`/`summary_basis` (see the table above)
  and refreshes the entry's `updated_at`.

`description` is the **publisher's** meta description captured from the
source; `summary` is **bot-generated** analysis. Never confuse or merge
them.

## Deprecation crash window

A synthesis whose cited capture was deleted carries `status: deprecated`
in its page frontmatter. The frontmatter is the truth of deprecation:
the derived SQLite `deprecated` column is re-derived from the page by
the reconcile pass. If a deprecation rewrite fails or is interrupted,
the synthesis stays unmarked, and reconcile cannot infer the
deprecation after the fact — only a successful page rewrite changes
this state.

## Flags (human decisions — bots read, never write)

- `bookmark` — the person explicitly saved this entry.
- `gem` — the person marked this entry as high-value.
- `archive` — the entry is out of the active rotation (still referenced,
  not deleted).

Flags are personal curation signals. Agents MUST treat them as read-only:
calibrate importance from them, never set them. Any write must go through
the app or the `analecta` CLI — direct file edits by bots are forbidden.

## Single writer

The vault has exactly one writer at a time: an OS-level `flock` on
`.analecta.lock` is held by whichever process owns writes (the desktop
app's sidecar, or a standalone CLI run). If the lock is held, do not race
it — wait, or serve the user.

## Flags in `backlink_refs` — for consumers

The SQLite sidecar database (`analecta.db`) is a derived, reconcilable
index (FTS5 search, backlink graph, read states). It is Analecta's own
mechanism: agents should prefer the CLI (`analecta index`, `analecta log`,
`analecta extract`, `analecta summary`, `analecta synthesize`) over touching
the database directly.
"""


def write_vault_manifest(vault_path: Path) -> str:
    """Write the generated ``AGENTS.md`` manifest at the vault root.

    Regenerates on every call (perpetual voice — stale prose cannot
    survive). Safe to run on every sidecar startup and CLI vault run.

    Args:
        vault_path: Root directory of the local vault.

    Returns:
        The written manifest text.
    """
    vault_path.mkdir(parents=True, exist_ok=True)
    path = vault_path / MANIFEST_FILE_NAME
    path.write_text(_BODY, encoding="utf-8")
    return _BODY
