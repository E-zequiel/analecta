# Security Log — Analecta

Record of the repository's own security posture: CI integration, the npm
provenance verification gate, hardening of the project's own code and
scripts, and the dependency cooldown policy. It carries no third-party CVE
or advisory records — advisory ids, CVSS scores, and per-bump ledgers are
deliberately not maintained in tracked docs; the security pins and their
reasons live inline in the enforcing configs (`pnpm-workspace.yaml`
overrides, `backend/pyproject.toml` `[tool.uv]` floors), which are the sole
record.

Referenced by `docs/github-actions-security.md` Controls 9 and 12.

---

## CI Integration

Defined in `.github/workflows/ci.yml` (`socket` job) and `.github/workflows/release.yml`.

- `ci.yml`: triggers on PRs with `pnpm-lock.yaml` or `backend/uv.lock` changes. Runs `pnpm exec socket ci --org Ezequiel --no-interactive`.
- `release.yml`: unconditional, runs before build on every version tag. Uses `socket scan create . --json --no-interactive --org Ezequiel` (not `socket ci` — that subcommand requires PR context).
- `socket-manual.yml`: `workflow_dispatch` for on-demand scans against any branch ref. Dispatch on `main`, set `ref` input to the target branch.

**Free plan limitation:** Socket only posts PR comments — it cannot block merges. Acceptable for solo-dev workflow.

---

---

## Provenance verification gate

Authoritative description of the npm SLSA provenance gate
(`scripts/verify_provenance.py`, CI job `verify-provenance` in `ci.yml`).
Maintained in place: this section always describes the gate as it exists
now. Dated change records live under `### Change history` below; the
implementation is the source of truth for behavior.

**What it guarantees.** For every entry in `pnpm-lock.yaml` whose package
publishes an SLSA provenance attestation on npm, the gate (1) fetches the
Sigstore bundle through a path independent of the registry's serving layer,
(2) verifies the Fulcio certificate chain and the Rekor transparency-log
inclusion proof, (3) ties the attested subject SHA-512 to the lockfile's
integrity value, and (4) refuses to succeed unless every parsed package is
accounted for as verified, failed, or legitimately skipped. Exit 1 on any
failure; the sweep never reports success over an unaccounted set.

**The lockfile scan is single-pass.** One walk over the entry blocks
produces everything the guards see (`LockfileScan`: parsed, unmatched,
conflicting, unaccounted, resolution_blocks). The parser and every guard
therefore cannot disagree about what was covered. The scan never raises;
classification — never arithmetic on free text — decides.

**Guards, most-diagnostic-first.** `parse_lockfile` refuses, in order:

1. untokenizable package entries — package-shaped lines the splitter
   cannot tokenize (detected by an independent raw-line scan);
2. unmatched package entries — package-shaped keys carrying a resolution
   that did not parse (peer-suffixed keys with integrity; git/tarball
   resolutions without one);
3. unaccounted material — resolution/integrity material behind a key that
   is no package entry (empty scalars and `@zkochan/` entries stay
   deliberate skips), or orphan inline material beyond a block's own
   resolution (classified by line SHAPE: a hash-shaped `integrity:` value
   on a line that does not start with `resolution:` or `integrity:`);
4. zero parsed entries from a lockfile whose text declares resolutions
   the walk never reached (total shape drift);
5. unreached resolution lines — the partial-drift counterpart (raw
   resolution lines vs lines the walk owns);
6. conflicting duplicate identities — two entries collapsing to one
   `(name, version)` identity with different integrity values; identical
   values are a quiet dedup.

**Registry metadata layer.** `_fetch_json` returns `None` only when the
request never got a well-formed answer; a well-formed answer is validated
down to every advertised layer. `get_provenance_bundle` raises
`RegistryTransportError` (no well-formed answer) or `RegistryShapeError`
(well-formed but contract-violating) — both `RuntimeError` subclasses.
The sweep classifies collected failures by the exception's class, never by
message wording: the shape messages embed the package name, which the
publisher controls, so wording-based classification would be spoofable.
Bare `RuntimeError`s fail toward the transport reading. The legitimate
skip is reserved for well-formed metadata without a usable
`dist.attestations.url`; once a URL is advertised, every layer must be the
right shape and the document must carry an SLSA-provenance attestation, or
the run fails.

