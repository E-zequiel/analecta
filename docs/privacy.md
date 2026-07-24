# Privacy: Network Identity and IP Exposure

This document states what Analecta actually protects when it fetches a URL, tracking the posture against the README's privacy claim ("No cloud sync, no subscription, no tracking. Truly private and yours."). It complements `docs/content-sanitization.md` (which covers code/markup injection, not network exposure) and `docs/electron-shell-security.md` (the Electron IPC/protocol/CSP surface).

**Scope.** This document covers the network identity Analecta presents to the sites it fetches, and how that exposure compares to browsing the same URL in a browser. It does not cover local vault file security (OS filesystem permissions, disk encryption) or content injection (see `docs/content-sanitization.md`).

---

## The Exposure Model

Analecta fetches a URL through a single path: a plain HTTP `GET` (`httpx2`), no JavaScript execution, no subresource loading, no cookies. Nothing on a fetched page ever runs. This is **strictly less** exposure than visiting the same URL in a browser — third-party trackers, analytics, and fingerprinting scripts never run, because nothing on the page executes.

---

## Identity Headers — Invariants

| Invariant | Enforced at | Why it matters |
|---|---|---|
| No Analecta- or maintainer-identifying string in any outbound header | `backend/src/analecta/extraction/http_identity.py` | A custom User-Agent naming Analecta or a maintainer's GitHub repository would tie every outbound request to a specific person's identity. Never introduce one. |
| A generic, current Chrome-on-Linux UA, not a real specific browser install | `http_identity.py::_user_agent` | Chrome's own UA is deliberately reduced/frozen (minor/build/patch zeroed) as of the Chromium User-Agent Reduction rollout — mirroring that shape, rather than querying and copying a real locally-installed browser's exact version, avoids pinning a sharper, more identifying fingerprint than real Chrome itself now sends. |
| Chrome *major version* single-sourced from Electron's own bundled Chromium | `ANALECTA_CHROME_MAJOR` env var, set in `electron/main/sidecar.ts` from `chrome-identity.ts::CHROME_MAJOR` (`process.versions.chrome`), read by `http_identity.py::_chrome_major` | The claimed UA borrows the real Chromium version actually bundled with the app rather than a hardcoded one that would age into an anomaly. `http_identity.py` falls back to a hardcoded major version only when no Electron parent set the env var (`/dev`, pytest) — bump the fallback occasionally so a fresh checkout doesn't claim an old Chrome. |
| Coherent header set beyond the UA (`Sec-CH-UA`, `Sec-Fetch-*`, `Accept`, generic `Accept-Language`) | `http_identity.py::build_headers` | A Chrome-shaped UA riding otherwise-bare headers (no client hints, no fetch metadata) is itself a tell — real Chrome never sends a UA without the low-entropy client hints alongside it. `Accept-Language` is deliberately a generic `en-US,en;q=0.9`, not the real system locale, which would itself be a small deanonymizer. |
| `Accept-Encoding` is never set explicitly | `http_identity.py::build_headers` docstring | httpx2 already negotiates the correct value for the codecs actually installed (gzip/deflate always; brotli/zstd only if those extras are present). Claiming a codec that isn't installed would return undecodable bytes. |

**Explicitly not pursued: TLS/HTTP2 (JA3/JA4) fingerprint parity.** A real browser has no *hidden* TLS fingerprint from the site it's visiting — TLS fingerprinting is an anti-bot-detection concern, not a privacy-vs-a-browser concern, so closing it wouldn't move Analecta closer to the "no worse than a browser" bar. It would require a compiled dependency (e.g. `curl_cffi`) outside the `httpx2`-only stack rule, and even actively-maintained forks trail real Chrome's current version. Revisit only if extraction starts hitting anti-bot blocks in practice — a different problem than privacy parity.

---

## Image Egress

No saved entry can reference a live remote image. `AssetDownloader` (`backend/src/analecta/extraction/assets.py`) retries a failed image fetch once, then falls back to a bundled local placeholder rather than keeping the original remote URL. CSP `img-src` (`electron/main/protocols.ts`) enforces this as an invariant rather than a convention: it permits only `analecta-file:`, `data:`, `blob:`, and `app:` — a stray remote `https:` reference in a `.md` file simply fails to render, it doesn't silently re-fetch and leak the reading IP.

---

## External Links

Every `http(s)://` link inside a saved entry — including the "View video on X" links X/Twitter extraction leaves in place instead of downloading video/animated-GIF media (see local Hard Constraints) — opens in the OS's default browser, not inside Analecta's own process. The reading view intercepts every link click (`frontend/src/routes/viewer/[id]/+page.svelte::handleContentClick`), calls `preventDefault()`, and routes it through the `open-url` IPC channel (`electron/main/ipc.ts`), which validates the scheme (`http:`/`https:` only, see `docs/electron-shell-security.md`) before calling `shell.openExternal`.

