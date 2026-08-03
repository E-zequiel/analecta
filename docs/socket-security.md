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

### 2026-08-03

| Package | CVE(s) | Fix |
|---------|--------|-----|
| `brace-expansion@1.1.16` / `2.1.2` / `5.0.8` | GHSA-rgw5-rvv9-x895 (CVE-2026-69152, HIGH 7.5) — bypasses the `maxLength` mitigation `5.0.8` shipped for GHSA-mh99-v99m-4gvg: two intermediate arrays (`values` in `expand_()`, and `expandSequence()`'s padded-sequence output) were never bounded by `maxLength`, so a ~25 KB input can still OOM-crash the process, and a ~400 KB input can stall the event loop for minutes. Fixed upstream by bounding both. | `overrides: {'brace-expansion@1': '1.1.18', 'brace-expansion@2': '2.1.4', 'brace-expansion@5': '5.0.9'}` in `pnpm-workspace.yaml`. **Cooldown exception:** all three released 2026-07-30, 4 days before this bump (6 short of the 10-day window) — approved explicitly given the CVSS 7.5 rating; EPSS is 0.003 and exposure is build-time tooling on our own glob patterns, not attacker-controlled input, so real-world urgency was low. **Retires the 2026-07-27 residual-risk carve-out below:** that entry assumed no 1.x/2.x backport existed for GHSA-mh99-v99m-4gvg; `1.1.17`/`2.1.3` were published afterward, and this bump adopts their successors (`1.1.18`/`2.1.4`) directly, so both the original CVE and its bypass are closed on all three lines. `dist.integrity` cross-checked against the npm registry and `pnpm view` for all three versions before adoption. |

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
