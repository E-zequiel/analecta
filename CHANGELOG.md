# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-07-06

Initial public release.

### Added

- Web extraction — paste a URL (`Ctrl+L`) to capture articles, YouTube transcripts, and Substack posts as clean Markdown, using trafilatura and readability-lxml for content extraction, markdownify for the Markdown conversion, youtube-transcript-api for transcripts, and defuddle as a rendered-page fallback.
- Native Markdown reading view — every captured entry renders as clean, formatted Markdown via markdown-it, with Shiki for syntax-highlighted code blocks.
- Local vault — every entry saved as a Markdown file in a user-controlled directory, compatible with Logseq and other PKM tools.
- Reading library — status-based organisation: Unread, Read, Bookmark, Gem, Archive.
- Full-text search across titles and content powered by SQLite FTS5 (`Ctrl+K`).
- Tag list, automatic bidirectional Linked Mentions, clickable `[[wikilink]]` rendering, and clickable `#hashtag` navigation to the TAGS dashboard across the vault. Hashtags and tag names accept Spanish-accented letters (`áéíóúñü`) and symbols (`_ - ' ~ ^`) in addition to ASCII. Tag identity is unified vault-wide and case-insensitive — `Python`, `python`, and `#PYTHON` all count as the same tag, while preserving whichever casing was curated first for display — but accent- and symbol-sensitive: `café` and `cafe` are different tags. A `#hashtag` that happens to match another entry's title resolves to it in the backlinks panel and vault graph across the full charset, so `#café` correctly connects to a "Café" entry and `#well-being` to a "Well-Being" entry, not just plain-ASCII titles. Creating or renaming a tag — from the sidebar or the TAGS dashboard — enforces the same hashtag charset, so every tag stays writable as an inline `#hashtag`, with one consistent, clearly visible error message for invalid names; a newly created tag now shows up on the TAGS dashboard immediately instead of requiring a rescan. Renaming a tag into another existing tag's name merges the two, with an explicit confirmation step since the merge can't be undone. Right-clicking a wikilink opens the context menu for the linked entry; middle-clicking a wikilink adds the linked entry to the right-sidebar entry stack without leaving the current reading view; hovering a wikilink or hashtag shows a type label (`[[Wikilink]]`/`TAGS`) in the status bar.
- Vault reconciliation — files edited outside Analecta (another editor, a sync tool) are automatically re-derived for tags, links, and search content the next time the sidecar starts, plus a manual "Rescan vault" action (`Ctrl+R`, also available in Settings) for edits made while the app is already running; an open reading view for the edited entry refreshes in place.
- Manually connect related entries via a search-to-connect dialog.
- Vault-wide knowledge graph, built with Sigma.js and graphology, and per-entry subgraph, built with d3-force.
- Built-in Markdown editor with CodeMirror 6 and Tokyo Night theme.
- Multi-tab reading with scroll position persistence across sessions.
- System tray integration (configurable close-to-tray behavior, off by default).
- Clipboard-to-URL capture via `Ctrl+L`.
- Auto-updates via electron-updater.
- Native Linux packaging — `.deb`, `.rpm`, `.AppImage` — with correct application identity and icons, including taskbar/alt-tab icon support on Wayland compositors, built with electron-builder.
- Release integrity verification — SHA256SUMS checksums for all packaged installers, plus a Sigstore build provenance attestation (attaches automatically once the repository goes public).

[Unreleased]: https://github.com/E-zequiel/analecta/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/E-zequiel/analecta/releases/tag/v0.3.1
