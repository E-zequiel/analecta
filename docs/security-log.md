# Security Log — Analecta

Catalog of triaged dependency-security alerts — Socket alerts and false-positive patterns, dismissed Dependabot alerts, and resolved CVE/GHSA advisories across the npm and Python ecosystems — for the Analecta project.

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
| 2026-05-30 scan | `linkedom@0.18.12` (`package/worker.js`) | Web Worker DOM lookup tables — identical pattern. |
| 2026-06-07 scan | `commander@9.5.0` | Transitive of electron-builder. Socket analyst: "conventional, non-obfuscated CLI framework component." 200M+ weekly downloads. |
| 2026-06-07 scan | `cssom@0.5.0` | Standard CSS parser. Socket analyst: "no evidence of malicious behavior." Minified lookup tables. |
| 2026-06-07 scan | `tiny-async-pool@1.3.0` | Standard concurrency utility. Socket analyst: "no evidence of malicious behavior, no hardcoded secrets." |
| 2026-06-07 scan | `electron-winstaller@5.4.0` (`wix.dll`) | Compiled Windows binary (WiX toolset). Binary DLLs always appear obfuscated to JS scanners. Windows-only; irrelevant to Linux-only build target. Lifecycle scripts blocked via `allowBuilds`. |
| 2026-06-07 scan | `graphology@0.26.0` (`specs/read.js`) | Direct dep (VaultGraph). Flagged file is a test spec. Socket analyst: "legitimate unit-test suite." |
| 2026-06-07 scan | `htmlparser2@10.1.0` | Standard HTML/XML tokenizer. 100M+ weekly downloads. Socket analyst: "non-malicious, standard tokenizer." |
| 2026-06-07 scan | `@typescript-eslint/eslint-plugin@8.60.0` | Dev dep, linting only. Official typescript-eslint org. |
| 2026-06-15 scan (16 alerts) | `nodejs-wheel-binaries@24.15.0` (PyPI) | Transitive of `basedpyright` (dev-only). |

- **`linkedom@0.18.12` (`package/worker.js`):** Optional dep of `defuddle@0.19.1` (root `package.json` devDependency — a diagnostic-only tool, never a shipped runtime dep, see `docs/defuddle-decision.md`); Web Worker path is unused in the offline diagnostic script that consumes it.
- **`nodejs-wheel-binaries@24.15.0` (PyPI):** Ships the compiled `node` binary across ~8 platform wheels; each binary flagged independently — same false-positive class as `electron-winstaller@wix.dll`. Confirmed absent from the shipped PyInstaller `--onedir` artifact (`backend.spec` has no `basedpyright`/`nodejs` references).

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
| GHSA-hgv7-v322-mmgr | `@sveltejs/kit ≥2.38.0 ≤2.60.0` | `query.batch()` cross-user context merge. |

- **Not applicable:** `query.batch()` not used; single-user Electron desktop app with no concurrent users and no SSR.
- **Fix present** (locked at 2.60.1). Dismissed 2026-05-21.

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

### 2026-09-20 — Python/uv `constraint-dependencies` (`anyio`)

| Package | Advisory(ies) | Floor & rationale |
|---------|--------------|-------------------|
| `anyio@4.13.0` | GHSA-82r6-8w77-94w6 / CVE-2026-63374 (CVSS 4.0 `AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:N`, CWE-297) — `TLSStream.wrap()` resolves internationalized (non-ASCII) hostnames with IDNA 2003 instead of IDNA 2008 when constructing the `server_hostname` passed to the TLS layer (`wrap_bio()`); because the two standards can map the same Unicode label to different ASCII representations, a certificate legitimately issued for one form can validate on a connection intended for the other. The advisory scopes exploitation to connections `TLSStream.wrap()` / `connect_tcp()` established or redirected by other means to an attacker's host. Fixed in `4.14.2`. | `anyio>=4.15.1` |
| `anyio@4.13.0` | GHSA-5p39-cfhj-2xmp / CVE-2026-64847 (CVSS 4.0 `AV:L/AC:L/AT:N/PR:L/UI:N/VC:N/VI:N/VA:H`) — `anyio.to_process`/process-pool workers can block indefinitely when a worker subprocess writes to stderr before the parent drains it, an availability DoS. Fixed in `4.14.2`. | `anyio>=4.15.1` |
| `anyio@4.13.0` | GHSA-3w57-8xmc-8v26 / CVE-2026-63349 — the `extra_groups` API added in `4.14.0` forwards the wrong variable in `open_process`, so a caller clearing supplementary groups silently retains the parent's. Affects `4.14.0` **only**; `4.13.0` is outside the affected range. | `anyio>=4.15.1` |

- **`anyio@4.13.0`:** transitive dep of `httpx2` (via `httpcore2`), `starlette`/`sse-starlette` and `watchfiles`; the backend imports it only through those consumers, never directly. Verified on the bumped environment: `httpcore2` ships `_backends/anyio.py` and selects anyio as the default async backend, so every HTTPS fetch runs through anyio's TLS streams.
- **Reachability (GHSA-82r6, HIGH):** live. The extraction pipeline fetches arbitrary user-supplied URLs (`backend/src/analecta/extraction/`), and internationalized hostnames are a realistic input class there. The advisory's exploit precondition — the connection must have been redirected to an attacker's host by other means (e.g. DNS-level hijack on an IDN) — is what the HTTPS-fetch path cannot rule out by itself; the IDNA 2003 encoding (fixed to IDNA 2008 upstream) is exercised whenever anyio builds the TLS `server_hostname` for such a connection.
- **Reachability (GHSA-5p39):** defense-in-depth. The sidecar spawns no anyio process pools (no `to_process`/`open_process`/`run_process` call sites in `backend/src/analecta`), so the affected code path is not exercised; the floor covers it for the transitive chain.
- **Reachability (GHSA-3w57):** not exposed. The installed `4.13.0` predates the `extra_groups` API the advisory names; the floor excludes the vulnerable `4.14.0` resolution class anyway.
- **Transitive resolution:** the floor moved `typing-extensions` from `4.15.0` to `4.16.0` (anyio `4.15.1` declares it for `python_full_version < '3.15'`); no advisory against either endpoint.
- **No cooldown exception needed:** `4.15.1` released 2026-09-05, 15 days before this bump (2026-09-20).

### 2026-09-19 — npm `pnpm` package-manager pin

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `pnpm@11.0.6` (published 2026-05-05) — the package manager itself, pinned in `package.json`'s `packageManager` field | 18 advisories — 11 high, 7 medium, 14 distinct CVEs. Install-time attack surface: GHSA-5wx6-mg75-v57r / CVE-2026-55487 (manifest identity spoof satisfies `allowBuilds` → attacker lifecycle scripts), GHSA-vx52-2968-3vc6 (env secrets exfiltrated via proxy-settings env-placeholder expansion), GHSA-c59q-g84q-2gj5 / CVE-2026-82392 (path traversal out of `node_modules` via lockfile depPath), GHSA-vq4v-j7r6-jq4m / CVE-2026-82393 (path traversal out of `node_modules` via tarball manifest name), GHSA-q6j5-fjx5-2mc3 / CVE-2026-50021 (integrity-check bypass via a lockfile missing the integrity field), GHSA-54hh-g5mx-jqcp / CVE-2026-50573 (unsafe default breaks the integrity check), plus 11 more patched at `11.0.7`, `11.4.0`, `11.5.3`, `11.7.0`, `11.8.0` and `11.11.0`. | `packageManager` pin regenerated `11.0.6` → `12.4.2` via `corepack use pnpm@12.4.2`. No `overrides:` entry exists or is needed — pnpm is the tool executing the install, not a workspace dependency; the pin is the control. Target `pnpm@12.4.2`: zero advisories. |

