# Socket Security — Analecta

Catalog of triaged Socket alerts, false-positive patterns, dismissed Dependabot alerts, and resolved CVEs for the Analecta project.

Referenced by `docs/github-actions-security.md` Controls 9 and 12.

---

## Setup

- **Organization:** E-zequiel
- **Plan:** Free tier (OSS Team plan requires public repo — request via socket.dev on repo publication)
- **CLI version:** `socket@1.1.99` — locked as a devDependency in `pnpm-lock.yaml` (SHA-512 verified)
- **BSM secret:** `SOCKET_SECURITY_API_TOKEN`
- **Org slug:** `Ezequiel` — must be passed explicitly as `--org Ezequiel` in all CLI calls. Without it the CLI enters an interactive org-discovery prompt, auto-selects the org in non-TTY, then exits with code 0 without running any scan (silent failure, confirmed 2026-06-07)
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

**Free plan limitation:** Socket only posts PR comments — it cannot block merges. Acceptable for solo-dev workflow; upgrade to OSS Team plan (unlocks PR blocking) when repo goes public.

---

## Known false positives (set to "Ignore" in dashboard)

### npm — Obfuscated code (false-positive pattern)

Socket's "Obfuscated code" detector flags packages that use split operations on large strings as an encoding optimization for lookup tables. This is not malware — it is a space-saving technique for static lookup data (HTML entities, tokenizer rules, compiler tables). Standard triage: check Socket's own analyst note; if no network exfiltration, eval injection, or credential access is identified → Ignore.

| Alert ID | Package | Reason |
|----------|---------|--------|
| SOCKET-EZEQUIEL-2 | `entities@4.5.0` | HTML entity lookup tables encoded as compact strings |
| SOCKET-EZEQUIEL-3 | `markdown-it@14.1.1` | Syntax/tokenizer rule tables |
| SOCKET-EZEQUIEL-5 | `svelte@5.55.7` | Compiler/runtime lookup tables |
| 2026-05-30 scan | `linkedom@0.18.12` (`package/worker.js`) | Web Worker DOM lookup tables — identical pattern. Optional dep of `defuddle@0.18.1`; Web Worker path is unused in Chromium context. |
| 2026-06-07 scan | `commander@9.5.0` | Transitive of electron-builder. Socket analyst: "conventional, non-obfuscated CLI framework component." 200M+ weekly downloads. |
| 2026-06-07 scan | `cssom@0.5.0` | Standard CSS parser. Socket analyst: "no evidence of malicious behavior." Minified lookup tables. |
| 2026-06-07 scan | `tiny-async-pool@1.3.0` | Standard concurrency utility. Socket analyst: "no evidence of malicious behavior, no hardcoded secrets." |
| 2026-06-07 scan | `electron-winstaller@5.4.0` (`wix.dll`) | Compiled Windows binary (WiX toolset). Binary DLLs always appear obfuscated to JS scanners. Windows-only; irrelevant to Linux-only build target. Lifecycle scripts blocked via `allowBuilds`. |
| 2026-06-07 scan | `graphology@0.26.0` (`specs/read.js`) | Direct dep (VaultGraph). Flagged file is a test spec. Socket analyst: "legitimate unit-test suite." |
| 2026-06-07 scan | `htmlparser2@10.1.0` | Standard HTML/XML tokenizer. 100M+ weekly downloads. Socket analyst: "non-malicious, standard tokenizer." |
| 2026-06-07 scan | `@typescript-eslint/eslint-plugin@8.60.0` | Dev dep, linting only. Official typescript-eslint org. |
| 2026-06-15 scan (16 alerts) | `nodejs-wheel-binaries@24.15.0` (PyPI) | Transitive of `basedpyright` (dev-only). Ships the compiled `node` binary across ~8 platform wheels; each binary flagged independently — same false-positive class as `electron-winstaller@wix.dll`. Confirmed absent from the shipped PyInstaller `--onedir` artifact (`backend.spec` has no `basedpyright`/`nodejs` references). |

### npm — `js-yaml@4.2.0` (monitor, not Ignore)

`obfuscatedFile`/`supplyChainRisk`, confidence 0.9. Flagged file is the library's minified UMD bundle. Socket analyst: "no clear evidence of malware (no network exfiltration, eval injection, or credential theft)"; only flags `__proto__` handling in YAML type resolution, which is normal object-assignment code. No upgrade exists — 4.2.0 is `dist-tags.latest` and the version already pinned via `pnpm-workspace.yaml overrides:`. **Status: "monitor" (not "Ignore") in Socket dashboard** — alert stays open for tracking. Do not re-propose setting it to "Ignore".

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
