# Socket Security — Analecta

Catalog of triaged Socket alerts, false-positive patterns, dismissed Dependabot alerts, and resolved CVEs for the Analecta project.

Referenced by `docs/github-actions-security.md` Controls 9 and 12.

---

## Setup

- **Organization:** E-zequiel
- **Plan:** Free tier
- **CLI version:** `socket@1.1.99` — locked as a devDependency in `pnpm-lock.yaml` (SHA-512 verified)
- **BSM secret:** `SOCKET_SECURITY_API_TOKEN`
- **Org slug:** `Ezequiel` — must be passed explicitly as `--org Ezequiel` in all CLI calls. Without it the CLI enters an interactive org-discovery prompt, auto-selects the org in non-TTY, then exits with code 0 without running any scan (silent failure)
- **`--no-interactive`:** required in all non-TTY contexts (CI, workflow_dispatch) for the same reason
- **Recently Published threshold:** 7 days — covers the highest-risk supply-chain window without excessive noise on dep-update PRs
- **Quota:** 500 API calls/hour (free tier). Do not add Socket to `check.sh` — it runs too frequently

**Local manual scan (no key on disk):**
```bash
bws run -- pnpm exec socket scan create . --json
```

---

## CI Integration

Defined in `.github/workflows/ci.yml` (`socket` job) and `.github/workflows/release.yml`.

- `ci.yml`: triggers on PRs with `pnpm-lock.yaml` or `backend/uv.lock` changes. Runs `pnpm exec socket ci --org Ezequiel --no-interactive`.
- `release.yml`: unconditional, runs before build on every version tag. Uses `socket scan create . --json --no-interactive --org Ezequiel` (not `socket ci` — that subcommand requires PR context).
- `socket-manual.yml`: `workflow_dispatch` for on-demand scans against any branch ref. Dispatch on `main`, set `ref` input to the target branch.

**Free plan limitation:** Socket only posts PR comments — it cannot block merges. Acceptable for solo-dev workflow.

---

## Known false positives (set to "Ignore" in dashboard)

### npm — Obfuscated code (false-positive pattern)

Socket's "Obfuscated code" detector flags packages that use split operations on large strings as an encoding optimization for lookup tables. This is not malware — it is a space-saving technique for static lookup data (HTML entities, tokenizer rules, compiler tables). Standard triage: check Socket's own analyst note; if no network exfiltration, eval injection, or credential access is identified → Ignore.

| Alert ID | Package | Reason |
|----------|---------|--------|
| SOCKET-EZEQUIEL-2 | `entities@4.5.0` | HTML entity lookup tables encoded as compact strings |
| SOCKET-EZEQUIEL-3 | `markdown-it@14.1.1` | Syntax/tokenizer rule tables |
| SOCKET-EZEQUIEL-5 | `svelte@5.55.7` | Compiler/runtime lookup tables |
| 2026-05-30 scan | `linkedom@0.18.12` (`package/worker.js`) | Web Worker DOM lookup tables — identical pattern. Optional dep of `defuddle@0.19.1` (root `package.json` devDependency — a diagnostic-only tool, never a shipped runtime dep, see `docs/defuddle-decision.md`); Web Worker path is unused in the offline diagnostic script that consumes it. |
| 2026-06-07 scan | `commander@9.5.0` | Transitive of electron-builder. Socket analyst: "conventional, non-obfuscated CLI framework component." 200M+ weekly downloads. |
| 2026-06-07 scan | `cssom@0.5.0` | Standard CSS parser. Socket analyst: "no evidence of malicious behavior." Minified lookup tables. |
| 2026-06-07 scan | `tiny-async-pool@1.3.0` | Standard concurrency utility. Socket analyst: "no evidence of malicious behavior, no hardcoded secrets." |
| 2026-06-07 scan | `electron-winstaller@5.4.0` (`wix.dll`) | Compiled Windows binary (WiX toolset). Binary DLLs always appear obfuscated to JS scanners. Windows-only; irrelevant to Linux-only build target. Lifecycle scripts blocked via `allowBuilds`. |
| 2026-06-07 scan | `graphology@0.26.0` (`specs/read.js`) | Direct dep (VaultGraph). Flagged file is a test spec. Socket analyst: "legitimate unit-test suite." |
| 2026-06-07 scan | `htmlparser2@10.1.0` | Standard HTML/XML tokenizer. 100M+ weekly downloads. Socket analyst: "non-malicious, standard tokenizer." |
| 2026-06-07 scan | `@typescript-eslint/eslint-plugin@8.60.0` | Dev dep, linting only. Official typescript-eslint org. |
| 2026-06-15 scan (16 alerts) | `nodejs-wheel-binaries@24.15.0` (PyPI) | Transitive of `basedpyright` (dev-only). Ships the compiled `node` binary across ~8 platform wheels; each binary flagged independently — same false-positive class as `electron-winstaller@wix.dll`. Confirmed absent from the shipped PyInstaller `--onedir` artifact (`backend.spec` has no `basedpyright`/`nodejs` references). |