**Sigstore verification.** Classification is decided by exception class.
A `VerificationError` is fatal except for the library's one fixed
compatibility sentence (matched exactly, never by loose resemblance); a
`NetworkError` is a warning; the bundle-format skip is decided by the
genuine `sigstore.models.InvalidBundle` class (isinstance), with the MRO
name match strictly a fallback for environments where the class is
unimportable — and the skip message names which path decided, so
library-upgrade drift is visible in CI logs. Anything unclassifiable
fails closed.

**The sweep report.** Failures are collected per package (one blip never
cuts the sweep short) as `(package, bucket, reason)`. The final report
groups failures by class (transport → registry-shape → subject-hash →
sigstore, fixed order, per-class counts), states the affected scope
("N of M parsed packages affected"), and the registry-shape class carries
its explicit disposition: shape violations suggest a registry-level actor
reshaping the served document — a supply-chain signal, not a mere
availability incident. Fail-closed-per-package is the deliberate
disposition: the hostile class is never silenced by an availability
threshold.

**Deliberate limits.**

- Partial censorship below 100% is invisible to the aggregate guard
  (fully discriminating requires an out-of-band anchor, not taken).
- A non-comment prose line inside an entry body carrying an algo-shaped
  dash-base64 token is indistinguishable from material and fails closed.
- The material value class requires 4+ base64ish chars, so a shorter fake
  hash stays quiet.
- Empty-scalar keys and `@zkochan/` entries (pnpm's own, published
  without attestations) are deliberate silent skips.
- The duplicate-conflict report counts pairs, not distinct identities.
- The zero-parse guard's reach signal is substring-sensitive to a
  mid-line `resolution:` mention inside some block; the ownership guard
  catches every realistic drift behind that.
- Git/tarball dependencies are unsupported: if one ever appears, the gate
  fails loudly and the decision is taken at that moment.
- A non-`RuntimeError` from the metadata layer would crash the sweep
  (unreachable today: every raise site raises the hierarchy).

### Change history

- 2026-09-25 — classified sweep failure report: exception hierarchy at
  the raise sites, bucketed grouping with scope and the hostile-shape
  disposition line; CI-alternates dispositions recorded (df1037d).
- 2026-09-25 — advisory sweep: algorithm check hoisted before payload
  decode (named on every non-sha512 path); orphan inline-material guard
  (shape-anchored); sigstore skip messages name the deciding classifier;
  conditional-assertion tests replaced with deterministic pins (df1037d).
- 2026-09-25 — single-pass scan (`LockfileScan`); unaccounted-resolution-
  block guard; duplicate-identity guard folded into the scan;
  `_conflicting_duplicates` deleted (dc8e046).
- 2026-09-23 — class-based Sigstore classification (genuine
  `InvalidBundle` isinstance; fail-closed name-fallback boundary);
  aggregate zero-verified guard (7e1d0fe).
- 2026-09-22 — conflicting-duplicate-identity guard (5b3256b); nested
  shape validation of the registry answer (4bc9b0b).
- 2026-09-21 — fail-open paths closed: partial drift, unclassified
  errors, transport-vs-gap, prose false positives (c9e08a4, 82c2904).
- 2026-09-19 — scoped packages parse; coverage guards; per-entry block
  matching replaces the fixed window (59288d1, 47281f1, b8ff85e,
  f013c3a).
- 2026-08 — gate introduced as a CI job (da93feb); quality gate extended
  to cover `scripts/*.py` (140654f).

---

## Application & script security hardening

Security-relevant changes to Analecta's own code and build tooling — not
dependency advisories — are recorded here: this file is their record
(CHANGELOG carries user-visible changes only, by policy; dependency updates
are never registered in it). Newest first; each date is the release that
carried the change.

### 2026-09-18 — release-age window (cooldown) reduced from 10 to 4 days (unreleased)

