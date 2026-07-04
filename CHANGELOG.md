# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-07-02

Initial public release.

### Added

- Web extraction — paste a URL (`Ctrl+L`) to capture articles, YouTube transcripts, and Substack posts as clean Markdown, using trafilatura and readability-lxml for content extraction, markdownify for the Markdown conversion, youtube-transcript-api for transcripts, and defuddle as a rendered-page fallback.
- Native Markdown reading view — every captured entry renders as clean, formatted Markdown via markdown-it, with Shiki for syntax-highlighted code blocks.
- Local vault — every entry saved as a Markdown file in a user-controlled directory, compatible with Logseq and other PKM tools.
- Reading library — status-based organisation: Unread, Read, Bookmark, Gem, Archive.
- Full-text search across titles and content powered by SQLite FTS5 (`Ctrl+K`).
- Tag list, automatic bidirectional Linked Mentions, and clickable `[[wikilink]]` rendering across the vault.
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
