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

Defined in `.github/workflows/ci.yml` (`socket` job) and
`.github/workflows/release.yml`.

- `ci.yml`: triggers on PRs with `pnpm-lock.yaml` or `backend/uv.lock`
  changes. Runs
  `pnpm exec socket ci --org Ezequiel --no-interactive`.
- `release.yml`: unconditional, runs before build on every version tag.
  Uses `socket scan create . --json --no-interactive --org Ezequiel`
  (not `socket ci` — that subcommand requires PR context).
- `socket-manual.yml`: `workflow_dispatch` for on-demand scans against any
  branch ref. Dispatch on `main`, set `ref` input to the target branch.

**Free plan limitation:** Socket only posts PR comments — it cannot block
merges. Acceptable for solo-dev workflow.

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
are never registered in it). Newest first; each heading carries the
change date and the release that shipped it.

### 2026-09-18 — release-age window (cooldown) reduced from 10 to 4 days (release 0.5.4)

- **What:** the updater's minimum release age was reduced from 10 days to
  4 days (`scripts/deps_update.py`'s `COOLDOWN_DAYS`, the `--help` default
  text, and `deps-update.yml`'s input default and shell fallback) and a
  native `cooldown: default-days: 4` was configured in
  `.github/dependabot.yml` (0e58186). The window is enforced at both
  resolution and frozen-install through pnpm's `minimumReleaseAge: 5760`
  (23faf93).
- **Why:** a deliberate tradeoff between supply-chain protection depth and
  update freshness, not drift. The exception-approval process around the
  gate is unchanged: early adoption still requires explicit maintainer
  approval and is never silent. Live policy statements: Control 7 and the
  Maintenance Checklist in `docs/github-actions-security.md`, and
  `docs/dependency-verification.md`.

### 2026-08-13 — `scripts/deps_update.py` GitHub Actions log-injection hardening (release 0.5.2)

- **What:** the two `::error::` annotation prints (`_record_error()` and
  `_resync_node_modules()`'s) now collapse embedded newlines in
  subprocess-derived text before printing, matching the PR-body path's
  `_sanitize_reason()` (1ea7352).
- **Why:** an embedded newline could put a later line at the start of its
  own log line, letting it be parsed as an unrelated Actions workflow
  command (e.g. `::stop-commands::`) instead of inert log text.

### 2026-07-28 — extraction & reading-view privacy hardening (release 0.4.0)

- **What:** extraction requests no longer identify Analecta or its
  maintainer — they present as a generic, current Chrome on Linux
  (c707ccd). Mechanism: `docs/privacy.md`.
- **What:** every URL the extraction pipeline fetches is resolved and
  validated before the request goes out — `http(s)`-only, public addresses
  only, connection pinned to the validated address (7233c13, af73a6b,
  c84135d). Full writeup: `docs/electron-shell-security.md` § 7.
- **What:** a failed remote-image download gets one retry, then a local
  placeholder instead of a preserved remote URL; the "Localize remote
  images" maintenance action backfills older entries (5229def). Mechanism:
  `docs/privacy.md`.
- **What:** the reading view's CSP `img-src` no longer permits arbitrary
  remote image hosts — local vault assets and inline data only (a6d6ff8).
  Mechanism: `docs/privacy.md`.
