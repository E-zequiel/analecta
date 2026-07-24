# Extraction Pipeline

This document describes how Analecta turns a pasted URL into the `ExtractedContent` consumed by `api/routes/extract.py` — source-type dispatch, the article pipeline's use of `readability-lxml` and `trafilatura`, and how Substack, YouTube, and X/Twitter extraction differ from it. It complements `docs/privacy.md` (network identity and exposure), `docs/content-sanitization.md` (the injection trust boundary downstream of extraction), and `docs/defuddle-decision.md` (why there is no browser-rendered fallback).

**Scope.** This document covers what happens inside `backend/src/analecta/extraction/`. It does not cover the HTML→Markdown conversion (`markdown/converter.py`, see `docs/content-sanitization.md` § Stage 2) or how the result is saved to the vault (`storage/`).

---

## Dispatch

`extraction/core.py::detect_source_type` classifies a URL by hostname alone, with no network request:

| Host pattern | `source_type` | Extractor |
|---|---|---|
| `youtube.com`, `youtu.be`, `m.youtube.com` | `youtube` | `YouTubeExtractor` |
| `twitter.com`, `x.com` (+ `www.`/`mobile.` variants) | `x` | `XExtractor` |
| `*.substack.com`, `substack.com` | `substack` | `SubstackExtractor` |
| anything else | `article` | `ArticleExtractor` |

`extraction/core.py::extract` dispatches to the matching extractor and returns its `ExtractedContent`. All four extractors are independent implementations of the same `SourceExtractor` interface — none of them share a browser-rendering fallback, because none exists (see `docs/defuddle-decision.md`).

---

## Article extraction (`article.py`)

`ArticleExtractor` is the default path for any URL that isn't YouTube, X, or Substack, and is what `SubstackExtractor` delegates to. It is a single HTTP fetch — no JavaScript on the fetched page ever executes.

### 1. Fetch

`_fetch()` validates the URL against the SSRF blocklist (`ssrf.py::validate_fetch_url` — rejects non-`http(s)` schemes and loopback/link-local/RFC 1918 hosts), then issues a `GET` with `httpx2`, following redirects. Every hop of the redirect chain is re-validated by an event hook (`ssrf.py::block_redirect_to_internal`) before it's followed. Outbound headers (`http_identity.py::build_headers`) present as a generic, current Chrome on Linux — see `docs/privacy.md` for the full identity model. The result's `url` is the post-redirect URL, not the one originally requested.

### 2. Embedded-tweet resolution

`tweet_embeds.py::resolve_embedded_tweets` runs on the raw fetched HTML, before either content extractor sees it. It replaces any classic Twitter/X widget embed (`blockquote.twitter-tweet`, or a pre-rendered `platform.{twitter,x}.com/embed/Tweet.html` iframe) with fully rendered tweet content fetched from the same syndication endpoint `XExtractor` uses. This exists because `readability-lxml` and `trafilatura` handle the raw embed markup inconsistently (one drops it via link-density pruning, the other keeps it but corrupts the author/date line) — replacing it up front with low-link-density markup sidesteps both failure modes. A tweet that can't be fetched degrades to a reshaped version of the embed's own fallback text rather than being left for one of the extractors to mishandle. See `docs/privacy.md` § Embedded Tweet Resolution for the exposure this introduces.

### 3. Preprocessing