- **Registry integrity verified before adoption:** `pnpm view pnpm@12.4.2 dist.integrity` → `sha512-CK3GYTGAJ1x8ntraOdzwjJxhrU5+rzMKTzRh8QKw+QdCNFTRF/mOctR/7wYWBwZE17/8lzpqV/UJCm18NosHyQ==` (hex `08adc6613180275c7c9edada39dcf08c9c61ad4e7eaf330a4f3461f102b0f907423454d117f98e72d47fef0616070644d7bffc973a6a57f5090a6d7c368b07c9`, base64→hex conversion re-derived independently). The regenerated `packageManager` suffix matches that hex exactly, and the new lockfile's `pnpm@12.4.2` resolution carries the identical integrity string.
- **The pin, not a manifest range:** the load-bearing pin is `package.json`'s `packageManager` field — pnpm's managed-version switch runs that version in-repo regardless of mise. `.mise.toml` now pins `pnpm = "12.4.2"` to match, so CI — which resolves pnpm through `jdx/mise-action` + `mise exec -- pnpm`, with no corepack — cannot float to a pnpm that writes a different lockfile shape; no workflow or script asserts a pnpm version.
- **Two additional security fixes in `12.4.2` (vs `12.4.1`, upstream release notes):** dependency executables can no longer take over another package's POSIX bin shim via shell helpers (#14837); GitHub Actions homepage links no longer expose server credentials (GitHub server URLs now require HTTPS, HTTP only for loopback).
- **Reachability:** pnpm is the package manager — never shipped in the `.deb`/`.rpm`/`.AppImage`, but it executes in CI (with repo tokens in the write-permission job) and on the maintainer's machine at install time. The advisories that matter here are the install-time ones (lifecycle-script execution, env-secret exfiltration, writes outside `node_modules`), which is exactly why the pin is the control.
- **Lockfile churn observed — two YAML documents, not additive-only:** `pnpm-lock.yaml` is no longer a single YAML document. Per pnpm's own lockfile documentation (`pnpm.io/lockfile`), pnpm writes an **env lockfile** first (when present), carrying `configDependencies` and `packageManagerDependencies`, then the **project lockfile** with the real dependency graph; both declare the same `lockfileVersion` — that field describes entry schema, not document count. The env document appears when the project has config dependencies or when pnpm records the resolved package-manager version — the latter applies here because `package.json` pins pnpm 12+ through `packageManager`: the prepended document records `importers: .: packageManagerDependencies: pnpm: {specifier: 12.4.2}` plus `pnpm@12.4.2` and its 14 `@pnpm/exe.*` platform-binary packages/snapshots, with the project document byte-identical (zero deleted lines). The only way to suppress the env document is `pmOnFail=ignore`, which also stops pnpm enforcing the `packageManager` pin — deliberately rejected. Documented hazard (quoted): a reader that loads a single document "either raises an error or silently gives you the first document", and such a tool "reports that the project has no dependencies, and therefore no vulnerabilities, and a CI gate built on it passes". This repo's readers: `scripts/deps_update.py` treats the lockfile as bytes (snapshot/restore), never parses it — unaffected; `pnpm` itself reads both documents; `scripts/verify-provenance.py` reads the file with a regex over the whole text, so it sees both documents (its scoped-package gap is tracked in the separate scanner work unit). CI consequence, pnpm-documented: since v11.23.0 a frozen install no longer rewrites the env block and fails with `ERR_PNPM_FROZEN_LOCKFILE_WITH_OUTDATED_LOCKFILE` when the recorded pnpm version is missing or no longer matches the pin. Install and `install --frozen-lockfile` were both no-ops under 12.4.2 (`Already up to date`; no relink, no store-dir change) and pnpm 12's built-in lockfile supply-chain-policy check passed (569 entries).
- **Rollback path:** reverting the pin means `corepack use pnpm@<previous>` (or restoring the previous `packageManager` value) **and** regenerating `pnpm-lock.yaml` with a non-frozen `pnpm install` — the env document records the resolved pnpm version, and a frozen install fails once it disagrees with the pin (see the lockfile bullet above).
- **Cooldown exception (maintainer-approved 2026-09-19):** `12.4.2` was published 2026-09-15T10:48:29Z, 3.64 days before this bump — 9 hours short of the 4-day minimum release age. Approved explicitly: the urgency is the outgoing pin's 11 high advisories, not `12.4.2`'s own age (it has zero advisories). Recorded here and in `CHANGELOG.md`, never silently.

### 2026-09-18 — npm/pnpm `devalue` override

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `devalue@5.9.0` | CVE-2026-81176 / GHSA-9rgm-9g3h-6x36 (CVSS 5.3 MEDIUM, `AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:L`, CWE-770) — `devalue.parse` prior to 5.9.2 fails to reject out-of-bounds indices; crafted payloads alternate between different array representations, doing work quadratic with payload size. Affected range `< 5.9.1`; the only release the advisory lists as patched is `5.9.2` — `5.9.1` was published in between but is declared neither affected nor patched, a publication gap; `5.9.2` was adopted as the first confirmed patched release, clear of that ambiguity. Advisory published 2026-09-17, one day before this bump — noticed via the advisory directly, no Dependabot alert yet at adoption time. | `overrides: {devalue: '5.9.2'}` in `pnpm-workspace.yaml`, bumped in place from `5.9.0`. |