### PyPI — Removed packages on npm-only PRs

When a PR triggers the `socket` CI job via `pnpm-lock.yaml` changes only (no `backend/uv.lock` change), Socket's `ci` diff command compares the full repo against its previous `main` baseline. Python packages that were indexed in the baseline but fall outside the PR's diff scope are reported as "Removed."

**Action:** none. This is a diff-session artifact, not a real removal. Confirm by checking whether `backend/uv.lock` changed in the PR.

### PyPI — Alert type false positives (2026-05-21 baseline)

These alert types on these library categories are expected behaviors, not malicious activity:

| Alert type | Packages | Why it fires / Why it's expected |
|------------|----------|----------------------------------|
| `filesystemAccess`, `shellAccess` | `trafilatura`, `readability-lxml`, `uvicorn`, `ruff`, `pyyaml` | Web scrapers write temp files; web servers bind sockets; linters exec subprocesses |
| `networkAccess` | `uvicorn`, `youtube-transcript-api` | Web server binds ports; YouTube client makes HTTP requests — by design |
| `usesEval` | `readability-lxml`, `sse-starlette`, `ruff` | Readability heuristics; SSE serialization; Ruff processes arbitrary Python source |
| `hasNativeCode` | `pyyaml` | Ships `_yaml.cpython-*.so` C extension — expected, not injected |
| `urlStrings` | all Python libs | Static URLs in error messages or tests |
| `gptAnomaly` | `youtube-transcript-api` | Verbose exception messages with video IDs; low-confidence anomaly |
| `installScripts` | `ruff` | Rust test fixture in source tree — not an install hook |

`potentialVulnerability` on `pyyaml` (unsafe constructors): backend uses only `yaml.safe_load()` — no `yaml.load()` or `yaml.full_load()` anywhere in `backend/src/`.

### @sveltejs/kit — `potentialVulnerability` (eval in write_tsconfig.js)

Build-time tsconfig/jsconfig JSON parsing. Developer-controlled input, not user input. Not a runtime concern.

---

## Dismissed Dependabot alerts

| GHSA | Package | Reason dismissed |
|------|---------|-----------------|
| GHSA-hgv7-v322-mmgr | `@sveltejs/kit ≥2.38.0 ≤2.60.0` | `query.batch()` cross-user context merge. Not applicable: `query.batch()` not used; single-user Electron desktop app with no concurrent users and no SSR. Fix present (locked at 2.60.1). Dismissed 2026-05-21. |

---

## Maintenance alerts (no action — transitive build-tool deps)

These deprecated packages are all transitive deps of electron-builder and cannot be directly upgraded. They resolve automatically when electron-builder updates its dependency tree.