**Why a link is not the same exposure as an embedded resource.** Unlike an `<img src>` (Image Egress, above), a link never fetches anything on its own — nothing happens until the user clicks it. And once clicked, the request happens entirely outside Analecta: no extraction-pipeline identity headers, no Analecta-managed session applies, because Analecta's own code never makes that request. It is the same exposure as the user typing that URL into their own browser directly — governed by whatever VPN/browser habits they already have (see IP Exposure and VPNs, below), not by anything Analecta does or doesn't do.

---

## Embedded Tweet Resolution

Article extraction (`backend/src/analecta/extraction/tweet_embeds.py`) resolves classic Twitter/X widget embeds — a `blockquote.twitter-tweet` or a `platform.{twitter,x}.com/embed/Tweet.html` iframe found in an article's own static HTML — into fully rendered tweet content, using the same `cdn.syndication.twimg.com` endpoint the standalone X extractor (`extraction/x.py`) already calls.

**The exposure shift this introduces, stated plainly.** Standalone X extraction only ever contacts `cdn.syndication.twimg.com` when the user directly pastes an X/Twitter URL to extract — an explicit action. Embedded-tweet resolution makes that same request *implicitly*: reading any third-party article that happens to embed a tweet now discloses to X (via IP, at fetch time) that this article was read, for every embedded tweet on the page, whether or not the user has any interest in that specific tweet. No new third-party host is contacted — same endpoint, same `build_headers("api")` identity headers as the existing X path, no `Referer` header (never set anywhere in `http_identity.py`, so the article's URL itself doesn't leak alongside the tweet request) — but the *trigger* changes from a direct user action to article content the user didn't choose tweet-by-tweet.

**No opt-out.** Embedded-tweet resolution runs unconditionally, the same posture standalone X extraction (`extraction/x.py`) already has. Reading any third-party article with an embedded tweet contacts `cdn.syndication.twimg.com` with no way to suppress it short of not reading that article. Flagged here as a gap worth a future opt-out, not resolved today.

**The failure path adds no further exposure.** When a tweet fails to fetch (deleted, rate-limited, network error), resolution falls back to reshaping the embed's own already-present static fallback text (or, for an iframe with no fallback text, a bare permalink) rather than retrying against a second endpoint (e.g. oEmbed) — one exposure event per embed, never two.

**Concurrency is bounded**, same idiom as `AssetDownloader`'s image downloads (`asyncio.Semaphore`, capped at 5 concurrent requests) — a resource-use bound, not a privacy control in itself, but it keeps an embed-heavy article from firing a burst of simultaneous requests at X's CDN.

---

## IP Exposure and VPNs

**Under a system-level VPN, Analecta and a browser are exposed identically — automatically, no Analecta-side configuration.** A system VPN (a native desktop app — Brave's own VPN, Mullvad, ProtonVPN, WireGuard, OpenVPN client) rewrites the OS routing table below the application layer. It does not distinguish which process opened a socket: `httpx2`, Electron/Chromium, and a browser all egress through the same tunnel. Analecta's code has no interface, DNS, or proxy pinning that would bypass this (verified by inspection of `electron/main/*.ts` — no `session.setProxy`/custom resolver/interface binding exists).

**The asymmetry: browser-only routing does not cover Analecta.** A VPN *browser extension*, an in-browser SOCKS proxy, or **a browser's own Tor-only private window** (e.g. Brave's Private Window with Tor) only routes that browser's traffic — Analecta is a separate process and is not covered. Concrete scenario: reading a URL in a Tor-routed private window, then pasting that same URL into Analecta to extract it, sends the extraction request with the real IP, with no relation to the Tor session's protection.

**Practical guidance:** use a system-level VPN if IP privacy matters for your use of Analecta, not a browser-only one. This is a configuration choice, not something Analecta's code can detect or enforce.

**What this does not claim:** with no VPN of either kind active, Analecta's IP exposure is the same as a browser's — direct connection, real IP, same as always. Nothing in this document changes that baseline; it only establishes that Analecta doesn't add exposure *beyond* whatever the user's existing browser habits already involve.

---

## Maintaining These Invariants

When touching the extraction code:

1. **Never hardcode a User-Agent (or any header) that names Analecta, a GitHub URL, or any maintainer identity.** If a site requires a distinct identifying UA for compliance reasons, that's a deliberate, scoped exception — not a default.
2. **Keep the Chrome major version single-sourced.** If a fetch site ever needs its own version number, re-derive it from `chrome-identity.ts::CHROME_MAJOR`/`ANALECTA_CHROME_MAJOR` rather than hardcoding a second value that can drift.
3. **Any new fetch site must call `http_identity.py::build_headers`**, not construct its own header dict — a bespoke header set is exactly the "UA says Chrome, headers say Python" inconsistency this module exists to prevent.

---

## Cross-References

- `docs/extraction.md` — how the pipeline whose identity/exposure this document tracks actually works.
- `docs/content-sanitization.md` — the code/markup injection trust boundary; this document is the network/identity half of Analecta's privacy posture, not the injection half.
- `docs/electron-shell-security.md` § Layer 5 (Content Security Policy) — `img-src` permits only `analecta-file:`, inline data (`data:`/`blob:`), and the packaged app bundle (`app:`); every image reference must resolve to one of those, never `https:`.
