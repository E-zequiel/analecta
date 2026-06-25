# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - YYYY-MM-DD

Initial public release.

### Added

- Web extraction — paste a URL (`Ctrl+L`) to capture articles, YouTube transcripts, and Substack posts as clean Markdown.
- Local vault — every entry saved as a Markdown file in a user-controlled directory, compatible with Logseq and other PKM tools.
- Reading library — status-based organisation: Unread, Read, Bookmark, Gem, Archive.
- Full-text search across titles and content powered by SQLite FTS5 (`Ctrl+K`).
- Hierarchical tag tree and automatic Linked Mentions across the vault.
- Vault-wide knowledge graph and per-entry subgraph, built with Sigma.js.
- Built-in Markdown editor with CodeMirror 6 and Tokyo Night theme.
- Multi-tab reading with scroll position persistence across sessions.
- System tray integration and clipboard-to-URL capture.
- Auto-updates via in-app updater.
- Distribution packages for Linux: `.deb`, `.rpm`, `.AppImage`.

[Unreleased]: https://github.com/E-zequiel/analecta/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/E-zequiel/analecta/releases/tag/v0.3.0