| Package | Deprecated reason |
|---------|------------------|
| `glob@7.2.3` | Old versions contain security vulns |
| `rimraf@2.6.3` | Versions prior to v4 unsupported |
| `inflight@1.0.6` | Memory leak; unsupported |
| `lodash.isequal@4.5.0` | Use `node:util` instead |
| `boolean@3.2.0` | Package no longer supported |
| `@humanfs/types@0.15.0` | `unpopularPackage` quality alert (Nicholas Zakas's package — legitimate) |

---

## Resolved CVEs

### 2026-08-28

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `@xmldom/xmldom@0.8.14` / `@xmldom/xmldom@0.9.11` | 12 GHSAs published 2026-08-21, no CVE IDs assigned yet, self-disclosed by the xmldom maintainers — surfaced via a Socket `deprecated`/maintenance alert on `0.9.11` (`"this version has critical issues, please update to the latest version"`), then cross-checked by hand against `0.8.14` too. 7 affect both the `0.8.x` (`0.7.0-0.8.14`) and `0.9.x` (`0.9.0-0.9.11`) lines: GHSA-c7q8-3ch8-vqpv (processing-instruction target injection bypasses `requireWellFormed`), GHSA-965w-775f-mr7g (quadratic-memory consumption), GHSA-6gmq-8vp8-gcm6 (XML fragment injection via `EntityReference.nodeName` during `requireWellFormed` serialization), GHSA-8344-3jmq-59r6 (quadratic-time attribute deduplication, CWE-407), GHSA-93r5-fhx6-vmg9 (quadratic-time parsing via the malformed-input recovery path, CWE-407), GHSA-27p8-2357-5qqv (DocType `name` injection bypasses `requireWellFormed`), GHSA-6h8r-xr42-gp59 (parser silently accepts a not-well-formed end tag followed by a line break and trailing content). 1 affects only `0.8.x`: GHSA-x4fp-j954-r2f4 (ReDoS in the `0.8.x` end-tag whitespace-trim regex, CWE-1333). 4 affect only `0.9.x`: GHSA-6mj3-qw4j-hgrw (HTML raw-text closing-tag case mismatch causes O(n²) output amplification), GHSA-3px3-54cx-rmw9 (Name/QName validation bypassable via an embedded line terminator), GHSA-vr34-hp96-76pp (DocType `publicId`/`systemId` validation bypass via line terminator, `0.9.10-0.9.11` only), GHSA-jxjr-3g7g-3944 (element/attribute name validation bypass via line terminator, `0.9.11` only). Fixed at `0.8.15` and `0.9.12` respectively — both confirmed non-deprecated. | Same two version-scoped `overrides:` entries in `pnpm-workspace.yaml`, bumped in place: `'plist@3.1.0>@xmldom/xmldom': '0.8.15'` and `'plist@3.1.1>@xmldom/xmldom': '0.9.12'` (plus `'mathml-to-latex@1.8.0>@xmldom/xmldom': '0.9.12'`). **Re-verified, not assumed:** the 2026-08-13 finding that `plist@3.1.0` (no `mimeType` arg to `parseFromString`) is incompatible with xmldom's `0.9.x` strict-mimeType check still holds, so the two branches stay unmerged. Ran an isolated install (`plist@3.1.0` + `@xmldom/xmldom@0.8.15` override) and called `plist.parse()`/`plist.build()` on a sample `Info.plist` — both succeeded, confirming this batch's `requireWellFormed` hardening didn't also tighten `0.8.x`'s mimeType handling. **Considered and rejected:** forcing a global `plist: '3.1.1'` override to collapse both branches and drop the `0.8.x` pin — mechanically viable (would satisfy `@electron/osx-sign`'s `^3.0.5` and `@electron/universal`'s `^3.1.0` ranges), but buys zero extra security since `0.8.x` already has a clean patch, at the cost of forcing a resolution over `app-builder-lib`'s own exact `"3.1.0"` pin — unchanged from `26.15.1` through the `27.0.0` alpha line as of this writing — on a code path already established as unreachable (see Reachability below, carried over unchanged from 2026-08-13). **Cooldown exception:** both `0.8.15` and `0.9.12` released 2026-08-21, 7 days before this bump (3 short of the 10-day window) — approved explicitly despite no CVE ID/CVSS score yet, given the GitHub-rated "high" severity on most of the 12 advisories and the upstream deprecation notice on both superseded versions. |

### 2026-08-13

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `nanoid@3.3.17` | CVE-2026-67213 / GHSA-2v37-7h3g-55p8 (CVSS 8.2 HIGH) | **Corrects the 2026-08-12 entry below.** `3.3.17` (adopted then) is still inside the advisory's own vulnerable range (`< 3.3.18`) — confirmed via the GHSA page itself ("Patched versions: 3.3.18"), Socket's own CSV export (`firstPatchedVersionIdentifier: "3.3.18"`), and npm registry timestamps (`3.3.17` published 2026-08-03, `3.3.18` published 2026-08-07 — distinct, later release). The 2026-08-12 write-up's reachability analysis was correct (`postcss@8.5.23`, build-time only, never calls `customAlphabet`/`customRandom`); only the adopted version number was wrong. `overrides: {nanoid: '3.3.18'}` in `pnpm-workspace.yaml`. **Cooldown exception:** `3.3.18` released 2026-08-07, 6 days before this bump (4 short of the 10-day window, a larger exception than the original) — approved explicitly given the unchanged CVSS 8.2 rating. |
| `@xmldom/xmldom@0.8.13` / `@xmldom/xmldom@0.9.10` | GHSA-w2rr-34g9-rvrj (CVSS 8.7, `createElement()` doesn't validate the element name — a crafted name survives serialization and injects extra attributes/event handlers), GHSA-4w3w-2rp5-g8jm (CVSS 8.7, same injection class via `setAttribute()` bypassing the name validation `createAttribute()` enforces) — both affect `0.7.0-0.8.13` and `0.9.0-0.9.10`, and neither is caught by `requireWellFormed: true`, previously the recommended mitigation. Plus GHSA-g53g-w8rj-fmg7 (CVSS 8.7, `0.9.0-beta.9-0.9.10` only — quadratic-time backtracking parsing an unterminated `<?` processing instruction, stalls the event loop on untrusted XML; the `0.8.x` line was never affected, different bounded parser). All three fixed at `0.8.14` and `0.9.11`. | Two version-scoped `overrides:` entries in `pnpm-workspace.yaml`, deliberately **not unified to one version**: `'plist@3.1.0>@xmldom/xmldom': '0.8.14'` and `'plist@3.1.1>@xmldom/xmldom': '0.9.11'` (plus `'mathml-to-latex@1.8.0>@xmldom/xmldom': '0.9.11'` for the unrelated `defuddle` branch, same target version). Verified empirically that unifying would break `plist@3.1.0`: downloaded and diffed both versions' tarballs — the only functional difference between `plist@3.1.0` and `3.1.1` is that `3.1.1`'s `lib/parse.js` added an explicit `"text/xml"` second argument to `DOMParser.parseFromString()`, while `3.1.0` still calls it with none. Reading `xmldom@0.9.11`'s own `dom-parser.js`/`conventions.js` confirms `isValidMimeType(undefined)` is `false`, so `parseFromString` throws a `TypeError` when called without a mimeType — forcing `0.9.11` onto the `3.1.0` branch would break `plist.parse()` outright, not just risk an incompatibility. **Reachability:** both `plist` branches are transitive via `app-builder-lib` (electron-builder); grepping its compiled output shows `plist` is only required from `electronMac.js`, `targets/pkg.js` (macOS `.pkg` target), and `LibUiFramework.js` (an Electron-alternative framework Analecta doesn't configure) — none of those run when packaging `.deb`/`.rpm`/`.AppImage`, Analecta's only build targets. The `mathml-to-latex` branch is a `defuddle` dependency (root `package.json` devDependency, dev-only diagnostic tool, never a shipped runtime dep — see `docs/defuddle-decision.md`). **Cooldown exception, the largest in this project's history:** both `0.8.14` and `0.9.11` released 2026-08-12, 1 day before this bump (9 short of the 10-day window) — approved explicitly given the CVSS 8.7 rating despite the non-reachability above. |

### 2026-08-12

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `js-yaml@4.3.0` | GHSA-5p4m-2wfm-xmqj (no CVE assigned; CVSS 7.5 HIGH, quadratic-time DoS in `!!omap` resolution — the CVE-2026-59870 fix from the 5.x line, never backported to 4.x) | `overrides: {js-yaml: '4.3.1'}` in `pnpm-workspace.yaml`. Transitive via `electron-builder`/`dmg-builder`/`app-builder-lib` (build-time tooling only) **and** `electron-updater` (a real runtime dependency — parses `latest-linux.yml` fetched from GitHub Releases when checking for updates). Runtime-reachable, but low practical severity despite the CVSS score: that YAML comes from Analecta's own release feed over HTTPS with SHA-512 verification, not attacker-controlled input — worst case is an updater hang, not compromise. |
| `nanoid@3.3.16` | CVE-2026-67213 / GHSA-2v37-7h3g-55p8 (CVSS 8.2 HIGH, infinite loop in `customAlphabet`/`customRandom` when called with `size: 0`) | `overrides: {nanoid: '3.3.17'}` in `pnpm-workspace.yaml`. **Cooldown exception:** `3.3.17` released 2026-08-03, 9 days before this bump (1 short of the 10-day window) — approved explicitly given the CVSS 8.2 rating; EPSS is 0.003 and the only consumer in this tree is `postcss@8.5.23` (build-time CSS tooling), which never calls either custom-generator function, let alone with `size: 0` — not reachable regardless. |
| `@sveltejs/kit@2.70.1` | CVE-2026-66062 / GHSA-29g2-3rmr-qm68 (CVSS 5.3 MODERATE, ReDoS in `Accept`-header content negotiation) | Bumped to `2.70.2` via Dependabot PR #82 (squash-merged 2026-08-12). Direct `devDependency`; not reachable in the packaged app — `@sveltejs/adapter-static` means the vulnerable server-side content-negotiation code never ships, present only in the local `pnpm dev` dev server. |

### 2026-08-03

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `brace-expansion@1.1.16` / `2.1.2` / `5.0.8` | GHSA-rgw5-rvv9-x895 (CVE-2026-69152, HIGH 7.5) — bypasses the `maxLength` mitigation `5.0.8` shipped for GHSA-mh99-v99m-4gvg: two intermediate arrays (`values` in `expand_()`, and `expandSequence()`'s padded-sequence output) were never bounded by `maxLength`, so a ~25 KB input can still OOM-crash the process, and a ~400 KB input can stall the event loop for minutes. Fixed upstream by bounding both. | `overrides: {'brace-expansion@1': '1.1.18', 'brace-expansion@2': '2.1.4', 'brace-expansion@5': '5.0.9'}` in `pnpm-workspace.yaml`. **Cooldown exception:** all three released 2026-07-30, 4 days before this bump (6 short of the 10-day window) — approved explicitly given the CVSS 7.5 rating; EPSS is 0.003 and exposure is build-time tooling on our own glob patterns, not attacker-controlled input, so real-world urgency was low. **Retires the 2026-07-27 residual-risk carve-out below:** that entry assumed no 1.x/2.x backport existed for GHSA-mh99-v99m-4gvg; `1.1.17`/`2.1.3` were published afterward, and this bump adopts their successors (`1.1.18`/`2.1.4`) directly, so both the original CVE and its bypass are closed on all three lines. `dist.integrity` cross-checked against the npm registry and `pnpm view` for all three versions before adoption. |
| `fast-uri@3.1.4` (via `ajv@8.20.0`) | GHSA-7p8r-x3mc-p8w7 (CVE-2026-18446, HIGH 7.5) — `\\`/`/\`/`\/` authority introducer parsed as no-authority (folds into path) instead of matching Node's native WHATWG `URL` behavior (used by `fetch()`/`undici`/`http`), which treats `\` as interchangeable with `/` for special schemes. Policy/parser desync for anything using `fast-uri` to enforce host-based rules ahead of a WHATWG-URL consumer. | `overrides: {'fast-uri': '3.1.5'}`. **Cooldown exception:** released 2026-07-31, 3 days before this bump — approved explicitly given the CVSS 7.5 host-confusion rating. Verified two ways beyond hash-check: export shape unchanged (`ajv`'s `require("fast-uri")` call gets the same `.parse`/`.resolve`/etc. shape), and the fix itself confirmed empirically — feeding the advisory's exact PoC (`\\evil.com/path`) now throws `"URI authority must not contain a literal backslash"` instead of silently mis-parsing it. |
| `undici@7.28.0` (`@electron/get`) / `undici@6.27.0` (`node-gyp`) | 5 CVEs at once, all patched at `7.29.0`: CVE-2026-13697/GHSA-4cwx-7wf7-3272 (HIGH 7.4, cross-user shared-cache disclosure + parse-time crash via degenerate `private` cache-control directives), CVE-2026-16728/GHSA-8xcm-r25x-g524 (MODERATE 4.8, response desync via `interceptors.retry()` serving a stale `Content-Length`), CVE-2026-16729/GHSA-v3r7-h72x-cjcm (MODERATE 4.8, cookie injection via unsanitized `setCookie` domain/`unparsed` fields), CVE-2026-14643/GHSA-jr45-8vmc-qm54 (MODERATE 5.9, cache-control whitespace-around-`=` parse bypass letting authenticated responses land in shared cache), CVE-2026-15157/GHSA-m8rv-5g2x-5cg5 (MODERATE 4.2, CRLF injection via a duck-typed blob's `.type` property). | `overrides: {undici: '7.29.0'}` — **unified from the prior split override** (`@electron/get>undici: 7.28.0` / `node-gyp>undici: 6.27.0`). `node-gyp`'s own `package.json` still declares `"undici": "^6.25.0"` (7.x is outside its stated semver range), so this was verified empirically before unifying rather than assumed safe: installed `7.29.0`, required it from `node-gyp`'s own dependency path, and replayed `lib/download.js`'s exact calls (`new RetryAgent(new Agent(), {maxRetries: 3})`, `new RetryAgent(new EnvHttpProxyAgent(opts), {maxRetries: 3})`, then a real `fetch()` through that dispatcher against a live URL) — all succeeded. `pnpm why undici` confirms a single resolved version across both consumers. No cooldown exception needed: both `7.29.0` and `6.28.0` (the version that would have covered `node-gyp` under the old split) were released 2026-07-24, exactly 10 days before this bump. |

### 2026-07-27

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `tar@7.5.16` | GHSA-r292-9mhp-454m (MODERATE 5.3, `mapHas`/`filesFilter` stack-overflow DoS) + 4 earlier CVEs it supersedes (GHSA-23hp-3jrh-7fpw CRITICAL 9.2 gzip-bomb DoS, GHSA-8x88-c5mf-7j5w HIGH 8.7 `replace()` infinite loop, GHSA-gvwx-54wh-qm9j MODERATE 5.3 PAX NUL-byte uncaught exception, GHSA-w8wr-v893-vjvp MODERATE 5.3 PAX numeric-path type confusion) | `overrides: {tar: '7.5.21'}` in `pnpm-workspace.yaml` |
| `fast-uri@3.1.2` (via `ajv@8.20.0`) | GHSA-v2hh-gcrm-f6hx (HIGH 7.5, backslash authority-delimiter host confusion), GHSA-4c8g-83qw-93j6 (HIGH 7.5, failed IDN canonicalization host confusion) | `overrides: {'fast-uri': '3.1.4'}` in `pnpm-workspace.yaml` |
| `brace-expansion@1.1.14` / `2.1.0` / `5.0.6` | GHSA-mh99-v99m-4gvg (HIGH 7.5, unbounded expansion length OOM), GHSA-3jxr-9vmj-r5cp (HIGH 7.7, exponential-time DoS) | Scoped per major line, **not a single blanket bump**: `overrides: {'brace-expansion@1': '1.1.16', 'brace-expansion@2': '2.1.2', 'brace-expansion@5': '5.0.8'}`. Verified empirically (real `minimatch` code, real brace-expansion tarballs, isolated `node_modules`) that forcing `5.0.8` onto the `minimatch@3.1.5`/`5.1.9`/`9.0.9` consumer lines throws `TypeError: expand is not a function` — brace-expansion@5.x rebuilt as a named-export-only CJS module via `tshy` (`exports.expand = expand`), while those minimatch versions call the old default-callable export directly (`require('brace-expansion')(...)`). Confirmed the inverse too: 1.1.16/2.1.2/5.0.8 paired with their own matching minimatch line all resolve correctly. **Residual risk, accepted 2026-07-27:** GHSA-mh99-v99m-4gvg has no backport to the 1.x/2.x lines — downloaded and grepped both `1.1.16` and `2.1.2`, neither contains the `EXPANSION_MAX_LENGTH` bound that `5.0.8` has. `minimatch@3.1.5` (→ `glob@7.2.3` → `@electron/asar`, `dir-compare`) and `minimatch@5.1.9`/`9.0.9` (→ `filelist`, `@electron/universal`) stay exposed to it. Accepted because both are electron-builder build-time tooling operating on our own glob patterns, not attacker-controlled input — a hand-maintained `pnpm patch` backport was considered and rejected as worse than the documented residual for a non-attacker-reachable DoS. |
| `postcss@8.5.17` | GHSA-r28c-9q8g-f849 (HIGH 7.5, `sourceMappingURL` path traversal → arbitrary `.map` file disclosure), GHSA-fxqj-rqcc-2cmp | `overrides: {postcss: '8.5.23'}` — 10-day window exception approved given severity (8.5.23 was 3 days old at merge). |
| `setuptools@82.0.1` (via `pyinstaller`, unconstrained range) | GHSA-h35f-9h28-mq5c (MODERATE 6.1, `MANIFEST.in` exclusion bypass in sdist builds via NFC/NFD Unicode normalization collision on macOS APFS/HFS+) | `[tool.uv] constraint-dependencies = ["setuptools>=83.0.0"]` in `backend/pyproject.toml` |
| `electron-builder@26.15.3` | — (routine patch bump, not Socket-flagged; bumped ahead of the brace-expansion investigation since it could have shifted `@electron/asar`/`@electron/universal`'s dependency tree — it didn't) | Upgraded to `26.15.6` in `electron/package.json`. Not exercised by `check.sh` (packaging step is skipped there — see "Task workflow" note in project docs); verified via `pnpm install` + `tsc --noEmit` only. Real packaging is exercised by the release workflow. |

### 2026-07-13

| Package | CVE | Fix |
|---------|-----|-----|
| `soupsieve@2.8.3` | GHSA-2wc2-fm75-p42x (CVSS 7.5 HIGH, memory exhaustion via large comma-separated CSS selector lists), GHSA-836r-79rf-4m37 (CVSS 7.5 HIGH, ReDoS in the attribute-value regex) | `[tool.uv] constraint-dependencies = ["soupsieve>=2.8.4"]` in `backend/pyproject.toml`. Architecture note: Analecta's own code never calls `.select()`/`.select_one()`/`soupsieve.compile()` (confirmed via grep across `backend/src/`, `readability-lxml`, `trafilatura`) — the vulnerable input is the *selector string*, which is always hardcoded, never attacker-controlled. Low exploitability; fixed anyway since the patch is free (2.8.3 → 2.8.4, no functional change). |
| `lxml-html-clean@0.4.4` | GHSA-4jhm-jv67-739f (CVSS 8.2 HIGH, `Cleaner` does not strip `javascript:` URLs from `xlink:href` when `safe_attrs_only=False`) | `[tool.uv] constraint-dependencies = ["lxml-html-clean>=0.4.5"]` in `backend/pyproject.toml`. Unlike soupsieve above, the vulnerable configuration is confirmed live: `readability-lxml` (a direct extraction dependency, transitively pulling `lxml[html-clean]`) calls `Cleaner(..., safe_attrs_only=False, ...)` in `readability/cleaners.py`, on HTML fetched from arbitrary user-supplied URLs. Frontend's `markdown-it` is configured with `html: false` (`frontend/src/lib/markdown/renderer.ts`), which happens to keep any surviving payload from executing in the reading view — that's an incidental downstream mitigation, not a substitute for the fix. |
| `@emnapi/runtime@1.11.1` | — (Socket `obfuscatedFile`/`supplyChainRisk`, no CVE; confidence 0.9 on `package/dist/emnapi.min.mjs`) | `overrides: {'@emnapi/runtime': '1.11.2'}` in `pnpm-workspace.yaml`. Socket's own analyst note found no malicious behavior — dynamic `Function` use limited to environment capability probing, same shape as other WASM-runtime false positives in this catalog. 1.11.2 doesn't reproduce the flag. Transitive via `@rolldown/binding-wasm32-wasi` (optional WASM fallback binding for Vite's Rolldown bundler). Same resolution pattern as the `js-yaml@4.2.0` entry below (2026-07-03): a version bump clears the flag rather than a permanent "Ignore." |

### 2026-07-03

| Package | CVE | Fix |
|---------|-----|-----|
| `js-yaml@4.2.0` | GHSA-52cp-r559-cp3m (CVSS 7.5, quadratic-time DoS via chained merge-key mappings) | `overrides: {js-yaml: '4.3.0'}` — 10-day window exception approved given severity (4.3.0 was ~7 days old at merge). Cleared the `obfuscatedFile`/`supplyChainRisk` "monitor" alert on the previous 4.2.0 minified bundle (formerly documented in "Known false positives" — removed, alert no longer present post-upgrade). |

### 2026-06-19

| Package | CVE | Fix |
|---------|-----|-----|
| `electron-builder@26.8.1` | CVE-2026-54672 (CVSS 7.8 HIGH — AppImage `LD_LIBRARY_PATH` misconfiguration) | Upgraded to `26.15.3` in `electron/package.json` |
| `electron-updater@6.8.3` | — (routine patch) | Upgraded to `6.8.9` |

### 2026-06-18

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `undici@7.25.0` (via `@electron/get`) | CVE-2026-9678 (MODERATE 5.9, shared-cache whitespace bypass); CVE-2026-9697 (HIGH 7.4, SOCKS5 `requestTls` TLS bypass) | `'@electron/get>undici': '7.28.0'` in `pnpm-workspace.yaml` |
| `undici@6.25.0` (via `node-gyp`) | GHSA-g8m3-5g58-fq7m, GHSA-vxpw-j846-p89q, GHSA-p88m-4jfj-68fv, GHSA-35p6-xmwp-9g52 | `'node-gyp>undici': '6.27.0'` in `pnpm-workspace.yaml` |
| `node-gyp@12.3.0` (via `@electron/rebuild`) | — (opportunistic bump) | `node-gyp: '12.4.0'` in `pnpm-workspace.yaml` |

### 2026-06-16

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `starlette@1.0.1` | CVE-2026-54283 (HIGH 7.5), CVE-2026-48818 (HIGH 7.5 Windows-only), CVE-2026-48817 (MODERATE 5.3), CVE-2026-54282 (LOW 3.7) | `[tool.uv] constraint-dependencies = ["starlette>=1.3.1"]` in `backend/pyproject.toml` |
| `markdown-it@14.1.1` | CVE-2026-48988 (MODERATE 5.3, smartquotes DoS) | `pnpm add markdown-it@14.2.0 --save-exact` |

### 2026-06-08

| Package | CVE | Fix |
|---------|-----|-----|
| `cookie@0.6.0` (via `@sveltejs/kit`) | CVE-2024-47764 (accept-splitting) | `overrides: {cookie: '0.7.0'}` in `pnpm-workspace.yaml` |

### 2026-05-30

| Package | CVE | Fix |
|---------|-----|-----|
| `tmp@0.2.5` (via `tmp-promise` ← `@malept/flatpak-bundler` ← electron-builder) | CVE-2026-44705 (CVSS 7.7, path traversal) | `overrides: {tmp: '0.2.7'}` in `pnpm-workspace.yaml` |

### 2026-05-20 (E1)

| Package | CVEs | Fix |
|---------|------|-----|
| `svelte@5.55.5` | CVE-2026-42567 (ReDoS), GHSA-f3cj-j4f6-wq85 (XSS hydratable), CVE-2026-42573 (DOM clobbering XSS), CVE-2026-42599 (spread attr XSS) | Updated to `5.55.8` |
| `devalue@5.8.0` | CVE-2026-42570 (HIGH 7.5, DoS sparse array) | Updated to `5.8.1` via `@sveltejs/kit@2.60.1` |

### 2026-06-15 (js-yaml, form-data, tar, vite)

| Package | CVE | Fix |
|---------|-----|-----|
| `js-yaml@4.1.1` | CVE-2026-53550 (CVSS 5.3, quadratic DoS on attacker-supplied YAML) | `overrides: {js-yaml: '4.2.0'}` |
| `form-data@4.0.5` | CVE-2026-12143 (CVSS 8.7, CRLF injection via untrusted field names) | `overrides: {form-data: '4.0.6'}` |
| `tar@7.5.15` | CVE-2026-53655 (CVSS 6.9, PAX header differential) | `overrides: {tar: '7.5.16'}` |
| `vite@8.0.12` | CVE-2026-53571 (CVSS 8.2, Windows NTFS bypass), CVE-2026-53632 (CVSS 5.5, Windows NTLM) | `overrides: {vite: '8.0.16'}` (devDep; zero Linux runtime risk) |
