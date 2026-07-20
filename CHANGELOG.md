# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Upgraded the bundled Defuddle rendering library to 0.19.1, patching a high-severity (CVSS 8.2) cross-site scripting vulnerability in its site-extractor output handling ([GHSA-jg4p-g6xj-4qmf](https://github.com/kepano/defuddle/security/advisories/GHSA-jg4p-g6xj-4qmf)).
- Disabled Defuddle's `useAsync` option in the browser-rendered extraction pass, which previously allowed its site-specific extractors to silently call third-party APIs (e.g. FxTwitter) from inside the scraping window — a network path invisible to the render server's own URL blocklist, and part of the same extractor subsystem patched above. Analecta never relied on this (own YouTube/Substack handling; X/Twitter extraction is unimplemented by design), so this closes an unaudited egress path with no functional loss.
- Extraction requests no longer identify Analecta or its maintainer to the sites they fetch — the previous User-Agent embedded a personal GitHub URL on every request. Outbound requests now present as a generic, current Chrome on Linux, with a coherent header set (client hints, fetch metadata) to match, single-sourced from Electron's own bundled Chromium version so it can't go stale or drift between the direct-fetch and browser-rendered extraction paths. See `docs/privacy.md`.
- The browser-rendered extraction window now presents as plain Chrome instead of leaking an Electron/app-identifying User-Agent, and WebRTC is now configured to route only through the same path as other traffic — intended to close a path where a page's own script could otherwise gather and leak the real IP independently of the User-Agent (pending manual verification, see `docs/privacy.md`).
- The browser-rendered extraction window's session no longer persists to disk across app restarts — it previously used a named, disk-backed session partition with no expiry.

### Fixed

- Packaged Linux builds now show the correct taskbar/alt-tab label and icon on Wayland compositors — the app previously broadcast `analecta-electron` as its window identity instead of `analecta`.
- Images with root-relative (`/foo.svg`) or protocol-relative (`//cdn.example.com/foo.svg`) `src` URLs are now resolved against the source article's URL before download, instead of leaking a broken path into the saved Markdown.
- Extracted articles now record the post-redirect URL as their canonical source URL instead of the originally requested one, so a domain or scheme change mid-redirect (e.g. `http://` → `https://`, or a moved Substack post) no longer causes the image-resolution fix above to resolve relative asset paths against the wrong host.
- Article headings are no longer silently dropped on sites (e.g. MDN) that wrap the entire heading text in a self-referencing permalink link — previously treated as a link-only heading and discarded during extraction.
- The article's opening paragraph(s) are no longer silently dropped on reference-doc sites (e.g. MDN) that place the title and intro text in a separate sibling from the rest of the body content — previously treated as a low-value fragment and discarded in favor of the higher-scoring body section.
- Interactive live-code demos on MDN pages are now captured as a static image and saved into the vault instead of disappearing entirely — these are populated by client-side JavaScript after the page loads, so extraction previously had nothing to show for them.
- The browser-rendered extraction pass no longer hangs and silently falls back to the lower-quality plain-HTML pass on pages with recurring background network activity (e.g. analytics beacons) — it was waiting for the page to go fully network-idle, which such pages never do.
- Genuine cross-reference sections on reference-doc sites (e.g. MDN's "See also" list) are no longer stripped from the browser-rendered extraction pass, along with everything after them — previously misidentified as a blog-style "related posts" widget by a generic anti-boilerplate heuristic and deleted wholesale.
- CSS code blocks on sites using the SyntaxHighlighter `brush: css` class convention (e.g. MDN) now keep their language label in the browser-rendered extraction pass, instead of coming out as an unlabeled code fence — a gap in the bundled rendering library's own language whitelist.

## [0.3.1] - 2026-07-13

Initial public release.

### Added

- Web extraction — paste a URL (`Ctrl+L`) to capture articles, YouTube transcripts, and Substack posts as clean Markdown, using trafilatura and readability-lxml for content extraction, markdownify for the Markdown conversion, youtube-transcript-api for transcripts, and defuddle as a rendered-page fallback.
- Native Markdown reading view — every captured entry renders as clean, formatted Markdown via markdown-it, with Shiki for syntax-highlighted code blocks.
- Local vault — every entry saved as a Markdown file in a user-controlled directory, compatible with Logseq and other PKM tools.
- Reading library — status-based organisation: Unread, Read, Bookmark, Gem, Archive.
- Full-text search across titles and content powered by SQLite FTS5 (`Ctrl+K`).
- Tag list, automatic bidirectional Linked Mentions, clickable `[[wikilink]]` rendering, and clickable `#hashtag` navigation to the TAGS dashboard across the vault. Hashtags and tag names accept Spanish-accented letters (`áéíóúñü`) and symbols (`_ - ' ~ ^`) in addition to ASCII. Tag identity is unified vault-wide and case-insensitive — `Python`, `python`, and `#PYTHON` all count as the same tag, while preserving whichever casing was curated first for display — but accent- and symbol-sensitive: `café` and `cafe` are different tags. A `#hashtag` that happens to match another entry's title resolves to it in the backlinks panel and vault graph across the full charset, so `#café` correctly connects to a "Café" entry and `#well-being` to a "Well-Being" entry, not just plain-ASCII titles. Creating or renaming a tag anywhere — the sidebar, the TAGS dashboard, or the reading view's inline "Add tag…" box — enforces the same hashtag charset, so every newly minted tag stays writable as an inline `#hashtag`, with one consistent, clearly visible error message for invalid names; a tag name that already exists (however it got there) is always usable and re-assignable regardless of charset. A newly created tag now shows up on the TAGS dashboard immediately instead of requiring a rescan. Renaming a tag into another existing tag's name merges the two, with an explicit confirmation step since the merge can't be undone. Right-clicking a wikilink opens the context menu for the linked entry; middle-clicking a wikilink adds the linked entry to the right-sidebar entry stack without leaving the current reading view; hovering a wikilink or hashtag shows a type label (`[[Wikilink]]`/`TAGS`) in the status bar. A wikilink with an empty or whitespace-only alias (`[[Title|]]`) is now fully indexed for the backlinks panel and vault graph, matching how it already rendered in the reading view.
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
