# Decision: Remove Browser-Rendered Extraction (Defuddle)

**Status:** Accepted
**Date:** 2026-07-23
**Context:** `feat/extraction-pipeline-quality` — extraction becomes a single, Python-only pipeline; Defuddle is retained solely as an offline developer diagnostic

---

## Background: how extraction used to work

Analecta turns a pasted URL into a clean Markdown file in a person's local vault. Doing that requires fetching the page and pulling the real article content out of it — discarding navigation bars, ads, cookie banners, and other page furniture.

For most of this project's history, extraction could happen in one of two ways:

1. **A plain HTTP fetch.** Analecta's Python backend requests the page's HTML directly (using [trafilatura](https://github.com/adbar/trafilatura) and [readability-lxml](https://github.com/buriy/python-readability) to identify and extract the article content) and converts the result to Markdown. Nothing on the page ever runs — this is a read of exactly the bytes the server sent, with zero code execution.
2. **A browser-rendered fallback.** When the plain fetch produced content that looked suspiciously thin — too little extracted text, or a page whose raw HTML was mostly `<script>` tags with little else — Analecta's desktop shell (Electron) would open the URL in a real, hidden Chromium browser window, let the page's own JavaScript run and build its DOM, and then hand the resulting live document to [Defuddle](https://github.com/kepano/defuddle), a third-party JavaScript library purpose-built for pulling readable article content out of an already-rendered page (the same kind of tool that powers the Obsidian Web Clipper's content extraction).

The fallback existed because some pages genuinely don't have their article text anywhere in the initial HTML response — sites built as single-page applications construct the visible page entirely through their own JavaScript after the browser loads it. A plain HTTP fetch cannot see content that doesn't exist yet at fetch time; only a real browser, executing that JavaScript, can.

## Why the fallback was removed

Analecta's extraction pipeline was evaluated over an extended period of real, day-to-day use with the browser-rendered fallback deliberately turned off, fixing the plain-fetch pipeline directly for every real failure that came up. Across that period, every genuine extraction gap found was fixable as a targeted improvement to the plain-fetch pipeline itself. No real, recurring case was found where rendering the page in an actual browser was the only way to get usable content.

That result changed the cost/benefit balance of keeping the fallback at all:

- **Privacy cost.** Running a page's own JavaScript means giving up the one guarantee the plain-fetch path provides for free: that nothing the page ships ever executes. A real browser executing arbitrary third-party code is a fundamentally larger amount of trust extended to whatever a fetched page happens to contain — analytics, fingerprinting scripts, anything — even with mitigations like blocking known tracker domains at the network level. That is a meaningfully weaker position than never executing anything at all, and it stood in direct tension with Analecta's own goal of being a private, local-only tool.
- **Complexity cost.** The fallback required an entire second subsystem: a hidden browser window, a local network server bridging the Python backend and the desktop shell's browser, token-based authentication between the two, network-level tracker-blocking rules, and a series of narrow workarounds for that specific library's own behavior. That is a large, ongoing maintenance surface in service of a capability that, in practice, never turned out to be needed.

Given both, the decision was to remove the browser-rendered fallback entirely, rather than narrow the conditions under which it fired. Extraction is now handled exclusively by the plain-fetch, Python-only pipeline.

## Alternatives considered

- **Narrow the fallback's trigger conditions instead of removing it.** Rejected — the trial period found no real page where the fallback was actually needed, so there was no evidence-backed condition left to narrow it *to*.
- **Run Defuddle's own extraction algorithm directly against the plain-fetched HTML**, without a browser, as a permanent second extraction attempt. Feasible in principle (Defuddle can process a raw HTML string using a lightweight, script-free parsing library instead of a real browser — see below), but rejected as a live pipeline component: fed the same static HTML the Python pipeline already has, it cannot recover anything a browser-executed page uniquely provides, since no JavaScript runs either way. What it offers at that point is a different extraction algorithm applied to identical input — worth checking case by case (see below), not worth running unconditionally on every extraction.
- **Bundle a JavaScript runtime inside the Python backend** so Defuddle's code could run directly as part of every extraction. Rejected — this would reintroduce a persistent third-party-code dependency into the shipped application for an unproven, page-by-page benefit, working against the exact simplification this decision is making.

## What this decision gains

### Privacy and security

- **No code from a fetched page ever executes, without exception.** The browser-rendered fallback was the one place a real browser engine ran arbitrary third-party JavaScript; removing it removes that capability from the application entirely, not just narrows it.
- **Removes a local network endpoint and its authentication token.** The bridge between the Python backend and the desktop shell's browser window is gone along with the fallback it existed to serve.
- **Removes two third-party packages from the shipped application.** Defuddle and the companion ad/tracker-blocking library it depended on are no longer part of the installable app at all — see below for where Defuddle still lives.

### Simplicity

- A single, unconditional extraction path, instead of two systems bridged by a local network connection.
- An entire subsystem is gone: browser automation, its tracker-blocking rules, and the reliability workarounds that any browser-automation approach eventually accumulates.
- Fewer third-party packages in the distributed application.

## Accepted trade-off

Some pages build their entire visible content through their own JavaScript, with nothing readable in the plain HTML response at all. Without a browser-rendering fallback, such a page now produces a thin or incomplete result, with no automatic recovery. This is a deliberate trade-off made in exchange for the properties described above, not an oversight.

## Defuddle's role today: an offline diagnostic tool

Defuddle is not deleted from the project. It remains a pinned, version-controlled, development-only dependency — never part of the installable application — used exclusively as a manual comparison tool.

When a real extraction failure comes up, a developer can run Defuddle by hand against the same raw HTML the Python pipeline already fetched, and see whether Defuddle's own extraction logic recovers something the pipeline missed. This works without a browser: Defuddle can parse a plain HTML string using [linkedom](https://github.com/WebReflection/linkedom), a lightweight library that builds a DOM from HTML text without executing any of the page's scripts — the same no-code-execution guarantee the rest of the pipeline relies on holds for this diagnostic use too. Any useful technique found this way is reimplemented directly in the Python pipeline; Defuddle itself never runs as part of a real extraction, and never ships with the application a person installs.