Before either content extractor runs, a fixed sequence of `BeautifulSoup` transforms reshapes the HTML (`_parse()`, in order: `_strip_hidden_elements`, `_simplify_figure_images`, `_unwrap_sections`, `_reunite_intro_with_body`, `_strip_heading_classes`, `_rescue_linked_lists`, `_rescue_linked_tables`, `_expand_table_spans`, `_rescue_short_nested_lists`, `_rescue_short_figure_labels`, `_unwrap_code_examples`). Each function exists to work around one specific `readability-lxml` pruning rule that would otherwise drop genuine content — readability scores each DOM subtree and discards low-scoring ones, and several of its heuristics (link-density thresholds, a 25-character conditional-clean minimum, class-name penalties) have known false-positive shapes on real sites (MDN, Wikipedia, Substack, milkroad.com, socket.dev, system76.com). Order matters: several later passes depend on an earlier one having already run (e.g. `_reunite_intro_with_body` requires `_unwrap_sections` to have flattened MDN's `<section>` wrapper first). Each function's own docstring documents the specific failure mode and site it addresses — this list is not repeated in prose here to avoid the two going out of sync.

The same preprocessed HTML feeds both extractors in the next step, so a rescue here benefits whichever one wins.

### 4. Readability-lxml and trafilatura, and how the pipeline chooses between them

Both extractors run against the same preprocessed HTML:

- **`readability-lxml`** (`Document(clean).summary()`) — scores DOM subtrees by text density and link density, keeps the highest-scoring one. Preferred by default: it preserves `<code>`/`<pre>` structure and list semantics more faithfully than trafilatura's own HTML output.
- **`trafilatura`** (`trafilatura.extract(..., output_format="html", favor_precision=True)`) — a different extraction algorithm, generally better at following the boundaries of long, multi-section technical content.

The pipeline prefers readability's output unless trafilatura's HTML is more than 1.5x longer — a proxy for "trafilatura found real depth readability missed" (long technical posts, multi-section articles), rather than switching extractors on every minor length difference. There is no per-site configuration; the same heuristic runs for every article.

Title and metadata (`author`, `description`, `published`) come from `trafilatura.extract_metadata()` regardless of which extractor won the content — trafilatura's metadata extraction is independent of its content-extraction path and is run unconditionally. If trafilatura found no title, `readability`'s own `Document.title()` is used as a fallback.

### 5. Post-processing

- `_strip_loading_placeholders` removes elements whose entire visible text is a client-side "Loading…" skeleton string, regardless of which extractor produced them.
- If the resulting content is under 200 words, `_try_nextjs_hydration` looks for a Next.js Pages Router `__NEXT_DATA__` JSON blob in the raw HTML and extracts text directly from `pageProps` — a fallback for pages whose real content only exists client-side, never in the fetched HTML at all. This only fires when both DOM extractors came up short; a server-rendered page with a full HTML body never reaches this path, since its hydration blob would also carry the entire page shell (navigation, UI strings) with no way to distinguish it from article text.
- `_rescue_orphaned_header` runs against the *raw* HTML, independent of whichever candidate readability/trafilatura picked, and prepends a dek/standfirst paragraph or hero image when a site's structural layout put them in a header branch neither extractor's winning candidate included (milkroad.com, socket.dev). Only fires when the piece is confirmed missing from the already-extracted content, so sites where it was already included are untouched.
- If the final content is still under 100 characters, extraction fails with `ExtractionError` — there is no further fallback.

### 6. Low-confidence signal

`_is_low_confidence()` flags extraction that likely missed JavaScript-rendered content — either the extracted text is under 200 words, or the raw HTML is disproportionately `<script>` tags (over 40% of all tags). Its result is written to the saved entry's frontmatter as `low_confidence: true|false` on every extraction. This is a diagnostic signal only, not a trigger for anything: there is no second extraction strategy to fall back to, so a person who suspects a thin capture can check the frontmatter rather than the pipeline silently guessing. See `docs/defuddle-decision.md` for why no automatic recovery exists for this case.

---

## Substack (`social.py`)

`SubstackExtractor` is a thin wrapper around `ArticleExtractor` — Substack renders full post content server-side, so the article pipeline handles it without special treatment. The only Substack-specific step is resolving a `substack.com/inbox/post/<id>` URL (the form Substack's own inbox UI links to) to its canonical `*.substack.com/p/<slug>` form via an HTTP `HEAD` request before handing off to `ArticleExtractor`. The SSRF blocklist applies to this resolution `HEAD` request the same way it applies to the article fetch that follows.

---

## YouTube (`youtube.py`)

`YouTubeExtractor` does not fetch or parse any HTML. It extracts the video ID from the URL, then concurrently: fetches the video title and channel name via YouTube's oEmbed endpoint, and fetches the transcript via `youtube-transcript-api` (preferring English or Spanish, falling back to whatever transcript is available). The transcript is rendered as one `<p>` per transcript entry. `ExtractionError` is raised when transcripts are disabled or none exist for the video — there is no fallback content for a video with no transcript.

---

## X/Twitter (`x.py`)

`XExtractor` fetches a tweet via X's own syndication endpoint (`cdn.syndication.twimg.com`), with the official oEmbed endpoint as a fallback for endpoint drift. It walks the tweet's full reply chain upward (`in_reply_to_status_id_str`) to the conversation's root, and renders each tweet in the chain as its own attributed block. Video and animated-GIF media are linked out rather than downloaded. A long-form "Note Tweet" that both endpoints only expose in truncated form gets a visible truncation marker rather than a silent mid-sentence cut. No headless browser is used anywhere in this path — same syndication call `tweet_embeds.py` reuses for inline embeds inside articles (see above).

---

## What's deliberately not part of this pipeline

Analecta has no browser-rendered extraction fallback. Every extraction strategy above works from a single plain HTTP fetch's HTML (or, for YouTube, an API response) — nothing on a fetched page ever executes. `docs/defuddle-decision.md` records why, and describes Defuddle's remaining role as an offline, developer-run diagnostic tool (`scripts/defuddle-diagnostic.mjs`) that never participates in a real extraction.

---

## Cross-References

- `docs/defuddle-decision.md` — why there is no browser-rendered fallback, and Defuddle's diagnostic-only role today.
- `docs/privacy.md` — the network identity presented by extraction fetches, the SSRF guard, and the exposure introduced by embedded-tweet resolution.
- `docs/content-sanitization.md` — how the HTML this pipeline produces is safely converted to Markdown and rendered, including why extracted HTML must be treated as attacker-influenced input.