- **What changed:** the minimum release age enforced by the age-gated
  dependency updater (`scripts/deps_update.py`'s `COOLDOWN_DAYS`, the
  `--help` default text, and `deps-update.yml`'s `workflow_dispatch` input
  default and `${COOLDOWN:-…}` shell fallback) was reduced from 10 days to
  4 days, along with every live policy statement in
  `docs/github-actions-security.md` (Control 7, the Control 10 coverage
  table, the Maintenance Checklist), `docs/dependency-verification.md`, and
  `docs/syntax-highlighting.md`. The window is enforced at both resolution
  and frozen-install: pnpm's `minimumReleaseAge` (5760 minutes) rejects
  too-fresh resolutions for all dependencies including transitive ones, and
  a frozen install fails loudly on an entry that violates it.
- **A deliberate relaxation, not a drift:** decided explicitly on
  2026-09-18 as a tradeoff between supply-chain protection depth and update
  freshness — a control was intentionally weakened, not lost. The
  exception-approval process around the gate (Control 7) is unchanged:
  merging a package before its window still requires explicit maintainer
  approval; genuine early adoption is never silent.
- **Dependabot gap closed in the same change:** `.github/dependabot.yml`
  now configures a native `cooldown: default-days: 4` on the
  `github-actions` update block. As before, Dependabot never applies a
  cooldown to security updates, so the manual release-date check before
  merging a Dependabot PR (Maintenance Checklist step 5) remains required.

### 2026-08-13 — `scripts/deps_update.py` GitHub Actions log-injection hardening (release 0.5.2)

- `scripts/deps_update.py`: two GitHub Actions `::error::` prints (`_record_error()`, and `_resync_node_modules()`'s own) carried raw, unsanitized subprocess-derived text, unlike the PR-body path which already ran the same text through `_sanitize_reason()`. An embedded newline in multi-line stderr (routine for pnpm's `ERR_PNPM_*` blocks) could put a later line at the start of its own log line, letting it be parsed as an unrelated Actions workflow command (e.g. `::stop-commands::`) instead of inert log text. Both now collapse newlines the same way before printing, with a much wider limit than the PR body's markdown-table-cell truncation — only the newline-collapsing was ever the point.

### 2026-07-28 — extraction & reading-view privacy hardening (release 0.4.0)

- Extraction requests no longer identify Analecta or its maintainer to the sites they fetch — the previous User-Agent embedded a personal GitHub URL on every request. Requests now present as a generic, current Chrome on Linux, with a coherent header set (client hints, fetch metadata) to match, single-sourced from Electron's own bundled Chromium version so it can't go stale or drift from the browser Analecta actually ships with. See `docs/privacy.md`.
- Every URL the extraction pipeline fetches directly — the submitted URL, any redirect target encountered while fetching it, and remote image URLs discovered in already-fetched page content — is now resolved and validated before the request goes out: only `http(s)` schemes are allowed, the pipeline resolves the host itself, rejects the fetch if any resolved address isn't allocated for public use (loopback, link-local, private including RFC 1918 and CGNAT, reserved, unspecified, benchmarking/documentation ranges, or multicast — including an internal IPv4 address embedded in an IPv4-mapped, NAT64, or deprecated IPv4-compatible IPv6 address), and connects directly to one of the validated addresses it resolved rather than re-resolving the hostname for the connection — closing both a hostname string that encodes a blocked address in a form a naive check wouldn't parse (e.g. decimal/hex/octal IPv4) and a hostname whose DNS answer changes between the check and the connection. A resolved address that refuses or times out the connection falls back to the next validated address for that same hostname, so a dual-stack site isn't broken by one unreachable address family. TLS certificate verification still targets the original hostname. No such validation previously existed for this fetch. See `docs/electron-shell-security.md` § 7.
- A remote image that fails to download (network error, or a non-image response) now gets one retry and, if that also fails, is replaced with a local placeholder instead of keeping the original remote URL — a preserved URL would re-fetch, and re-expose the reading IP, every time the entry was reopened. A new "Localize remote images" action in Settings → Maintenance backfills any entries already saved with a live remote image reference from before this fix.
- The reading view's Content Security Policy no longer permits loading images from arbitrary remote (`https:`) hosts — only local vault assets and inline data. Since extraction already localizes every image, this closes off the one remaining path (a hand-edited or otherwise unusual entry) by which a remote image reference could silently re-fetch and expose the reading IP.
