# Wikilinks and Hashtags — Analecta

> Explains the `[[wikilink]]` and `#hashtag` grammar, how references are parsed and
> indexed, how the two tag systems relate to each other, and how both render and
> behave in the reading view.

---

## Overview

Analecta supports two inline linking mechanisms inside an entry's Markdown body:

- **`[[Wikilinks]]`** — references to another entry, by title.
- **`#Hashtags`** — inline topic tags, written directly in the article text.

Both are parsed out of the Markdown body and indexed into a single `backlink_refs`
table, which powers the BACKLINKS/Connections panel and the vault graph. They are
distinct from **structural tags** — the curated, CRUD-managed tags in the Sidebar's
TAGS section (`tags`/`entry_tags` tables) — but the two tag concepts (structural tags
and content hashtags) are unified for counting and lookup; see
[Structural tags vs. content hashtags](#structural-tags-vs-content-hashtags) below.

## Syntax

### Wikilinks

```
[[Title]]
[[Title|Alias]]
```

- `Title` is matched against real entry titles, case-insensitively.
- With `|Alias`, the alias is displayed instead of the title, but the title is still
  what's resolved against.
- A wikilink whose title matches no entry is **unresolved** — it still renders, but
  styled as inert (see [Rendering](#rendering-frontend)).

### Hashtags

```
#tag
#some_topic
```

- Body: `[A-Za-z][A-Za-z0-9_]*` — must start with a letter.
- Must not be preceded by a non-whitespace character — `foo#bar` and a `#fragment`
  inside a bare URL (`https://example.com/path#section`) are never treated as
  hashtags.
- Stored under a normalized identity (see
  [Normalization](#normalization-tag-identity) below), not the raw typed form.

### What's excluded

Neither a wikilink nor a hashtag is recognized:

- Inside an inline code span (`` `like this` ``).
- Inside a fenced code block (`` ``` ``).
- As the `#`/`##`/... marker of a Markdown heading line (`# My Heading`). A `#tag`
  appearing *inside* heading text, e.g. `## My favorite #topic`, still resolves
  normally — only the heading's own leading `#`s are excluded.

## Parsing & indexing (backend)

`backend/src/analecta/markdown/backlinks.py`'s `parse_refs(markdown)` is the single
entry point. Per line, it:

1. Masks inline-code spans (blanks them out, preserving character offsets) so nothing
   inside backticks is ever matched.
2. Tracks whether it's inside a fenced code block and skips those lines entirely.
3. Tracks the current heading (for context in the BACKLINKS panel). Only the
   heading line's own `#`/`##`/... marker is excluded — the heading's text is
   parsed like any other line, tagged with the heading it just opened.
4. Matches `[[wikilinks]]` and `#hashtags` against the remaining text.

It also reads a `linked:` list from the entry's YAML frontmatter, if present, and
emits one reference per title exactly as if it were a `[[wikilink]]`. This is how the
manual "connect entries" feature (search-to-connect dialog, Cable icon) participates
in the same backlink graph as inline wikilinks — a frontmatter-declared connection and
a typed `[[wikilink]]` are indistinguishable once indexed.

Every reference captures: the matched text, whether it's a hashtag, the enclosing
heading (a ref inside a heading line belongs to that same heading), and up to 60
characters of surrounding context on each side (used
for the BACKLINKS panel's preview snippet).

`VaultIndex.index_backlinks(entry_id)` re-reads the entry's file, calls `parse_refs`,
and replaces that entry's rows in `backlink_refs`. It runs whenever an entry is saved
or re-extracted.

### Normalization (tag identity)

`normalize_tag()` (`backend/src/analecta/markdown/hashtags.py`) converts a hashtag's
raw text to its storage identity: Unicode NFKD → ASCII → lowercase → non-alphanumeric
runs collapsed to a single underscore → leading/trailing underscores stripped. This is
what's written to `backlink_refs.target_text` for hashtags, and what
`get_entry_ids_by_tag`/`get_graph`/etc. key hashtag lookups on.

## Rendering (frontend)

Wikilinks and hashtags render in the reading view via two native `markdown-it` inline
rules, registered in `frontend/src/lib/markdown/renderer.ts`:

- `frontend/src/lib/markdown/wikilink.ts`
- `frontend/src/lib/markdown/hashtag.ts`

Being real tokenizer rules (not regexes run over the raw string), inline-code and
fenced-block exclusion come for free — those spans are already claimed by
markdown-it's own code/fence rules before either inline rule ever runs.

Resolution happens client-side against `entryTitleIndex`
(`frontend/src/lib/stores/entryTitles.ts`), a `lowercase title → entry id` map fetched
from `GET /entries/titles` and refreshed whenever an entry is added or changed.

| Element | Resolved | Unresolved |
|---|---|---|
| Wikilink | `<a class="wikilink" data-entry-id="N">` | `<span class="wikilink-unresolved">` (styled but inert — dashed underline, muted color, no click) |
| Hashtag | `<a class="hashtag" data-hashtag="{normalized}">#{raw}</a>` | — (hashtags have no unresolved state; they always render as a link) |

CSS lives in `frontend/src/lib/markdown/tokyo-night.css` (`.wikilink`,
`.wikilink-unresolved`, `.hashtag`).

## Structural tags vs. content hashtags

Analecta has two ways to attach a tag identity to an entry:

- **Structural tags** — created and managed from the Sidebar's TAGS section (inline
  create/rename/delete), stored in `tags`/`entry_tags`.
- **Content hashtags** — `#hashtag` mentions written directly in an entry's body,
  stored in `backlink_refs` (`is_hashtag = 1`).

They are deliberately separate systems (a structural tag has no required body text;
a content hashtag needs no Sidebar entry), but share one **normalized identity**:
`tags.normalized` is a `casefold()` of the structural tag's display name, and content
hashtags are already normalized at index time. An entry carrying `Python` structurally
and `#python` in its body is the same tag for every counting/lookup purpose:

- `VaultIndex.list_tags()` (Sidebar TAGS grid, tag counts) — a **true union**: every
  entry that has the tag either structurally or as a content hashtag, deduplicated by
  entry id.
- `VaultIndex.get_entry_ids_by_tag(tag)` (clicking into a tag, filtering by tag) —
  same true-union semantics.
- `create_tag`/`rename_tag`/`delete_tag` — all look up by normalized identity, so
  creating "python" when "Python" already exists resolves to the existing tag instead
  of creating a case-duplicate.

When both a structural tag and a content hashtag share an identity, the **structural
tag's display casing wins** wherever a single name is shown (grid, connection groups).
A normalized identity that strips symbols means a structural tag like `C++` and a
hashtag `#c` are never accidentally unified — `normalize_tag("C++")` and `casefold`
identity diverge enough that they stay distinct tags.

### Deleting a tag neutralizes literal body text too

The literal `#hashtag` text in an article's body is otherwise never rewritten (see
above), which used to leave a resurrection hole on delete: if any entry's Markdown
still contained `#tag` in its body, the next `index_backlinks()` re-index would bring
the tag right back (lowercase, split from whatever casing the deleted structural tag
had). `delete_tag` closes this by wrapping every live `#hashtag` occurrence sharing the
deleted tag's identity in backticks (`` `#tag` ``) — inline code is already excluded
from parsing (see `_mask_inline_code` above), so the reference stops being indexed on
the next re-index. The literal characters are preserved — readers still see `#python`
in the text, now styled as inline code instead of a live tag — only the markup around
it changes. This runs even inside heading text (the heading-embedded-hashtag case
`## My favorite #topic` parses as live, so it must be neutralized too, even though the
result — inline code nested in a heading — renders unusually). It also applies to a
tag with no structural row at all (a purely content-hashtag identity shown in the
Sidebar's TAGS grid) — deleting it neutralizes every occurrence the same way. A
structural tag whose name has no valid hashtag form (symbols/spaces, e.g. `C++`) never
touches any body text on delete — see `get_body_hashtag_entry_ids`'s collision-avoidance
rule above.

### Renaming a tag migrates literal body text too

The same resurrection risk exists on rename: if a renamed tag also appears as literal
`#hashtag` text in an entry's body, leaving that text alone would let the old
(lowercase) identity reappear in `list_tags()` as a separate tag after the rename —
a previously-unified tag splitting into two. Unlike delete, rename must preserve the
body text's *continuity* with the tag rather than sever it, so `rename_tag` rewrites
every live `#old_name` occurrence sharing the renamed identity to `#new_name` in
place (`rename_hashtag_occurrences`), then re-indexes the affected entries. The
literal text changes here — unlike the neutralize case, where only the surrounding
markup changes — because the goal is migration, not severance. This also applies to
a tag with no structural row at all (a purely content-hashtag identity): renaming it
rewrites every occurrence the same way, with no structural table involved.

`new_name` can only be migrated into body text if it's itself a valid bare hashtag
token (a leading letter, then letters/digits/underscores — no symbols or spaces). If
literal occurrences of the old identity exist in any entry's body and `new_name`
doesn't qualify (e.g. renaming into `C++`), the rename is rejected outright (`409`)
instead of silently leaving a split behind — edit the body text manually first, then
retry the rename. A rename with no literal body-text occurrences at all is unaffected
by this restriction, regardless of what `new_name` is.

Renaming a content-only tag (no structural row) into an identity that already exists
*structurally* merges rather than conflicts — the mirror of the already-allowed
"structural tag renamed into a content-only identity" case above. A content-only
identity has no `entry_tags` row to reconcile, so there's nothing to conflict.

### Merging two structural tags

Renaming a structural tag into a name that already belongs to *another* structural
tag is a merge — two curated tags collapsing into one — and is irreversible: once
merged, there's no record of which entries originally carried which tag. Because of
that, it's blocked by default (`409`, "already exists"); the caller must pass
`merge: true` explicitly to proceed. The Sidebar's inline rename detects the
collision client-side (against the already-loaded tag list) and shows a confirm/cancel
row instead of committing on Enter, mirroring the delete-confirmation UX — a typo into
an existing tag name must not silently collapse two categories.

When the merge is confirmed, `rename_tag` reassigns every entry from the old tag's
`entry_tags` row to the destination's (`INSERT OR IGNORE`, so an entry that already
carried both tags doesn't collide), deletes the old `tags` row, and rewrites
`tags_json` with the old name replaced by the destination's name — de-duplicated, so
an entry that had both ends up listing the destination once, not twice.

**The destination's preexisting display casing always wins**, everywhere the merge
writes something — the structural row, `tags_json`, and any freshly-migrated body-text
`#hashtag` occurrences all end up spelled exactly as the destination tag already was,
not however the new name was typed into the rename input. This matches the
sticky-first-seen convention used everywhere else in the tag system, and avoids a
merge silently re-casing an already-established tag vault-wide. If literal body-text
occurrences of the old identity exist and the destination's name isn't a valid bare
hashtag token (e.g. merging into a symbol-bearing tag like `C++`), the merge is
rejected the same way an ordinary rename is (see above) — the body text can't be
migrated to the destination's name, so nothing is written.

The content-only-into-structural case above does **not** require `merge: true` — a
content-only identity has no structural row to protect, so there's nothing to
reconcile. The destination-casing rule doesn't apply there either: the body text is
migrated to whatever name was given to the rename, not the destination's preexisting
casing.

## BACKLINKS / Connections panel

The right-sidebar panel shown for an entry combines two kinds of connections:

- **Direct links** — one combined list of incoming and outgoing wikilink/hashtag
  references, each row carrying a direction icon (inbound vs. outbound). Backed by
  `GET /entries/{id}/backlinks` (incoming) and `GET /entries/{id}/outgoing-links`
  (outgoing) — an outgoing reference only appears if it resolves to a real entry.
- **Hashtag connections** — other entries that share a tag (structural or content)
  with the current entry, grouped by tag name. Backed by
  `GET /entries/{id}/hashtag-connections`.

## Reading-view interactions

In the reading view (`frontend/src/routes/viewer/[id]/+page.svelte`):

| Action | On a wikilink | On a hashtag |
|---|---|---|
| Click | Opens the linked entry as a tab | Navigates to the TAGS dashboard, filtered to that tag |
| Right-click | Opens the context menu for the **linked** entry (not the current article) | — (falls through to the current article's own context menu) |
| Middle-click | Adds the linked entry to the right-sidebar Entry Stack, in the background (current reading view stays open) | — |
| Hover | Status bar (bottom-left) shows a fixed label: `[[Wikilink]]` | Status bar shows a fixed label: `TAGS` |

The hover label is always the same fixed text per element type — it does not show the
specific destination title or tag name, mirroring how the existing external-link
hover behavior shows the raw URL (a different kind of "what is this" signal, not a
per-instance one for internal links).

An unresolved wikilink (rendered as a bare `<span>`, not an `<a>`) has no target to
click, right-click, or middle-click through — hover is the only interaction available
for it, since it still carries the `.wikilink-unresolved` class.

## Vault graph

The vault-wide graph and each entry's local subgraph (`GET /entries/{id}/subgraph`,
`GET /entries/graph`) represent a tag as a single node regardless of whether it comes
from a structural tag or a content hashtag — node identity is the same normalized
string described above (`tag:{normalized}`), so an entry connected to a tag only
through a hashtag still appears as a neighbor of a structural tag node of the same
name, and vice versa.

## Known limitations

- The reading view's `data-hashtag` value (and the TAGS-dashboard header it drives)
  uses a simple `toLowerCase()` on the frontend, not the backend's full
  `normalize_tag()`. For the character set hashtags are restricted to
  (`[A-Za-z0-9_]`), this never affects *which* entries are found — the backend
  re-normalizes independently for the actual query — but a rare edge case (e.g. a
  hashtag with doubled or trailing underscores) can show the un-collapsed form in the
  TAGS-dashboard header rather than backend's fully normalized display.

## See also

- `frontend/src/lib/components/RightSidebar.svelte` — Connections panel implementation.
- `frontend/src/lib/stores/entryTitles.ts` — wikilink title resolution index.
- `backend/src/analecta/storage/index.py` — `get_backlinks`, `get_outgoing_links`,
  `get_hashtag_connections`, `list_tags`, `get_entry_ids_by_tag`, `get_graph`,
  `get_subgraph`.