- **A pin, not a force:** the two consumers, `svelte@5.57.0` and `@sveltejs/kit@2.70.3`, both declare `devalue: ^5.8.1`, which `5.9.2` satisfies. `pnpm why` shows a single resolved instance; the lockfile's `svelte`/`kit` snapshot lines and the `devalue@5.9.2` package entry all flip to `5.9.2`, and `pnpm dedupe` reported "Already up to date" — no orphaned `5.9.0` remains.
- **Reachability:** not reachable with untrusted data in the packaged app. Zero direct imports in this repo's code (`frontend/src`, `electron/`); the only consumers are `@sveltejs/kit`'s server runtime (`data_serializer.js`, `form-utils.js`, `env_module.js`, `server/utils.js`) and `svelte`'s `internal/server/*` (SSR rendering) — every `devalue.parse` call site (`form-utils.js:266`) runs in a server/form-action path. Analecta is a static build: `@sveltejs/adapter-static` with `prerender = true; ssr = false` globally (`frontend/src/routes/+layout.ts`), zero `*.server.ts` files (no form actions, no server load functions), no `use:enhance`. Nothing attacker-controlled ever reaches `devalue.parse`; the only parsed payload would be prerendered HTML generated at build time from this project's own data.
- **Consumer smoke test** (run anyway per the step-5 precedent in this log — no runtime consumer path is exercised by `check.sh`, since no runtime path exists at all) against the real `node_modules/.pnpm/devalue@5.9.2/` instance: replayed `form-utils.js`'s exact call shape — `devalue.stringify([data, meta], reducers)` → `devalue.parse(text, reducers)` with a `File` reviver — on representative data (Date, Map, Set, RegExp, sparse array, Infinity/NaN/undefined); round-trip lossless and re-encode byte-identical. The advisory's out-of-bounds-index class now fails closed: out-of-bounds sparse-array indices (`idx >= len`, negative, `> MAX_ARRAY_INDEX`, non-integer, string), out-of-range sparse `len` (`> MAX_ARRAY_LEN`), and out-of-bounds numeric references (`idx >= values.length`, huge and fractional) all reject immediately with `Invalid input`, while a legitimate sparse array (`[[-7, 5, 4, 1], "v"]` form, matching `stringify`'s own encoding) and a 200 000-element object round-trip still parse correctly.
- **No cooldown exception needed:** `5.9.2` released 2026-08-27, 22 days before this bump (2026-09-18). Note: `5.9.3`/`5.9.4` published later the same day as this bump — not adopted; no known advisories against `5.9.2`.

### 2026-09-18 — Python/uv `constraint-dependencies`

Security floors in `[tool.uv] constraint-dependencies` (`backend/pyproject.toml`). Each is a **floor, not a pin** — `uv` resolves the newest version satisfying every declared range above it, so the floor only removes vulnerable resolutions. The one-line comments in `pyproject.toml` point here; this table carries the rationale so the file itself doesn't have to.

| Package | Advisory(ies) | Floor & rationale |
|---------|--------------|-------------------|
| `starlette@1.0.1` | CVE-2026-54283 (HIGH 7.5, `request.form()` DoS), CVE-2026-48818 (HIGH 7.5, `StaticFiles` SSRF, Windows-only), CVE-2026-48817 (MODERATE 5.3, `HTTPEndpoint` getattr dispatch), CVE-2026-54282 (LOW 3.7, `request.url.hostname` poisoning) | `starlette>=1.3.1` |
| `soupsieve@2.8.3` | GHSA-2wc2-fm75-p42x (HIGH 7.5, memory exhaustion via large comma-separated CSS selector lists), GHSA-836r-79rf-4m37 (HIGH 7.5, ReDoS in the attribute-value regex) — both fixed by 2.8.x; GHSA-j934-xhv5-fg8f + GHSA-gjv8-xp57-g29c (2026-09-18, availability-only quadratic-CPU DoS in the selector compiler — unanchored trailing-whitespace/comment trim and adjacent-quantifier identifier backtracking; ~10 s CPU per ~20 KB selector and ~17 s per ~12 KB selector, also triggerable through `BeautifulSoup.select()` — both fixed in 2.9) | `soupsieve>=2.9.2` |
| `lxml-html-clean@0.4.4` | GHSA-4jhm-jv67-739f (CVSS 8.2 HIGH, `Cleaner` does not strip `javascript:` URLs from `xlink:href` with `safe_attrs_only=False`) | `lxml-html-clean>=0.4.5` |
| `setuptools@82.0.1` | GHSA-h35f-9h28-mq5c (MODERATE 6.1, `MANIFEST.in` exclusion bypass in sdist builds via NFC/NFD Unicode normalization collision on macOS APFS/HFS+) | `setuptools>=83.0.0` |

- **`starlette@1.0.1`:** transitive dep (via `fastapi`, `sse-starlette`); `fastapi` only requires `>=0.46.0`, so a floor here is sufficient.
- **`soupsieve@2.8.3`:** transitive dep of `beautifulsoup4`. Analecta's own code never calls `.select()`/`.select_one()`/`soupsieve.compile()` (extraction uses only `find_all()` with hardcoded inputs), so both ReDoS/DoS classes need attacker-controlled selector input — the floor is maintained as defense-in-depth for the transitive chain.
- **`lxml-html-clean@0.4.4`:** transitive dep of `readability-lxml` (via `lxml[html-clean]`), which uses exactly that `Cleaner` configuration in `readability/cleaners.py`, on HTML fetched from arbitrary user-supplied URLs — the vulnerable configuration is live. Frontend's `markdown-it` runs with `html: false`, an incidental downstream mitigation, not a substitute for the fix.
- **`setuptools@82.0.1`:** transitive dep of `pyinstaller` (unconstrained range). See also the 2026-07-27 entry below.

### 2026-09-11

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `js-yaml@4.3.1` | CVE-2026-84375 / GHSA-2883-xcg3-v3hh (CVSS 7.5 HIGH, `AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H`) — `maxTotalMergeKeys` (default 10000) counted only the *keys* of each merge-source mapping, not the source mapping itself. A YAML document that merges a large sequence of *empty* mappings (`{}`, zero keys each) into K targets does `O(N*K)` work in `mergeMappings()` while `totalMergeKeys` never increments, so the guard never trips — the advisory's own PoC (N=20000 empty mappings, K=20000 targets) measured ~13s. | `overrides: {js-yaml: '4.3.2'}` in `pnpm-workspace.yaml`, bumped in place from `4.3.1` — supersedes the `js-yaml@4.3.0` row below. |

- **Not a version bump alone — a real code fix:** `mergeMappings()` now calls `chargeMergeWork(state)` once for the source mapping itself, before iterating its keys (`lib/loader.js:388`, comment: "Count the source mapping itself to bound sequences of empty mappings"), plus an unconditional new cap — `storeMappingPair()` throws `'abnormal merge sequence size'` if a merge sequence (`<<: [...]`) exceeds 100 elements (`lib/loader.js:438`), independent of `maxTotalMergeKeys`.
- **Reachability:** transitive via `electron-builder`/`dmg-builder`/`app-builder-lib` (build-time packaging tooling, not exercised outside `pnpm dist`) **and** `electron-updater` (a real runtime dependency — confirmed by reading the installed package: `Provider.js:97` calls `js_yaml_1.load(rawData)` on the `latest-linux.yml` manifest fetched from this project's own GitHub Releases feed over HTTPS with SHA-512 verification, not attacker-controlled input despite the CVSS score).
- **Consumer smoke test** (per `docs/dependency-verification.md` step 5 — neither call site is exercised by `check.sh`) against the real `node_modules/.pnpm/js-yaml@4.3.2/` instance: replayed `Provider.js`'s exact `load()` call on a representative `latest-linux.yml`, output unchanged; replayed the advisory's own PoC verbatim (`YAML11_SCHEMA`, N=20000) — now rejected in ~18ms with `abnormal merge sequence size` instead of completing in ~13000ms, confirming the fix engages; a small, legitimate `<<: *defaults` merge (well under the new 100-element cap) still resolves correctly, ruling out a regression on ordinary merge-key usage.
- **No cooldown exception needed:** `4.3.2` released 2026-08-26, 16 days before this bump.

### 2026-09-03

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `fast-uri@3.1.5` (via `ajv@8.20.0`) | Six HIGH advisories (all CVSS 7.5, `AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:H/A:N`), superseding the `fast-uri@3.1.4` row below. | `overrides: {'fast-uri': '3.1.7'}` in `pnpm-workspace.yaml`, bumped in place from `3.1.5`. |

- **Four fixed at `3.1.6` (published 2026-08-23):** GHSA-5jgf-p345-68v8 / CVE-2026-75931 (host confusion — `resolve()` emits a scheme-relative `//host` reference verbatim without IDN-canonicalizing it once the effective scheme is known, so re-parsing the result yields a different host; Dependabot alert #47), GHSA-fph4-wmhf-6fwf / CVE-2026-75899 (SSRF — a nested percent-encoded host is decoded twice in one `normalize()`/`resolve()` call, so `%256c%256f%2563%2561%256c%2568%256f%2573%2574` recomposes to `localhost`; alert #48), GHSA-f65p-4m7j-42xc / CVE-2026-75975 (SSRF via malformed IPv6 normalization), GHSA-jqff-g426-hqxp / CVE-2026-76172 (host confusion via percent-encoded scheme normalization).
- **Two fixed only at `3.1.7` (published 2026-09-02) — repo-level advisories on `fastify/fast-uri`, not yet propagated to GitHub's global advisory DB, so no Dependabot alert yet:** GHSA-qw65-cvwx-89v3 (authority injection — `serialize()`/`normalize()`/`equal()` never validated the `port` component, so a component object `{host: 'trusted.example', port: '8080@evil.example'}` serialized to `http://trusted.example:8080@evil.example/…`, folding the real host into userinfo) and GHSA-58mr-gqgx-xq4g (host confusion — an unbalanced or misplaced `[`/`]` in the authority was waved through as an IP literal instead of being validated as a reg-name).
- **A pin, not a force:** `ajv@8.20.0` declares `fast-uri: ^3.0.1`; `pnpm why` shows one resolved instance and the lockfile's `ajv@8.20.0` snapshot line flips to `3.1.7`.
- **Reachability:** not reachable in the packaged app — the sole consumer is `ajv@8.20.0` via `app-builder-lib` (electron-builder, a `devDependency`), which uses `fast-uri` for build-time JSON Schema `$id`/`$ref` resolution over this project's own schemas, never attacker-controlled network input.
- **Consumer smoke test** (per `docs/dependency-verification.md` step 5) against the real `node_modules/.pnpm/fast-uri@3.1.7/` instance ajv resolves: export key set identical to `3.1.5` (`SCHEMES, default, equal, fastUri, normalize, parse, resolve, resolveComponent, serialize`); `ajv@8.20.0` and `ajv/dist/2019` compile and validate schemas carrying `$id`, cross-file `$ref`, self `$ref`, and `$recursiveRef`/`$recursiveAnchor` unchanged; `ajv`'s own `parse()`/`serialize()`/`resolve()` call shapes round-trip on representative schema IDs; and both `3.1.7`-only PoCs now fail closed — `serialize({…, port: '8080@evil.example'})` throws `URI port is malformed.`, `parse('http://[fe80')` sets `error: 'URI host is malformed.'` and `resolve()` throws — while valid ports and well-formed bracketed IPv6 literals still pass. Non-vacuous: the same bad-port input against the pre-bump `fast-uri@3.1.5` tree still returns `http://trusted.example:8080@evil.example/app`.
- **Cooldown exception:** `3.1.7` released 2026-09-02, 1 day before this bump (9 short of the 10-day window — tied with the 2026-08-12 `@xmldom/xmldom` bump for the project's largest) — approved explicitly given the uniform CVSS 7.5 host-confusion/SSRF rating. `3.1.6` alone (11 days old, clears the window unaided) would have closed the first four; `3.1.7` also closes the `3.1.7`-only pair and pre-empts their eventual alerts.

### 2026-09-02

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `postcss-selector-parser@7.1.1` | GHSA-w9m9-85wc-3x92 / CVE-2026-9358 (CVSS v4 `AV:N/AC:L/AT:N/PR:N/UI:P/VC:N/VI:N/VA:L` — Low, availability-only; CWE-404). Uncontrolled recursion in `src/selectors/container.js`'s `toString()` (AST serialization) — a deeply nested selector recurses with no depth bound and overflows the stack. Affected `>= 7.1.0, < 7.1.3` (the advisory also covers `< 6.1.3`, but no `6.x` instance resolves in the tree); fixed at `7.1.3`, which adds a 256-level nesting-depth cap that throws a catchable `Error` instead of recursing. | `overrides: {postcss-selector-parser: '7.1.5'}` in `pnpm-workspace.yaml` — a single global entry (`pnpm why` shows one resolved instance). Pinned to `7.1.5` (latest) rather than the `7.1.3` minimum. |

- **Surfaced via** Dependabot alert #46.
- **A pin, not a force:** the sole consumer, `svelte-eslint-parser@1.8.0` (via `eslint-plugin-svelte@3.23.0`, a root `devDependency`), declares `postcss-selector-parser: ^7.0.0`, which `7.1.5` satisfies — this only removes the resolver's freedom to drift within that range, it doesn't override a declared constraint. Dependabot's "latest possible version that can be installed is 7.1.1" is its own inability to author pnpm `overrides:` for a transitive dep, not a real ceiling — the `pnpm install` confirms `7.1.5` resolves cleanly, and the lockfile's `svelte-eslint-parser` snapshot line flips to `7.1.5` (override took effect, not resolved-and-ignored).
- **Reachability:** not reachable in the packaged app. `svelte-eslint-parser` is ESLint tooling — runs only during `pnpm lint` / `check.sh`, never ships in the `.deb`/`.rpm`/`.AppImage`, and `lib/parser/style-context.js` feeds it only this project's own component `<style>` selectors (`selectorParser().astSync(rule.selector)`), never attacker-controlled input. Upstream itself rates server-side DoS on user-generated CSS as low risk.
- **Consumer smoke test** (per `docs/dependency-verification.md` step 5 — `check.sh`'s eslint step proves the module loads under `svelte-eslint-parser@1.8.0` but not that the serialization path still behaves): replayed `style-context.js`'s exact call shape — `selectorParser()` → `astSync()` → `root.walk()` reading `node.source` — against the real `node_modules/.pnpm/postcss-selector-parser@7.1.5/` instance on representative selectors (`:is()`/`:not()`, attribute selectors, combinators, `&` nesting, comma lists); all round-trip through `toString()` correctly, and a 20 000-level nested selector now throws `Error: Cannot parse selector: nesting depth exceeds the maximum of 256` instead of overflowing the stack.
- **No cooldown exception:** `7.1.5` released 2026-08-07, 26 days before this bump.

### 2026-08-28

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `@xmldom/xmldom@0.8.14` / `@xmldom/xmldom@0.9.11` | 12 GHSAs published 2026-08-21, no CVE IDs assigned yet, self-disclosed by the xmldom maintainers. Fixed at `0.8.15` and `0.9.12` respectively — both confirmed non-deprecated. | Same two version-scoped `overrides:` entries in `pnpm-workspace.yaml`, bumped in place: `'plist@3.1.0>@xmldom/xmldom': '0.8.15'` and `'plist@3.1.1>@xmldom/xmldom': '0.9.12'` (plus `'mathml-to-latex@1.8.0>@xmldom/xmldom': '0.9.12'`). |

- **Surfaced via** a Socket `deprecated`/maintenance alert on `0.9.11` (`"this version has critical issues, please update to the latest version"`), then cross-checked by hand against `0.8.14` too.
- **7 affect both the `0.8.x` (`0.7.0-0.8.14`) and `0.9.x` (`0.9.0-0.9.11`) lines:** GHSA-c7q8-3ch8-vqpv (processing-instruction target injection bypasses `requireWellFormed`), GHSA-965w-775f-mr7g (quadratic-memory consumption), GHSA-6gmq-8vp8-gcm6 (XML fragment injection via `EntityReference.nodeName` during `requireWellFormed` serialization), GHSA-8344-3jmq-59r6 (quadratic-time attribute deduplication, CWE-407), GHSA-93r5-fhx6-vmg9 (quadratic-time parsing via the malformed-input recovery path, CWE-407), GHSA-27p8-2357-5qqv (DocType `name` injection bypasses `requireWellFormed`), GHSA-6h8r-xr42-gp59 (parser silently accepts a not-well-formed end tag followed by a line break and trailing content).
- **1 affects only `0.8.x`:** GHSA-x4fp-j954-r2f4 (ReDoS in the `0.8.x` end-tag whitespace-trim regex, CWE-1333).
- **4 affect only `0.9.x`:** GHSA-6mj3-qw4j-hgrw (HTML raw-text closing-tag case mismatch causes O(n²) output amplification), GHSA-3px3-54cx-rmw9 (Name/QName validation bypassable via an embedded line terminator), GHSA-vr34-hp96-76pp (DocType `publicId`/`systemId` validation bypass via line terminator, `0.9.10-0.9.11` only), GHSA-jxjr-3g7g-3944 (element/attribute name validation bypass via line terminator, `0.9.11` only).
- **Re-verified, not assumed:** the 2026-08-13 finding that `plist@3.1.0` (no `mimeType` arg to `parseFromString`) is incompatible with xmldom's `0.9.x` strict-mimeType check still holds, so the two branches stay unmerged. Ran an isolated install (`plist@3.1.0` + `@xmldom/xmldom@0.8.15` override) and called `plist.parse()`/`plist.build()` on a sample `Info.plist` — both succeeded, confirming this batch's `requireWellFormed` hardening didn't also tighten `0.8.x`'s mimeType handling.
- **Considered and rejected:** forcing a global `plist: '3.1.1'` override to collapse both branches and drop the `0.8.x` pin — mechanically viable (would satisfy `@electron/osx-sign`'s `^3.0.5` and `@electron/universal`'s `^3.1.0` ranges), but buys zero extra security since `0.8.x` already has a clean patch, at the cost of forcing a resolution over `app-builder-lib`'s own exact `"3.1.0"` pin — unchanged from `26.15.1` through the `27.0.0` alpha line as of this writing — on a code path already established as unreachable (see Reachability below, carried over unchanged from 2026-08-13).
- **Cooldown exception:** both `0.8.15` and `0.9.12` released 2026-08-21, 7 days before this bump (3 short of the 10-day window) — approved explicitly despite no CVE ID/CVSS score yet, given the GitHub-rated "high" severity on most of the 12 advisories and the upstream deprecation notice on both superseded versions.

### 2026-08-13

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `nanoid@3.3.17` | CVE-2026-67213 / GHSA-2v37-7h3g-55p8 (CVSS 8.2 HIGH) | `overrides: {nanoid: '3.3.18'}` in `pnpm-workspace.yaml`. |

- **Corrects the 2026-08-12 entry below:** `3.3.17` (adopted then) is still inside the advisory's own vulnerable range (`< 3.3.18`) — confirmed via the GHSA page itself ("Patched versions: 3.3.18"), Socket's own CSV export (`firstPatchedVersionIdentifier: "3.3.18"`), and npm registry timestamps (`3.3.17` published 2026-08-03, `3.3.18` published 2026-08-07 — distinct, later release). The 2026-08-12 write-up's reachability analysis was correct (`postcss@8.5.23`, build-time only, never calls `customAlphabet`/`customRandom`); only the adopted version number was wrong.
- **Cooldown exception:** `3.3.18` released 2026-08-07, 6 days before this bump (4 short of the 10-day window, a larger exception than the original) — approved explicitly given the unchanged CVSS 8.2 rating.
| `@xmldom/xmldom@0.8.13` / `@xmldom/xmldom@0.9.10` | GHSA-w2rr-34g9-rvrj (CVSS 8.7, `createElement()` doesn't validate the element name — a crafted name survives serialization and injects extra attributes/event handlers), GHSA-4w3w-2rp5-g8jm (CVSS 8.7, same injection class via `setAttribute()` bypassing the name validation `createAttribute()` enforces) — both affect `0.7.0-0.8.13` and `0.9.0-0.9.10`, and neither is caught by `requireWellFormed: true`, previously the recommended mitigation. Plus GHSA-g53g-w8rj-fmg7 (CVSS 8.7, `0.9.0-beta.9-0.9.10` only — quadratic-time backtracking parsing an unterminated `<?` processing instruction, stalls the event loop on untrusted XML; the `0.8.x` line was never affected, different bounded parser). All three fixed at `0.8.14` and `0.9.11`. | Two version-scoped `overrides:` entries in `pnpm-workspace.yaml`, deliberately **not unified to one version**: `'plist@3.1.0>@xmldom/xmldom': '0.8.14'` and `'plist@3.1.1>@xmldom/xmldom': '0.9.11'` (plus `'mathml-to-latex@1.8.0>@xmldom/xmldom': '0.9.11'` for the unrelated `defuddle` branch, same target version). |

- **Verified empirically** that unifying would break `plist@3.1.0`: downloaded and diffed both versions' tarballs — the only functional difference between `plist@3.1.0` and `3.1.1` is that `3.1.1`'s `lib/parse.js` added an explicit `"text/xml"` second argument to `DOMParser.parseFromString()`, while `3.1.0` still calls it with none. Reading `xmldom@0.9.11`'s own `dom-parser.js`/`conventions.js` confirms `isValidMimeType(undefined)` is `false`, so `parseFromString` throws a `TypeError` when called without a mimeType — forcing `0.9.11` onto the `3.1.0` branch would break `plist.parse()` outright, not just risk an incompatibility.
- **Reachability:** both `plist` branches are transitive via `app-builder-lib` (electron-builder); grepping its compiled output shows `plist` is only required from `electronMac.js`, `targets/pkg.js` (macOS `.pkg` target), and `LibUiFramework.js` (an Electron-alternative framework Analecta doesn't configure) — none of those run when packaging `.deb`/`.rpm`/`.AppImage`, Analecta's only build targets. The `mathml-to-latex` branch is a `defuddle` dependency (root `package.json` devDependency, dev-only diagnostic tool, never a shipped runtime dep — see `docs/defuddle-decision.md`).
- **Cooldown exception, the largest in this project's history:** both `0.8.14` and `0.9.11` released 2026-08-12, 1 day before this bump (9 short of the 10-day window) — approved explicitly given the CVSS 8.7 rating despite the non-reachability above.

### 2026-08-13

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `electron@42.1.0` (direct dependency, `electron/package.json`) | GHSA-r4w5-6pfg-jxp5 / CVE-2026-70606 — session-isolation flaw in protocol response handling: a `ProtocolResponse` omitting an explicit session could leak cached responses across isolated session partitions. | Bumped to `42.5.1`. **Not reachable in this app:** both custom protocol handlers (`app://`, `analecta-file://`) return `Response` objects via `protocol.handle()` rather than the legacy `ProtocolResponse` shape, and only `session.defaultSession` is used — no partitioned sessions exist to leak across. |

### 2026-08-12

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `js-yaml@4.3.0` | GHSA-5p4m-2wfm-xmqj (no CVE assigned; CVSS 7.5 HIGH, quadratic-time DoS in `!!omap` resolution — the CVE-2026-59870 fix from the 5.x line, never backported to 4.x) | `overrides: {js-yaml: '4.3.1'}` in `pnpm-workspace.yaml`. |
| `nanoid@3.3.16` | CVE-2026-67213 / GHSA-2v37-7h3g-55p8 (CVSS 8.2 HIGH, infinite loop in `customAlphabet`/`customRandom` when called with `size: 0`) | `overrides: {nanoid: '3.3.17'}` in `pnpm-workspace.yaml`. |
| `@sveltejs/kit@2.70.1` | CVE-2026-66062 / GHSA-29g2-3rmr-qm68 (CVSS 5.3 MODERATE, ReDoS in `Accept`-header content negotiation) | Bumped to `2.70.2` via Dependabot PR #82 (squash-merged 2026-08-12). |

- **`js-yaml@4.3.0`:** Transitive via `electron-builder`/`dmg-builder`/`app-builder-lib` (build-time tooling only) **and** `electron-updater` (a real runtime dependency — parses `latest-linux.yml` fetched from GitHub Releases when checking for updates). Runtime-reachable, but low practical severity despite the CVSS score: that YAML comes from Analecta's own release feed over HTTPS with SHA-512 verification, not attacker-controlled input — worst case is an updater hang, not compromise.
- **`nanoid@3.3.16` — Cooldown exception:** `3.3.17` released 2026-08-03, 9 days before this bump (1 short of the 10-day window) — approved explicitly given the CVSS 8.2 rating; EPSS is 0.003 and the only consumer in this tree is `postcss@8.5.23` (build-time CSS tooling), which never calls either custom-generator function, let alone with `size: 0` — not reachable regardless.
- **`@sveltejs/kit@2.70.1`:** Direct `devDependency`; not reachable in the packaged app — `@sveltejs/adapter-static` means the vulnerable server-side content-negotiation code never ships, present only in the local `pnpm dev` dev server.

### 2026-08-03

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `brace-expansion@1.1.16` / `2.1.2` / `5.0.8` | GHSA-rgw5-rvv9-x895 (CVE-2026-69152, HIGH 7.5) — bypasses the `maxLength` mitigation `5.0.8` shipped for GHSA-mh99-v99m-4gvg: two intermediate arrays (`values` in `expand_()`, and `expandSequence()`'s padded-sequence output) were never bounded by `maxLength`, so a ~25 KB input can still OOM-crash the process, and a ~400 KB input can stall the event loop for minutes. Fixed upstream by bounding both. | `overrides: {'brace-expansion@1': '1.1.18', 'brace-expansion@2': '2.1.4', 'brace-expansion@5': '5.0.9'}` in `pnpm-workspace.yaml`. |
| `fast-uri@3.1.4` (via `ajv@8.20.0`) | GHSA-7p8r-x3mc-p8w7 (CVE-2026-18446, HIGH 7.5) — `\\`/`/\`/`\/` authority introducer parsed as no-authority (folds into path) instead of matching Node's native WHATWG `URL` behavior (used by `fetch()`/`undici`/`http`), which treats `\` as interchangeable with `/` for special schemes. Policy/parser desync for anything using `fast-uri` to enforce host-based rules ahead of a WHATWG-URL consumer. | `overrides: {'fast-uri': '3.1.5'}`. |
| `undici@7.28.0` (`@electron/get`) / `undici@6.27.0` (`node-gyp`) | 5 CVEs at once, all patched at `7.29.0`: CVE-2026-13697/GHSA-4cwx-7wf7-3272 (HIGH 7.4, cross-user shared-cache disclosure + parse-time crash via degenerate `private` cache-control directives), CVE-2026-16728/GHSA-8xcm-r25x-g524 (MODERATE 4.8, response desync via `interceptors.retry()` serving a stale `Content-Length`), CVE-2026-16729/GHSA-v3r7-h72x-cjcm (MODERATE 4.8, cookie injection via unsanitized `setCookie` domain/`unparsed` fields), CVE-2026-14643/GHSA-jr45-8vmc-qm54 (MODERATE 5.9, cache-control whitespace-around-`=` parse bypass letting authenticated responses land in shared cache), CVE-2026-15157/GHSA-m8rv-5g2x-5cg5 (MODERATE 4.2, CRLF injection via a duck-typed blob's `.type` property). | `overrides: {undici: '7.29.0'}` — **unified from the prior split override** (`@electron/get>undici: 7.28.0` / `node-gyp>undici: 6.27.0`). |

- **`brace-expansion` — Cooldown exception:** all three released 2026-07-30, 4 days before this bump (6 short of the 10-day window) — approved explicitly given the CVSS 7.5 rating; EPSS is 0.003 and exposure is build-time tooling on our own glob patterns, not attacker-controlled input, so real-world urgency was low.
- **`brace-expansion` — Retires the 2026-07-27 residual-risk carve-out below:** that entry assumed no 1.x/2.x backport existed for GHSA-mh99-v99m-4gvg; `1.1.17`/`2.1.3` were published afterward, and this bump adopts their successors (`1.1.18`/`2.1.4`) directly, so both the original CVE and its bypass are closed on all three lines. `dist.integrity` cross-checked against the npm registry and `pnpm view` for all three versions before adoption.
- **`fast-uri` — Cooldown exception:** released 2026-07-31, 3 days before this bump — approved explicitly given the CVSS 7.5 host-confusion rating. Verified two ways beyond hash-check: export shape unchanged (`ajv`'s `require("fast-uri")` call gets the same `.parse`/`.resolve`/etc. shape), and the fix itself confirmed empirically — feeding the advisory's exact PoC (`\\evil.com/path`) now throws `"URI authority must not contain a literal backslash"` instead of silently mis-parsing it.
- **`undici`:** `node-gyp`'s own `package.json` still declares `"undici": "^6.25.0"` (7.x is outside its stated semver range), so this was verified empirically before unifying rather than assumed safe: installed `7.29.0`, required it from `node-gyp`'s own dependency path, and replayed `lib/download.js`'s exact calls (`new RetryAgent(new Agent(), {maxRetries: 3})`, `new RetryAgent(new EnvHttpProxyAgent(opts), {maxRetries: 3})`, then a real `fetch()` through that dispatcher against a live URL) — all succeeded. `pnpm why undici` confirms a single resolved version across both consumers.
- **`undici` — No cooldown exception needed:** both `7.29.0` and `6.28.0` (the version that would have covered `node-gyp` under the old split) were released 2026-07-24, exactly 10 days before this bump.

### 2026-07-27

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `tar@7.5.16` | GHSA-r292-9mhp-454m (MODERATE 5.3, `mapHas`/`filesFilter` stack-overflow DoS) + 4 earlier CVEs it supersedes (GHSA-23hp-3jrh-7fpw CRITICAL 9.2 gzip-bomb DoS, GHSA-8x88-c5mf-7j5w HIGH 8.7 `replace()` infinite loop, GHSA-gvwx-54wh-qm9j MODERATE 5.3 PAX NUL-byte uncaught exception, GHSA-w8wr-v893-vjvp MODERATE 5.3 PAX numeric-path type confusion) | `overrides: {tar: '7.5.21'}` in `pnpm-workspace.yaml` |
| `fast-uri@3.1.2` (via `ajv@8.20.0`) | GHSA-v2hh-gcrm-f6hx (HIGH 7.5, backslash authority-delimiter host confusion), GHSA-4c8g-83qw-93j6 (HIGH 7.5, failed IDN canonicalization host confusion) | `overrides: {'fast-uri': '3.1.4'}` in `pnpm-workspace.yaml` |
| `brace-expansion@1.1.14` / `2.1.0` / `5.0.6` | GHSA-mh99-v99m-4gvg (HIGH 7.5, unbounded expansion length OOM), GHSA-3jxr-9vmj-r5cp (HIGH 7.7, exponential-time DoS) | Scoped per major line, **not a single blanket bump**: `overrides: {'brace-expansion@1': '1.1.16', 'brace-expansion@2': '2.1.2', 'brace-expansion@5': '5.0.8'}`. |
| `postcss@8.5.17` | GHSA-r28c-9q8g-f849 (HIGH 7.5, `sourceMappingURL` path traversal → arbitrary `.map` file disclosure), GHSA-fxqj-rqcc-2cmp | `overrides: {postcss: '8.5.23'}` — 10-day window exception approved given severity (8.5.23 was 3 days old at merge). |
| `setuptools@82.0.1` (via `pyinstaller`, unconstrained range) | GHSA-h35f-9h28-mq5c (MODERATE 6.1, `MANIFEST.in` exclusion bypass in sdist builds via NFC/NFD Unicode normalization collision on macOS APFS/HFS+) | `[tool.uv] constraint-dependencies = ["setuptools>=83.0.0"]` in `backend/pyproject.toml` |
| `electron-builder@26.15.3` | — (routine patch bump, not Socket-flagged; bumped ahead of the brace-expansion investigation since it could have shifted `@electron/asar`/`@electron/universal`'s dependency tree — it didn't) | Upgraded to `26.15.6` in `electron/package.json`. |

- **`brace-expansion`:** Verified empirically (real `minimatch` code, real brace-expansion tarballs, isolated `node_modules`) that forcing `5.0.8` onto the `minimatch@3.1.5`/`5.1.9`/`9.0.9` consumer lines throws `TypeError: expand is not a function` — brace-expansion@5.x rebuilt as a named-export-only CJS module via `tshy` (`exports.expand = expand`), while those minimatch versions call the old default-callable export directly (`require('brace-expansion')(...)`). Confirmed the inverse too: 1.1.16/2.1.2/5.0.8 paired with their own matching minimatch line all resolve correctly.
- **`brace-expansion` — Residual risk, accepted 2026-07-27:** GHSA-mh99-v99m-4gvg has no backport to the 1.x/2.x lines — downloaded and grepped both `1.1.16` and `2.1.2`, neither contains the `EXPANSION_MAX_LENGTH` bound that `5.0.8` has. `minimatch@3.1.5` (→ `glob@7.2.3` → `@electron/asar`, `dir-compare`) and `minimatch@5.1.9`/`9.0.9` (→ `filelist`, `@electron/universal`) stay exposed to it. Accepted because both are electron-builder build-time tooling operating on our own glob patterns, not attacker-controlled input — a hand-maintained `pnpm patch` backport was considered and rejected as worse than the documented residual for a non-attacker-reachable DoS.
- **`electron-builder@26.15.3`:** Not exercised by `check.sh` (packaging step is skipped there — see "Task workflow" note in project docs); verified via `pnpm install` + `tsc --noEmit` only. Real packaging is exercised by the release workflow.

### 2026-07-13

| Package | CVE | Fix |
|---------|-----|-----|
| `soupsieve@2.8.3` | GHSA-2wc2-fm75-p42x (CVSS 7.5 HIGH, memory exhaustion via large comma-separated CSS selector lists), GHSA-836r-79rf-4m37 (CVSS 7.5 HIGH, ReDoS in the attribute-value regex) | `[tool.uv] constraint-dependencies = ["soupsieve>=2.8.4"]` in `backend/pyproject.toml`. |
| `lxml-html-clean@0.4.4` | GHSA-4jhm-jv67-739f (CVSS 8.2 HIGH, `Cleaner` does not strip `javascript:` URLs from `xlink:href` when `safe_attrs_only=False`) | `[tool.uv] constraint-dependencies = ["lxml-html-clean>=0.4.5"]` in `backend/pyproject.toml`. |
| `@emnapi/runtime@1.11.1` | — (Socket `obfuscatedFile`/`supplyChainRisk`, no CVE; confidence 0.9 on `package/dist/emnapi.min.mjs`) | `overrides: {'@emnapi/runtime': '1.11.2'}` in `pnpm-workspace.yaml`. |

- **`soupsieve@2.8.3` — Architecture note:** Analecta's own code never calls `.select()`/`.select_one()`/`soupsieve.compile()` (confirmed via grep across `backend/src/`, `readability-lxml`, `trafilatura`) — the vulnerable input is the *selector string*, which is always hardcoded, never attacker-controlled. Low exploitability; fixed anyway since the patch is free (2.8.3 → 2.8.4, no functional change).
- **`lxml-html-clean@0.4.4`:** Unlike soupsieve above, the vulnerable configuration is confirmed live: `readability-lxml` (a direct extraction dependency, transitively pulling `lxml[html-clean]`) calls `Cleaner(..., safe_attrs_only=False, ...)` in `readability/cleaners.py`, on HTML fetched from arbitrary user-supplied URLs. Frontend's `markdown-it` is configured with `html: false` (`frontend/src/lib/markdown/renderer.ts`), which happens to keep any surviving payload from executing in the reading view — that's an incidental downstream mitigation, not a substitute for the fix.
- **`@emnapi/runtime@1.11.1`:** Socket's own analyst note found no malicious behavior — dynamic `Function` use limited to environment capability probing, same shape as other WASM-runtime false positives in this catalog. 1.11.2 doesn't reproduce the flag. Transitive via `@rolldown/binding-wasm32-wasi` (optional WASM fallback binding for Vite's Rolldown bundler). Same resolution pattern as the `js-yaml@4.2.0` entry below (2026-07-03): a version bump clears the flag rather than a permanent "Ignore."

### 2026-07-03

| Package | CVE | Fix |
|---------|-----|-----|
| `js-yaml@4.2.0` | GHSA-52cp-r559-cp3m (CVSS 7.5, quadratic-time DoS via chained merge-key mappings) | `overrides: {js-yaml: '4.3.0'}` — 10-day window exception approved given severity (4.3.0 was ~7 days old at merge). |

- **Cleared the `obfuscatedFile`/`supplyChainRisk` "monitor" alert** on the previous 4.2.0 minified bundle (formerly documented in "Known false positives" — removed, alert no longer present post-upgrade).

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

---

## Application & script security hardening

Security-relevant changes to Analecta's own code and build tooling — not dependency advisories — recorded here so the CHANGELOG's `### Security` one-liners carry a full-text backing. Newest first; each date is the release that carried the change.

### 2026-09-22 — provenance verification gate: conflicting-duplicate-identity guard (unreleased)

Closing the pending peer-suffix/identity-collapse finding (round-3 review WARNING `R3-peer-suffix-resolution-gap` + the latent `R3-duplicate-key-overwrite`) as its own candidate. Demonstrated-red: Agent A designed the three tests without the fix design, maintainer-approved at the red gate, Agent B implemented against the frozen test file, advisor audit afterwards.

- **The identity collapse was a silent hash drop.** The parser keys its verified map by `(name, version)` and discards the key line's quoting style (and would discard a peer suffix): two entries resolving to the same identity — byte-identical duplicate key lines, or a quoted and an unquoted spelling of the same identity — overwrote each other in the parsed map, last one wins. With different attested integrity values, one of the two hashes was verified and the other silently dropped from verification while `parse_lockfile` returned success.
- **The guard:** new `_conflicting_duplicates` helper (a second pass over the entry blocks tracking identity → first integrity) and a `parse_lockfile` guard placed last in the most-diagnostic-first ordering — a conflicting identity behind an entry the walk never reached is a shape problem first, and the unreached-resolutions guard names that drift more precisely. The failure names the colliding identity and surfaces both values (`first vs later`). Identical values remain a quiet dedup (across quoting variants too). The identity itself is deliberately unchanged: peer-suffixed keys carrying integrity already fail loudly in the unmatched-keys guard (verified by probe — they never reach the conflict helper), and making the suffix part of the identity is a separate design decision not taken here.
- **Deliberate structural note:** the collision detection lives in `parse_lockfile`'s composition, not in `_scan_lockfile`'s raising path — `find_unmatched_package_keys` must keep returning the gap list without raising (the approved tests pin that). `_scan_lockfile`'s docstring now documents its last-wins silence on duplicate identities and why the guard lives next door.
- **Advisor audit:** every reachable collapse path caught (byte-identical, cross-quoting, scoped variants, cross-document duplicates between the env and project lockfile documents, 3-entry collisions — `setdefault` keeps first so any later differing occurrence trips it); the repo's own two-document lockfile has zero cross-document identity overlap (584 parsed, 0 conflicts, 0 unmatched) and the guard adds ~3 ms per parse. Open follow-ups, deliberately not taken this round: the helper's parse predicate is a hand-mirrored copy of `_scan_lockfile`'s — identical by construction today, but folding the scan into `_scan_lockfile` itself (returning a third value without raising) would restore the 2026-09-19 single-pass invariant ('the parser and the guard cannot disagree about what was covered'); the conflict count reports pairs, not distinct identities (cosmetic). (An earlier revision of this bullet said no cross-document-duplicate or 3-entry-collision test fixture yet — stale as of 27295ee, which added exactly those fixtures plus a peer-suffixed-twin one.)
- **Tests:** suite grew 37 → 40 (2 demonstrated-red + 1 green companion pinning the quiet-dedup contract in 54305a8; 3 composition/edge fixtures — cross-document duplicate, three-way collision, peer-suffixed twin — added by the follow-up commit 27295ee). Gate: "all checks passed" (944 backend tests).

### 2026-09-22 — provenance verification gate: nested shape validation of the registry answer (unreleased)

Closing the review round of the 2026-09-21 work (review `review-67b70efaaab68546`, 9 advisories, all informational — 3 WARNINGs clustered on the attestation fetch path). Demonstrated-red throughout: Agent A designed the tests without seeing the fix design, the four red tests were approved at a maintainer gate, Agent B implemented against the frozen test file, and a read-only advisor audit checked completeness afterwards.

- **The nested layers of the registry answer were unvalidated.** `get_provenance_bundle` walked `meta["dist"]`, `dist["attestations"]`, the fetched bundle document, and its `attestations` entries behind `cast()` calls — runtime no-ops. A well-formed answer with a wrong nested shape (`dist: null`, `dist.attestations: "no"`, a truthy non-object bundle document like `[1,2]`, a non-list `attestations`, a non-object entry, a non-string `predicateType`) crashed the sweep with an unclassified `AttributeError`/`TypeError` that escaped `main()`'s `except RuntimeError` per-package collection, aborting the run mid-sweep without the complete picture.
- **A declared attestation could be silenced into the legitimate skip.** Once the metadata advertises `dist.attestations.url`, the bundle path had three silent-degradation exits: an attestations value that was falsy, an attestations list with no SLSA-provenance `predicateType`, or a matched attestation whose `bundle` was `None` (which also masqueraded as a falsy result at the call site). All resolved to the expected no-attestation gap: a registry-level MITM could strip or reshape the served document and the job would count the package under 'No attestation (expected gap)' and exit 0. The path is now fail-closed: after an advertised URL, every layer must be the right shape and the document must carry an SLSA-provenance attestation, or the run raises a classified `RuntimeError` (new `_classified_shape_error` helper) naming the package and the source. The legitimate skip is reserved for well-formed metadata without a usable `dist.attestations.url` — an absent/non-string/empty url keeps the skip, which is where the pre-existing no-out-of-band-anchor tradeoff (a MITM can always strip the declaration itself, recorded 2026-09-21) still applies, unchanged.
- **Presence checks, not truthiness checks:** explicit `null` layers raise (absent-with-None was initially conflated with absent-key and slipped through a first `is not None` guard — caught mid-implementation by the red tests and re-derived to presence checks) — `"dist" in meta` / `"attestations" in dist` distinguish absent (skip) from present-but-wrong (classified failure).
- **Advisor audit results:** no fail-open path remains on the advertised-URL path; the only tuple return validates both members; all new raise sites are `RuntimeError` reachable from the collection; realistic npm shapes (extra metadata fields, v0.2/v1 prefixes, no-`dist` metadata) pass untouched. Follow-ups deliberately left open: bundle-fetch URL scheme not pinned to https (pre-existing, adjacent), `check_subject_hash`'s inner-envelope failures keep the 'payload parse error' label (fail-closed already, label could be more accurate).
- **Tests:** suite grew 32 → 36 (4 demonstrated-red + 1 green companion pinning that an attestations object without `url` still skips). Gate: "all checks passed" (ruff format/check, basedpyright, 941 backend tests, svelte-check, vite build).

### 2026-09-21 — provenance verification gate: fail-open paths in its own logic (unreleased)

Follow-up round on the gate itself, closing three fail-open holes and one false-positive class the first two rounds left in the gate's *verification* logic (the earlier rounds closed the parser's coverage holes). Demonstrated-red throughout: every fix was a failing test first, reviewed by the maintainer at the red gate before implementation.

- **Partial shape drift silently shrank the verified set.** The zero-parse guard fired only when `parsed` was empty, so a lockfile with some entries indented differently from their neighbours parsed the visible subset and lost the drifted entries with no signal — exit 0 over a partial set. New guard: `_unowned_resolution_lines` counts the file's raw resolution lines (line-anchored, so prose and comments never count) against the lines the entry-block walk *owns*, and `parse_lockfile` raises naming the counts and the unowned lines.
- **The invariant's first version had its own hole, reproduced before the fix shipped:** a drifted entry absorbed into a `snapshots:` block was invisible, because a peerless snapshots key is byte-identical to a `packages:` key — the package pattern matched it and the drifted integrity **silently overwrote** the legitimate entry's hash in the parsed map (both the drifted-key and the bare-resolution shapes reproduced: `parsed[('good','1.0.0')] = <drifted hash>` with every guard empty). Ownership is now section-aware: a resolution line is owned only under a `packages:` section, inside an entry block, as that block's first one; a resolution line under `snapshots:`/`importers:`/the document head, a second one inside a block, or a column-0 resolution line is unowned and fails the parse. The second lockfile document is entirely `snapshots:` (line 2646+), so the realistic drift shape is the one this covers.
- **The Sigstore check was fail-open on unknown exceptions.** `verify_sigstore`'s final `except Exception` returned `ok=True` ("check skipped") for any exception class the script could not classify — a crafted bundle hitting an odd error path in the library would bypass the signature check. Unclassifiable exceptions now fail **closed** (`ok=False`); the network-as-warning and Rekor-timestamp/bundle-format compatibility skips keep their pre-existing verdicts, verified unchanged at byte level.
- **A failed registry request was indistinguishable from "package has no provenance".** `_fetch_json` returned `None` on every failure and the sweep counted those as the expected ~60% no-attestation gap — a registry-level MITM suppressing `dist.attestations` could make the job verify 0 of 584 packages and exit 0. Transport failures (timeout, connection refused, any request that never got a well-formed HTTP answer, including a 404 on the metadata or bundle request) now raise loudly naming the package and the unreachable source; a well-formed response without `dist.attestations.url` still returns `None` — the expected skip. Deliberate tradeoff, maintainer-approved: a package that npm no longer serves fails the job rather than counting as a gap, and the error message cannot yet distinguish "package gone" from "network down" (triage note for when it first fires).
- **A prose line mentioning `resolution:` failed the parse on a valid lockfile.** The inline-entry branch of `find_untokenized_package_keys` matched any two-space line containing the bare substrings `integrity:`/`resolution:`; it now reports only lines whose scalar part is package-shaped (carries `@`, the genuine inline shape `foo@1.0.0: {resolution: …}`), byte-identical for the genuine case.
- **Tests:** the script's suite grew from 20 to 28 tests. The four defect tests plus one section-ownership test were each demonstrated red against the pre-change code and approved at a maintainer gate before implementation (Agent-A/Agent-B cycle with a frozen test file, deltas reviewed); the three green companions pin that well-formed shapes (prose notes inside blocks, all-`@zkochan` lockfiles, packages-absent metadata) still parse quietly. Gate: 933 passed, "all checks passed".

#### Same-day advisory round (10 findings, 9 resolved)

The round's own review closed approved with 10 advisory findings; 9 were resolved in a follow-up pass (single-pass mode, behavior fixes demonstrated red inline first), one deliberately not:

- **Transport failures are loud by design — no retry.** The one WARNING deliberately not attacked (maintainer decision): a retry after an unclassified failure reintroduces the ambiguity the loud-transport fix closed (a retried request could land on a MITM's answer), is unbounded in latency, and makes the result non-deterministic. The correct layer for retries is the CI workflow, not the gate. Recorded here as a deliberate tradeoff.
- **The sweep no longer aborts at the first transport failure** (collect-then-fail): transport errors are collected like verification failures and the run ends with the complete picture — every package after a blip is still swept. Rate-limit pacing is kept between the collected failures.
- **Column-0 resolution ownership:** a column-0 resolution line inside a `packages:` entry block was owned silently — as a block's only resolution it parsed with the drifted hash while the legitimate integrity vanished from the map; beside a legitimate one it stole ownership and the guard named the wrong line. Ownership now requires block indentation (the raw count keeps counting column-0 so the drift is loud either way).
- **Transport vs malformed vs non-object answers:** `meta is None` is the transport sentinel ("could not reach"); a falsy-but-answered body or a truthy non-object body (`[1,2]`, `"abc"`, `42` — servable by a tampered or proxied registry, which would previously crash the sweep with an uncaught `AttributeError` and abort it) is "malformed package metadata". The helper's return type now reflects reality (`json.loads`'s shape, validated by the caller) instead of asserting a dict that bodies like these contradict.
- **Doc accuracy:** `verify_sigstore`'s docstring now states the real classification (network warning / `VerificationError` fatal / compat skippable / unclassified fatal) instead of promising "only VerificationError is fatal"; the sweep-coverage tests' docstrings describe current behavior, and the transport test asserts the exact message shape.

### 2026-09-19 — provenance verification gate: scoped packages went unverified (unreleased)

- **What was wrong:** `scripts/verify-provenance.py` reads `pnpm-lock.yaml` with a
  regex, and its quoted-name branch could not match scoped names (`'@sveltejs/kit@2.70.3':`) —
  the name class excluded `@`. The gate therefore verified only the unscoped
  subset: **0 of the 156 scoped entries carrying a resolution** were seen (the
  lockfile holds 277 scoped keys; the rest are `snapshots:`-style entries with no
  integrity line), and the CI job reported success over what it did read.
- **Impact:** the SLSA-provenance check is a supply-chain control, not an attack
  surface — nothing was exploitable. The failure mode is coverage: every scoped
  package (most of the ecosystem, including the SvelteKit and CodeMirror trees)
  was skipped silently, so a registry-level replacement of any of them would not
  have been caught by this job. It never reached a release as a known-good
  report; found 2026-09-19 while checking the repo's own lockfile readers after
  pnpm 12 began writing the lockfile as two YAML documents.
- **Fix:** the parser now sees 584 packages (was 428), 156 of them scoped. The
  quoted branch stops its name and version classes at `(` as well, so a
  peer-suffixed key (`'@keyv/bigmap@1.3.1(keyv@5.6.0)'`) can no longer have its
  suffix swallowed into the name or version.
- **The class, not just the instance:** a parser that skips what it cannot match
  would have hidden the next gap the same way. `find_unmatched_package_keys` now
  makes that a **loud failure**: any package-shaped key whose own entry block
  carries a resolution and did not match the pattern raises and names the keys,
  so a future lockfile-format drift fails the job instead of under-reporting.
- **Entry blocks, not a character window:** a key is matched with the body of its
  own entry (everything up to the next 2-space key). An earlier revision of the
  guard looked a fixed 300 characters past each key, which both crossed into the
  next entry (falsely reporting an unresolved key as a gap, failing CI on a valid
  lockfile) and missed a resolution block longer than the window. The parser and
  the guard now share one pass, so they cannot disagree about what was covered.
- **First tests for the script:** it had none — its filename contains a hyphen,
  so it cannot be imported by name. `backend/tests/test_verify_provenance.py`
  loads it from its path and covers scoped and unscoped entries, both lockfile
  documents, entries without a resolution, the `@zkochan` exclusion, both
  false-positive/false-negative window regressions, and the structural invariant
  that the real lockfile has no unparsed package entries.
- **Hardening after review (same day):** the gaps the round-1 fix left, plus the
  one the first hardening pass then introduced.
    - **A key line the splitter could not tokenize was invisible, not merely
      unreported:** `_ANY_KEY_RE`'s scalar class excluded `:`, so a quoted key
      containing one never became an entry block and vanished from the parsed set
      *and* the reported set at the same time. `:` is now allowed **only inside a
      quoted scalar**; an unquoted scalar still excludes it, because allowing it
      there made a non-key line (`  note: this is prose:`) a block start that cut
      the preceding entry's block short and hid its resolution — reintroducing the
      very class this change exists to close (reproduced during review:
      `_scan_lockfile` returned `({}, [])` for that shape). The terminator colon
      stays anchored to the line end, and the pattern still matches **941 blocks**
      on the real lockfile.
    - **The class, not only the instance:** `find_untokenized_package_keys` scans
      `content.splitlines()` **independently of the block splitter** and reports
      every package-shaped entry candidate the splitter cannot tokenize — a
      two-space key line ending in `:` that `_ANY_KEY_RE` rejects, or an entry
      written inline on its key line with its resolution. `parse_lockfile` refuses
      to proceed while that set is non-empty, so a splitter regression can no
      longer hide behind the splitter.
    - **A shape drift the walk never reached was silent success:** if the
      lockfile's indentation changed wholesale, every pattern read zero blocks
      and `main()` would print "Parsed 0 packages" and exit 0 — the gate verifying
      nothing while reporting success. `parse_lockfile` now fails when it parses
      zero packages and the walk never reached a `resolution:` line, while still
      accepting a file whose only entries were legitimately excluded (all
      `@zkochan`, as a test pins).
    - **A resolution without an `integrity:` line was skipped in silence too:**
      such a block can never be verified (pnpm writes them for tarball/commit
      resolutions), yet it was neither parsed nor reported. It is now reported as
      a parser gap — which is what the guard's own message always said it meant
      ("each carries a resolution"). `resolution:` and `integrity:` each appear
      584 times in the current lockfile, so nothing fires today.
    - **A non-`sha512-` integrity was skipped silently:** `_INTEGRITY_RE` only
      recognised the `sha512-` prefix, so an entry carrying any other algorithm
      was neither parsed nor reported. The value is now captured whatever its
      prefix, and `check_subject_hash` rejects anything but sha512 with a
      diagnostic naming the unsupported algorithm (`unsupported integrity
      algorithm 'sha1' …`) instead of a misleading lockfile-format message. All
      584 integrity lines in the current lockfile are sha512, so this is latent —
      it exists so that drift cannot hide, not because anything is broken today.
    - **The real-lockfile test asserted existence only**
      (`any(name.startswith("@"))`), so a partial regression passed and the one
      concrete anchor that test had was gone. It now holds the guard to its own
      documented contract on the real lockfile — every package-shaped entry block
      carrying a resolution is parsed or reported (`len(parsed) + len(unmatched) ==
      expected`) — **and** compares `len(parsed)` against a raw count of
      resolution-integrity lines taken straight from the file text. The raw anchor
      is the half that a *splitter* regression cannot satisfy, since the block-based
      bookkeeping derives `expected` from the same splitter. It fails loudly if
      `@zkochan`-scoped entries ever appear, rather than comparing wrong numbers.
- **`@zkochan`, documented instead of mysterious:** pnpm's own vendored
  `@zkochan/*` packages are published without provenance — the npm registry
  serves `dist.attestations` (with a provenance url) for e.g. `devalue@5.9.2` and
  `@sveltejs/kit@2.70.3`, but not for `@zkochan/js-yaml@0.0.11`, whose metadata
  shows `_from: file:zkochan-js-yaml-0.0.11.tgz`. The real lockfile has zero
  `@zkochan` entries and never had any (`git log -S "@zkochan" -- pnpm-lock.yaml`
  is empty), so the exclusion is defensive, and it is now a comment rather than a
  puzzle.

### 2026-09-18 — release-age window (cooldown) reduced from 10 to 4 days (unreleased)

- **What changed:** the minimum release age enforced by the age-gated dependency updater (`scripts/deps_update.py`'s `COOLDOWN_DAYS`, the `--help` default text, and `deps-update.yml`'s `workflow_dispatch` input default and `${COOLDOWN:-…}` shell fallback) was reduced from 10 days to 4 days, along with every live policy statement in `docs/github-actions-security.md` (Control 7, the Control 10 coverage table, the Maintenance Checklist), `docs/dependency-verification.md`, and `docs/syntax-highlighting.md`. A transition note in Control 7 records the change date itself.
- **A deliberate relaxation, not a drift:** decided explicitly on 2026-09-18 as a tradeoff between supply-chain protection depth and update freshness — a control was intentionally weakened, not lost. The exception-approval process around the gate (Control 7) is unchanged: merging a package before its window still requires explicit maintainer approval.
- **Dependabot gap closed in the same change:** `.github/dependabot.yml` now configures a native `cooldown: default-days: 4` on the `github-actions` update block. Previously Dependabot ran unconfigured (default 3-day cooldown on version updates); as before, Dependabot never applies a cooldown to security updates, so the manual release-date check before merging a Dependabot PR (Maintenance Checklist step 5) remains required.
- **Historical entries below are unaffected — this section exists to say so:** every cooldown-exception entry in Resolved CVEs above quantifies its exception against the window current on its date (10 days until 2026-09-18) — "9 short of the 10-day window" and its siblings read their original numbers deliberately. Socket's "Recently Published threshold: 7 days" in the Setup section is a distinct alert setting, unrelated to this window.

### 2026-08-13 — `scripts/deps_update.py` GitHub Actions log-injection hardening (release 0.5.2)

- `scripts/deps_update.py`: two GitHub Actions `::error::` prints (`_record_error()`, and `_resync_node_modules()`'s own) carried raw, unsanitized subprocess-derived text, unlike the PR-body path which already ran the same text through `_sanitize_reason()`. An embedded newline in multi-line stderr (routine for pnpm's `ERR_PNPM_*` blocks) could put a later line at the start of its own log line, letting it be parsed as an unrelated Actions workflow command (e.g. `::stop-commands::`) instead of inert log text. Both now collapse newlines the same way before printing, with a much wider limit than the PR body's markdown-table-cell truncation — only the newline-collapsing was ever the point.

### 2026-07-28 — extraction & reading-view privacy hardening (release 0.4.0)

- Extraction requests no longer identify Analecta or its maintainer to the sites they fetch — the previous User-Agent embedded a personal GitHub URL on every request. Requests now present as a generic, current Chrome on Linux, with a coherent header set (client hints, fetch metadata) to match, single-sourced from Electron's own bundled Chromium version so it can't go stale or drift from the browser Analecta actually ships with. See `docs/privacy.md`.
- Every URL the extraction pipeline fetches directly — the submitted URL, any redirect target encountered while fetching it, and remote image URLs discovered in already-fetched page content — is now resolved and validated before the request goes out: only `http(s)` schemes are allowed, the pipeline resolves the host itself, rejects the fetch if any resolved address isn't allocated for public use (loopback, link-local, private including RFC 1918 and CGNAT, reserved, unspecified, benchmarking/documentation ranges, or multicast — including an internal IPv4 address embedded in an IPv4-mapped, NAT64, or deprecated IPv4-compatible IPv6 address), and connects directly to one of the validated addresses it resolved rather than re-resolving the hostname for the connection — closing both a hostname string that encodes a blocked address in a form a naive check wouldn't parse (e.g. decimal/hex/octal IPv4) and a hostname whose DNS answer changes between the check and the connection. A resolved address that refuses or times out the connection falls back to the next validated address for that same hostname, so a dual-stack site isn't broken by one unreachable address family. TLS certificate verification still targets the original hostname. No such validation previously existed for this fetch. See `docs/electron-shell-security.md` § 7.
- A remote image that fails to download (network error, or a non-image response) now gets one retry and, if that also fails, is replaced with a local placeholder instead of keeping the original remote URL — a preserved URL would re-fetch, and re-expose the reading IP, every time the entry was reopened. A new "Localize remote images" action in Settings → Maintenance backfills any entries already saved with a live remote image reference from before this fix.
- The reading view's Content Security Policy no longer permits loading images from arbitrary remote (`https:`) hosts — only local vault assets and inline data. Since extraction already localizes every image, this closes off the one remaining path (a hand-edited or otherwise unusual entry) by which a remote image reference could silently re-fetch and expose the reading IP.
