# GitHub Actions Security Policy

This document explains the security controls applied to all CI/CD workflows in this repository and the reasoning behind each decision.

---

## Threat Model

GitHub Actions workflows run arbitrary code with access to repository secrets. The primary threat vectors are:

1. **Supply chain compromise of third-party actions** — a malicious actor pushes a new release to a dependency action (e.g., `actions/upload-artifact`), which then exfiltrates secrets or tampers with build artifacts during the next workflow run.
2. **Secret exfiltration** — overly broad permissions or misplaced secrets allow a compromised step to read values it should not see.
3. **Fork poisoning** — a fork triggers a workflow that references secrets not available in the fork context, producing misleading errors or enabling a confused-deputy attack.
4. **Race conditions in release state** — concurrent release jobs produce incomplete GitHub Releases with partial artifact sets.

---

## Control 1: SHA-Pinned Action References

Every third-party (and first-party) action is pinned to an immutable commit SHA, never to a mutable tag (`@v4`, `@main`, `@latest`).

```yaml
# Correct: immutable
uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4.3.1

# Wrong: mutable tag — silently changes on the next release
uses: actions/checkout@v4
```

**Why tags are insufficient:** Tags in Git are mutable pointers. A repository owner — or an attacker who has compromised a maintainer account — can move a tag to a different commit without any indication to consumers. Pinning to a SHA means the code that runs is exactly the code that was audited when the SHA was recorded.

### Current action inventory

| Action | Tag | SHA | Last verified |
|--------|-----|-----|---------------|
| `actions/checkout` | `v7.0.0` | `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0` | 2026-07-03 |
| `jdx/mise-action` | `v4.2.0` | `e6a8b3978addb5a52f2b4cd9d91eafa7f0ab959d` | 2026-07-03 |
| `bitwarden/sm-action` | `v3.0.0` | `27c0c9dcab679d7250dbab91227c85b49ffa5e0f` | 2026-05-08 |
| `actions/attest-build-provenance` | `v4.1.1` | `0f67c3f4856b2e3261c31976d6725780e5e4c373` | 2026-07-01 |

### How to resolve a SHA for a new action or version

```bash
# 1. Find the tag ref
curl -s https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{tag} \
  | jq '{sha: .object.sha, type: .object.type}'

# 2. If type == "tag" (annotated), dereference to the underlying commit
curl -s https://api.github.com/repos/{owner}/{repo}/git/tags/{sha} \
  | jq '.object.sha'

# If type == "commit", the first SHA is the one to use directly.
```

### Automated SHA updates

`.github/dependabot.yml` is configured to open weekly PRs that update pinned SHAs to new releases. Each PR must be reviewed before merging — Dependabot PRs are not auto-merged.

---

## Control 2: Principle of Least Privilege

The workflow-level `permissions` block is set to `{}` (no permissions). Each job then declares only the permissions it actually needs.

```yaml
# Workflow level — deny all by default
permissions: {}

jobs:
  build-linux:
    permissions:
      contents: write  # Minimum needed to create a GitHub Release
```

`contents: write` is required because the release job uses `gh release create` to create the release and upload `.deb`, `.AppImage`, and `.rpm` assets via the GitHub Releases API.

`pull-requests: write` (plus `contents: write`) is required in the `commit-and-pr` job of `deps-update.yml` so that `git push` and `gh pr create` can open a PR for the weekly dependency update branch. These permissions are scoped to the `commit-and-pr` job only. The preceding `update` job — which executes new package code via `check.sh` — runs with `permissions: {}` and `persist-credentials: false` so no `GITHUB_TOKEN` is present in `.git/config` while untrusted code runs. See Control 7 for the full privilege-split rationale.

`contents: read` is required for every job that uses `actions/checkout` to clone a private repository. This includes the `socket` job — without it, `actions/checkout` cannot authenticate and the clone fails with "Repository not found". On a public repository this permission is redundant (checkout requires no token), but it is harmless and kept for consistency.

The only exception is `build-linux` in `release.yml`, which also declares `id-token: write` and `attestations: write` for build provenance attestation — see Control 14. No other permission (`packages`, etc.) is granted to any job.

### Known platform limitation

`contents: write` is broader than strictly needed — it also grants write access to repository code, branches, and refs, not just releases. GitHub's `GITHUB_TOKEN` has no dedicated `releases` scope; release operations are bundled under `contents`. This is a GitHub platform constraint with no workaround short of provisioning a GitHub App token (via `actions/create-github-app-token`), which would be over-engineering for this project at its current scale.

### `github.token` vs `secrets.GITHUB_TOKEN`

Both expressions resolve to the same auto-generated job token, but `${{ github.token }}` is the canonical form. It signals clearly that this is the runner's automatic token — not a user-managed secret — and is preferred by security scanners (OSSF Scorecard). All workflows in this repo use `github.token`.

---

## Control 3: Secrets Scoping

### Secrets used in release.yml

| Secret | Source | Used by | Purpose |
|--------|--------|---------|---------|
| `github.token` | Auto (runner) | `gh release create` | Create GitHub Release, upload assets |
| `BWS_ACCESS_TOKEN` | GitHub secret | `sm-action` | Read-only BSM machine account token |
| `SOCKET_SECURITY_API_TOKEN` | BSM via `sm-action` | `pnpm exec socket` | Socket.dev dependency security scan |

`SOCKET_SECURITY_API_TOKEN` is **not** stored as a GitHub secret. It lives in Bitwarden Secrets Manager and is injected at runtime by `bitwarden/sm-action`. `BWS_ACCESS_TOKEN` is a read-only machine account token scoped to this project only; if compromised, it cannot create or modify secrets.

`github.token` auto-expires when the job ends. `BWS_ACCESS_TOKEN` is low-blast-radius (read-only BSM access).

**Linux electron-builder does not require code signing.** `electron-updater` verifies release downloads via SHA-512 checksums embedded in `latest-linux.yml` — no private key needed.

---

## Control 4: Repository Guard

All jobs include a base repository check:

```yaml
if: github.repository == 'E-zequiel/analecta'
```

This prevents forked repositories from triggering jobs on their own pushes where secrets would be absent and produce confusing failures.

### Fork PR behaviour with `on: pull_request`

When an external contributor opens a PR from a fork, GitHub runs `on: pull_request` workflows in the context of the **base repository** — so `github.repository` evaluates to `'E-zequiel/analecta'` and the base guard alone does **not** skip fork PR runs. GitHub's own protection for this trigger is that secrets are stripped entirely: `BWS_ACCESS_TOKEN` arrives as an empty string, and any step that requires it fails.

For jobs that should only run on internal PRs (branches in this repo, not forks), the base guard is extended with:

```yaml
if: >-
  github.repository == 'E-zequiel/analecta' &&
  github.event_name == 'pull_request' &&
  github.event.pull_request.head.repo.full_name == github.repository
```

`head.repo.full_name == github.repository` is false for every fork PR, so the job is skipped with a clean neutral result rather than failing with an authentication error. The `socket` job in `ci.yml` uses this extended condition because it requires `BWS_ACCESS_TOKEN` to retrieve `SOCKET_SECURITY_API_TOKEN` from BSM.

### Dependabot PR behavior

Dependabot opens PRs from branches in the same repository, so `head.repo.full_name == github.repository` evaluates to **true** — the extended fork guard alone does not skip the job. However, GitHub deliberately strips secret access for Dependabot-authored PRs: `BWS_ACCESS_TOKEN` resolves to an empty string, causing `bitwarden/sm-action` to fail with "Access token is required".

The job-level guard cannot distinguish Dependabot PRs from regular internal PRs. The fix is applied at the step level:

```yaml
- uses: bitwarden/sm-action@<SHA>
  if: github.event.pull_request.user.login != 'dependabot[bot]'
  with:
    access_token: ${{ secrets.BWS_ACCESS_TOKEN }}
    ...

- name: Socket dependency scan
  if: github.event.pull_request.user.login != 'dependabot[bot]'
  run: mise exec -- pnpm exec socket ci --org Ezequiel --no-interactive
  ...
```

**Why `user.login`, not `github.actor`:** If a maintainer re-runs a failed workflow, `github.actor` changes to the maintainer's username — but re-running a Dependabot PR does not grant it secret access. The secret steps would fire again and fail for the same reason. `github.event.pull_request.user.login` is the PR *author*, which is immutable across re-runs.

**Why `user.login` cannot be spoofed:** The `[bot]` suffix in GitHub usernames is reserved for GitHub App accounts and cannot be registered as a regular user account.

**Result:** The `socket` job still runs (checkout + install) and reports **success**, satisfying the required status check. Only the two secret-dependent steps are skipped. Socket scan coverage for Dependabot PRs is provided by the **Socket GitHub App** (a native GitHub integration that runs independently of `BWS_ACCESS_TOKEN`). The CLI step that is skipped adds CI enforcement via exit code; the underlying dependency analysis still runs.

For CLI-level enforcement on a Dependabot PR before merging, use the manual scan workflow described in Control 13.

---

## Control 5: Concurrency Lock

```yaml
concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: false
```

`cancel-in-progress: false` is deliberate. Cancelling a release job mid-run leaves a GitHub Release in a partial state — some assets uploaded, some missing — which requires manual cleanup and can confuse users downloading from the draft. Two release tags for the same ref cannot be pushed simultaneously in normal workflow, so in practice this lock only protects against accidental double-push scenarios.

---

## Control 6: Dependency Integrity at Build Time

| Layer | Mechanism |
|-------|-----------|
| Python | `uv sync --frozen` — fails if `uv.lock` is out of date; no network resolution |
| Node.js | `pnpm install --frozen-lockfile` — fails if `pnpm-lock.yaml` is out of date |

The sidecar build (`scripts/build_sidecar.py`) runs inside the locked Python environment established by `uv sync --frozen`. PyInstaller and all its dependencies are resolved deterministically.

---

## Control 7: Age-Gated Dependency Updates

`.github/workflows/deps-update.yml` runs on a weekly schedule (Thursdays at 12:00 UTC) and via `workflow_dispatch` (repo write-access users only). It updates Python and Node lock files, but applies a **cooldown gate** before touching any package:

1. Detect outdated packages via `uv` and `pnpm outdated`.
2. Query the upstream registry for the new version's release date (PyPI JSON API, npm registry `time` map).
3. **Skip** any package released fewer than 10 days ago — this prevents "day-zero supply chain" attacks where a malicious release is injected before the community has time to detect and report it.
4. Apply updates selectively: Python packages via `uv lock --upgrade-package <pkg>`; Node packages via `pnpm add <pkg>@<latest> --save-exact --filter <workspace>`, followed by a direct re-check that strips any range operator `--save-exact` failed to remove, re-syncing the lockfile if it had to correct one (see `docs/dependency-verification.md`). (`pnpm update` is a no-op for exact-pinned packages — it only moves within the declared range, but exact pins have no range to move within.)
5. Run `check.sh` to confirm all tests and static checks still pass; revert lockfiles if they fail.
6. Open a pull request summarising what was updated and what was held back (with eligibility dates).

**Privilege split (two-job design):** The workflow uses two jobs to prevent new package code from executing inside a job that holds write credentials.

- `update` job: `permissions: { contents: read }` + `persist-credentials: false`. `contents: read` is required for `actions/checkout` on a private repo; `persist-credentials: false` removes the token from `.git/config` immediately after clone, before any package code runs. Even if a package reads `$GITHUB_TOKEN` from the environment, it only has read-only access.
- `commit-and-pr` job: `permissions: { contents: write, pull-requests: write }`. Downloads the verified lockfiles as a GitHub Actions artifact and commits them. Never executes package code.

This ensures that if a package that cleared the 10-day cooldown contains a malicious install or runtime payload, it cannot read or use the repository write token.

**Bypass via `workflow_dispatch`:** The `cooldown` input (default `10`) can be set to `0` to bypass the gate. `workflow_dispatch` requires repository write access, so this bypass is not available to external contributors.

**Exception approval:** Any update that clears the cooldown gate early — whether by `cooldown=0` dispatch, by merging a Dependabot PR within its minimum-age window, or by any other means — requires explicit maintainer approval before merging. Do not self-certify an exception even when CVE urgency justifies a shorter window; surface it and get a confirmation first.

**`--ignore-scripts` in the install step:** `deps-update.yml` installs the *current* (pre-update) lockfile with `pnpm install --frozen-lockfile --ignore-scripts` before running `deps_update.py`. Lifecycle scripts (notably `electron`'s binary download) are blocked because the `update` job only needs `pnpm outdated` and `pnpm add` (via `deps_update.py`) to enumerate and bump packages — the Electron binary is not required there. The `update` job has no write token anyway (`permissions: { contents: read }`), so the blast radius of any lifecycle script is limited to the runner itself.

**Provenance note:** Lock file hashes provide **integrity** (package content matches the recorded hash). SLSA provenance attestation for npm packages is implemented in the `verify-provenance` CI job (see Control 10). Python provenance remains unimplemented — PyPI-side ecosystem support is still immature. This is a known gap, not an oversight.

**Dependabot PR caveat:** This automated cooldown applies only to packages updated by `deps-update.yml`. Dependabot has no native minimum-age setting and can open a PR for a version published hours earlier — the `schedule.interval: weekly` raises the average buffer but does not guarantee a 10-day minimum. The cooldown must be verified manually before merging any Dependabot package-version PR (see Maintenance Checklist).

---

## Control 8: Lockfile-Pinned CLI Tools

Tools executed via `pnpm dlx`, `npx`, `yarn dlx`, or `npm exec` are downloaded from the npm registry at the moment the step runs — no hash is verified against any lockfile. If a step that calls one of these commands also has secrets in its `env:` block, a compromised package version can read the process environment and exfiltrate those secrets.

This attack surface was demonstrated by the **Mini Shai-Hulud campaign (2026-05-19)**, in which 323 packages in the @antv npm ecosystem were compromised in an automated burst. The Socket CLI — a dependency security scanner — was the ironically named example in this repository: `pnpm dlx socket ci` ran with `SOCKET_SECURITY_API_TOKEN` in the environment. A compromised `socket` package release would have been the exfiltration vector.

### Fix: install as a lockfile-managed devDependency

```bash
# 1. Add the tool at the version matching its last verified GitHub tag
pnpm add -D -w <tool>@<version>

# 2. Verify the lockfile hash against the npm tarball directly
curl -sL https://registry.npmjs.org/<tool>/-/<tool>-<version>.tgz \
  | openssl dgst -sha512 -binary | base64
# Output must match the integrity field in pnpm-lock.yaml
```

**Scope of this check:** the tarball comparison detects the case where a maintainer's npm account is compromised and they publish a malicious version *without* a corresponding git tag — the npm content diverges from the audited source. It does **not** protect against a registry-level compromise where both the tarball and its hash are served consistently; verifying a tarball against the same registry that served it is circular for that threat. Registry-level threats are addressed by provenance attestations (Control 10).

Once pinned:
- Replace `pnpm dlx <tool>` with `pnpm exec <tool>` in workflow files.
- If the job does not already run `pnpm install --frozen-lockfile`, add that step before the tool invocation.

Every subsequent `pnpm install --frozen-lockfile` in CI will verify the SHA-512 hash of the downloaded tarball against the lockfile before execution.

### Version selection policy

Only pin to a version that has a **verified tag** in the tool's GitHub repository. npm versions that exist on the registry without a corresponding GitHub tag cannot be traced to audited source code — use the last verified release instead.

### Never install globally in CI

`pnpm add -g <tool>` bypasses the lockfile and reintroduces the unverified download pattern. Always use workspace `devDependencies`.

### Current CLI tool inventory

| Tool | Version | Verified GitHub tag | SHA-512 verified |
|------|---------|--------------------|--------------------|
| `socket` | `1.1.99` | [`v1.1.99`](https://github.com/SocketDev/socket-cli/releases/tag/v1.1.99) | Yes — matches `pnpm-lock.yaml` integrity field |

---

## Control 9: Local Dependency Scan Protocol

The Socket CI job (`ci.yml`) only runs on PRs that change a lockfile. This means new packages installed locally on a branch — between `pnpm add` and the PR — are unscanned until the PR is opened. Running `scripts/socket-audit.sh` locally closes that gap.

### When to run

| Trigger | Action |
|---------|--------|
| After any `pnpm add` or `uv add` | Run `./scripts/socket-audit.sh` before committing |
| After running `scripts/deps_update.py` locally | Run `./scripts/socket-audit.sh` before committing or pushing (see note below) |
| After the weekly `deps-update.yml` PR lands | Run locally before merging (in addition to `check.sh`) |
| Before opening a PR that touches `pnpm-lock.yaml` or `backend/uv.lock` | Run as a pre-PR gate |

**`deps_update.py` runs fully script-free.** `_apply_node_package()`'s `pnpm add`, the exact-pin resync `pnpm install`, and `pnpm dedupe` all pass `--ignore-scripts`. This isn't about `socket-audit.sh` timing — it closes a separate gap: the `deps-update.yml` "update" job's only output is `pnpm-lock.yaml`/`package.json`/`pr-body.md` bytes, picked up by `upload-artifact` and then committed and force-pushed by a second job holding `contents: write` and `pull-requests: write`. Any lifecycle script executing on that disk before the upload could tamper with what gets pushed, regardless of whether the package it belongs to is itself malicious. `allowBuilds` restricts *which* packages may run scripts on a normal install; `--ignore-scripts` here means none run at all in this job, for any package, which is what a job whose output feeds a privileged pusher should do — and costs nothing, since nothing `check.sh` exercises (type-checking, lint, Vite build) launches Electron or needs its downloaded binary.

**Local limitation — manual runs only:** a developer running `pnpm add <pkg>@<latest> --save-exact` by hand (not via `deps_update.py`) does not get this protection automatically. `electron`'s lifecycle script (the only one permitted by `allowBuilds`) may run before `socket-audit.sh` can scan the updated lockfile. Running the scan immediately after — and not pushing until it is clean — is the correct compensating control for that path. The hard enforcement gate regardless of path is CI: the `socket` job in `ci.yml` runs with `--ignore-scripts` and its result gates `check-frontend` and `test-backend` via `needs:` (see Control 12).

### How to run

```bash
./scripts/socket-audit.sh
```

`bws run` injects `SOCKET_SECURITY_API_TOKEN` at runtime from Bitwarden Secrets Manager — no token is written to disk. Requires the `bws` CLI and a valid `BWS_ACCESS_TOKEN` in the shell environment.

**Org picker:** If the script opens an interactive prompt asking which organization to use, set `SOCKET_ORG` in your shell profile to skip it on future runs:

```bash
# ~/.zshrc or ~/.bashrc
export SOCKET_ORG=YourOrgName
```

The script passes `--org "$SOCKET_ORG"` only when the variable is set; it falls back to auto-discovery otherwise.

The script calls `pnpm exec socket` (lockfile-pinned, `socket@1.1.99`) — never `pnpm dlx`. This matters because `bws run` injects `SOCKET_SECURITY_API_TOKEN` into the process environment; a `dlx`-downloaded package could exfiltrate it.

### Interpreting results

- **No issues:** proceed.
- **Known false positives:** check the false-positive catalog in `docs/socket-security.md` before acting.
- **New alert:** investigate before committing. If it is a confirmed false positive, add it to the catalog with a justification. If it is a real risk, do not merge.

### Quota

500 API calls/hour on the free plan. Do **not** add this script to `check.sh` — that runs on every change and would exhaust the quota.

---

## Control 10: Provenance Attestation Verification

The `verify-provenance` CI job (`scripts/verify-provenance.py`) verifies npm SLSA provenance attestations for every package in `pnpm-lock.yaml` that has one.

### Why lockfile integrity alone is insufficient at write time

`pnpm install --frozen-lockfile` verifies packages against the SHA-512 hashes in `pnpm-lock.yaml` (Control 6). This is strong for **read-time** verification. The gap is **write-time**: when `pnpm add <pkg>` is run, pnpm fetches the tarball and the registry's advertised hash in the same request, then writes both to the lockfile. A registry-level compromise could serve a malicious tarball with an internally consistent hash — future `--frozen-lockfile` installs would trust it.

Lockfile integrity is still essential and cannot be dropped; Control 10 adds an independent anchor for the subset of packages that publish provenance.

### How it works

1. **Attestation bundle download:** queries npm registry for `dist.attestations.url` per package.
2. **Subject hash check:** decodes the DSSE payload from the Sigstore bundle and compares the attested SHA-512 to the pnpm-lock.yaml integrity entry. This is the first check: if an attacker compromised the package at `pnpm add` time, the installed hash in the lockfile would differ from the attested hash.
3. **Sigstore signature verification:** calls `sigstore.verify.Verifier.production().verify_dsse()` with an `OIDCIssuer` policy requiring a GitHub Actions signing identity. This verifies:
   - The signing certificate was issued by Fulcio CA (Sigstore's certificate authority).
   - The certificate's OIDC issuer is `https://token.actions.githubusercontent.com`.
   - The bundle is included in the Rekor transparency log (tamper-evident, append-only).

Steps 2 and 3 together provide an independent verification anchor: a registry-level MITM cannot forge a Rekor entry retroactively.

### Residual gap (documented, not an oversight)

| Scenario | Covered |
|---|---|
| Package was compromised after publication (hash mismatch with attested hash) | ✅ |
| Fake attestation for malicious package (Sigstore signature invalid) | ✅ |
| Package has no attestation (~60% of tree) | ❌ — covered only by Socket scan + lockfile integrity |
| Rekor + Fulcio infrastructure compromise (state-level attack) | ❌ — no practical mitigation exists |
| Write-time registry compromise for packages WITH attestation | ✅ (subject hash check catches it) |
| Write-time registry compromise for packages WITHOUT attestation | ❌ — mitigated only by Socket scan + minimum-age cooldown (10 days for routine updates; shorter minimum for active-CVE exceptions — see Maintenance Checklist) |

### Coverage

Provenance attestation adoption in the npm ecosystem is incomplete. Packages without attestations are skipped without error — they are covered by other controls. Measured coverage as of 2026-05-31:

- **52 of 386 installed packages** (13%) have SLSA provenance attestations.
- Among key application-level deps: `svelte`, `vite`, `electron-builder`, `electron-updater`, `rolldown`, `socket` have attestations. `sigma`, `graphology`, `markdown-it`, `defuddle`, `electron`, `eslint`, `prettier`, `typescript` do not. `defuddle` is a diagnostic-only devDependency that never ships in a packaged build (see `docs/defuddle-decision.md`) — its missing attestation has no production-attestation implication the way the others' does; it's still tracked under the same verification protocol (`docs/dependency-verification.md`), just for a lower-stakes reason (dev-machine supply-chain integrity, not shipped-artifact provenance).
- The 87% without attestations are primarily older utility packages (`acorn`, `semver`, `yargs`, etc.) and packages that predate npm's provenance feature.

Coverage is expected to grow as the ecosystem adopts `--provenance` publishing. The script automatically picks up new attestations without code changes.

### Implementation

```yaml
# .github/workflows/ci.yml
verify-provenance:
  runs-on: ubuntu-22.04
  permissions:
    contents: read
  steps:
    - uses: actions/checkout@<SHA>
    - uses: jdx/mise-action@<SHA>
    - name: Set up provenance verification environment
      run: |
        mise exec -- uv venv .venv-provenance
        mise exec -- uv pip install --require-hashes \
          -r scripts/requirements-provenance.lock \
          --python .venv-provenance
    - name: Verify npm provenance attestations
      run: .venv-provenance/bin/python scripts/verify-provenance.py
      env:
        PYTHONUNBUFFERED: "1"
```

No new external GitHub Actions are required. `sigstore` and its 31 transitive dependencies are installed from `scripts/requirements-provenance.lock`, which is committed to the repo and contains SHA-256 hashes for every package. `uv pip install --require-hashes` enforces that all installed wheels match those hashes — equivalent to `uv sync --frozen` for the backend.

**Updating sigstore:** regenerate the lockfile with:
```bash
echo "sigstore==<new-version>" | mise exec -- uv pip compile --generate-hashes - -o scripts/requirements-provenance.lock
```
Run `./scripts/socket-audit.sh` after regenerating (sigstore itself is an npm-adjacent tool, but its Python deps should be scanned via the backend audit path).

**Known Rekor entry-type limitation:** sigstore 4.x cannot verify the integrated timestamp for Rekor entry types newer than `dsse/hashedrekord 0.0.1`. Affected packages receive a compatibility warning (`⚠ Rekor entry type not supported`) rather than a failure. The subject hash check (step 2 above) still passes for these packages, preserving the key supply-chain guarantee. This limitation will resolve as sigstore adds support for newer entry types.

---

## Control 11: Lifecycle Script Restriction

Every npm package can declare `preinstall`, `install`, and `postinstall` scripts in its `package.json`. By default, pnpm runs these scripts for all installed packages, meaning any package in the transitive dependency tree can execute arbitrary code with full user privileges on every `pnpm install`.

`pnpm-workspace.yaml` restricts this to an explicit allowlist via `allowBuilds`:

```yaml
allowBuilds:
  electron: true          # must download platform binary via install.js
  electron-winstaller: false  # explicitly blocked
```

Only packages in this allowlist are permitted to run lifecycle scripts. All others — including packages with malicious postinstall scripts — are blocked.

### Current allowlist and rationale

| Package | Script | Why allowed |
|---------|--------|-------------|
| `electron` | `install.js` — downloads Electron binary via `@electron/get` | Required; no alternative download mechanism |
| `electron-winstaller` | `select-7z-arch.js` | Blocked — Windows-only, irrelevant on Linux |

`esbuild` was removed from the allowlist on 2026-05-31 — Vite 8 uses Rolldown, so esbuild is not in the dependency tree.

### Verifying allowlist entries

When a package is in `allowBuilds`, its lifecycle script executes on every `pnpm install`. Before adding or retaining an entry, verify the script matches the published npm source:

```bash
# Hash the installed script
sha256sum node_modules/.pnpm/<pkg>@<version>/node_modules/<pkg>/install.js

# Hash the same file from the npm registry tarball (independent fetch)
curl -sL "https://registry.npmjs.org/<pkg>/-/<pkg>-<version>.tgz" \
  | tar -xzO package/install.js | sha256sum

# Hashes must match exactly.
```

Do **not** use `raw.githubusercontent.com` for this check — it is flagged as malicious by some security tools (it's GitHub's CDN for raw content, legitimately used but also widely used by malware as free hosting). Use the npm registry tarball or `api.github.com` instead.

**Verified 2026-05-31:** `electron@42.1.0` `install.js` — sha256 `8a6e96a324147490ad5d474e2c6deec608018a90032e80ec8e3ae97a6cd02851` — matches npm registry tarball.

### Auditing the full installed tree

To discover which packages in the installed tree actually have lifecycle scripts (and therefore which ones are silently blocked by the allowlist):

```python
import json, os
base = "node_modules/.pnpm"
for pkg_dir in os.listdir(base):
    nm = os.path.join(base, pkg_dir, "node_modules")
    if not os.path.isdir(nm):
        continue
    for name in os.listdir(nm):
        pjson = os.path.join(nm, name, "package.json")
        if os.path.exists(pjson):
            d = json.load(open(pjson))
            lc = {k: v for k, v in d.get("scripts", {}).items()
                  if k in ("install", "preinstall", "postinstall")}
            if lc:
                print(f"{d.get('name')}@{d.get('version')} → {lc}")
```

As of 2026-05-31: only `electron-winstaller@5.4.0` has a lifecycle script in the installed tree, and it is blocked.

---

## Control 12: Scan Ordering — Scan Before Lifecycle Scripts

Controls 8, 9, and 11 together establish *which* packages run scripts and *when* the lockfile is scanned. This control addresses the sequencing gap: even with `allowBuilds` restricted to `electron`, a compromised version of `electron` introduced into the lockfile would have its `install.js` (binary download) execute during `pnpm install` **before** `socket ci` could flag it, unless install order is explicitly managed.

### CI: `socket` job installs with `--ignore-scripts`

The `socket` job in `ci.yml` installs dependencies with:

```yaml
- name: Install dependencies
  run: mise exec -- pnpm install --frozen-lockfile --ignore-scripts
```

`--ignore-scripts` blocks all `preinstall`, `install`, and `postinstall` scripts for every package — including `electron`. pnpm still downloads tarballs and populates `node_modules` (linking happens via symlinks, not scripts), but no script code executes. The security property is **no script execution before the scan**, not "no download."

`socket` (the CLI, `v1.1.99`) is a pure-JavaScript tool with no native binary download; it runs correctly under `--ignore-scripts`. Verified locally: `pnpm install --frozen-lockfile --ignore-scripts && pnpm exec socket --version` → `1.1.99`.

After install, `socket ci` reads `pnpm-lock.yaml` and `package.json` directly — it does not require `node_modules` to be populated beyond the tool itself. The scan therefore reflects the full lockfile diff against the base branch before any allowlisted script has run.

### CI: downstream jobs are gated on `socket` passing

`check-frontend` and `test-backend` declare `needs: [socket]` so they only execute after the scan succeeds. Each job runs `./scripts/check.sh <layer>` — the full quality gate for that layer:

- `test-backend` → `./scripts/check.sh backend`: ruff format, ruff check, basedpyright, pytest
- `check-frontend` → `./scripts/check.sh frontend`: prettier, ESLint, tsc, svelte-check, Vite production build

This means the entire static analysis and test suite for both layers is CI-enforced, not
just a subset of tools.

```yaml
check-frontend:
  needs: [socket]
  if: >-
    !cancelled() &&
    github.repository == 'E-zequiel/analecta' &&
    (needs.socket.result == 'success' || needs.socket.result == 'skipped')
  permissions:
    contents: read
```

Key details:

- `!cancelled()` (not `always()`) — a cancelled run does not force downstream jobs to fire.
- `needs.socket.result == 'skipped'` — on `push` to `main`, the `socket` job does not run (its `if:` excludes non-PR events). Without this clause, `check-frontend` and `test-backend` would be permanently skipped on every main push.
- `verify-provenance` is **not** gated on `socket` — it installs only Python packages via `uv`; it never calls `pnpm install`.

**Accepted trade-off:** a transient Socket API or BSM outage causes `socket` to fail, which skips `check-frontend` and `test-backend`. The Python test suite and frontend checks are thus coupled to Socket availability. This is intentional — it prevents a merge from slipping through without a scan result.

### CI: `socket` as a required status check

The `needs:` coupling above prevents wasting runner minutes, but the actual merge gate is the **branch protection rule** in GitHub → Settings → Branches → `main`. Add `socket` to the required status checks list there. A skipped required check is treated as "not passed" by GitHub — this is the hard block on merging when socket fails.

### CI: `deps-update.yml` — privilege split + `--ignore-scripts`

`deps-update.yml` uses two jobs to isolate the privilege boundary:

- The `update` job runs with `permissions: { contents: read }` and `persist-credentials: false`. `contents: read` is the minimum for `actions/checkout` on a private repo. `persist-credentials: false` removes the token from `.git/config` immediately after clone, before new package code executes (via `check.sh`). Even if a package reads `$GITHUB_TOKEN` from the environment, it has read-only access — it cannot push or create PRs.
- The `commit-and-pr` job holds `contents: write` + `pull-requests: write` but only downloads the pre-verified lockfile artifact and commits it — it never installs or executes package code.

This split means that even if a package that cleared the 10-day cooldown contains a malicious payload, it cannot access or exfiltrate the repository write token. The blast radius of a compromise in the `update` job is limited to the runner instance itself.

### Local: advisory workflow (`deps_update.py` does not pass `--ignore-scripts`)

`scripts/deps_update.py` calls `pnpm add <pkg>@<latest> --save-exact` for each outdated Node package. This command supports `--ignore-scripts` but the script does not pass it, so `electron`'s postinstall may run before `socket-audit.sh` can scan the result.

The compensating control is sequencing discipline:

```
deps_update.py           ← updates lockfile + installs (electron postinstall may run)
./scripts/socket-audit.sh   ← scans the updated lockfile
<review output>
git add pnpm-lock.yaml && git commit   ← only if scan is clean
```

Do not push before the scan completes. The CI gate (`socket` job) is the hard enforcement; local is advisory.

### Fork PR caveat (relevant when the repo goes public)

The `socket` job is gated on:

```yaml
github.event.pull_request.head.repo.full_name == github.repository
```

Fork PRs fail this check — `socket` is skipped. If `socket` is a required status check and the repo is public, fork PRs will have a permanently unresolvable required check (skipped ≠ passed). Mitigations:

- Remove `socket` from required checks (weakens the gate for internal PRs).
- Or configure GitHub to allow fork PRs to use the Actions secrets needed for socket (requires careful scope review).

This is tracked in `project_public_repo_checklist.md`.

---

## Control 13: Manual Socket Scan (`socket-manual.yml`)

The `socket` job in `ci.yml` only runs on pull request events — it never runs on `push` to `main`, and its secret-dependent steps are skipped for Dependabot PRs (Control 4). `socket-manual.yml` provides a `workflow_dispatch`-triggered scan that can be run at any time against any branch, filling both gaps.

### Trigger access

`workflow_dispatch` can only be triggered by users with **write access** to the repository via the GitHub UI, GitHub CLI, or GitHub REST API. It cannot be triggered by any automatic GitHub event (push, pull_request, schedule, etc.). Dependabot, fork contributors, and read-only collaborators cannot trigger it.

The effective access boundary is: *anyone with repository write access*. In a single-maintainer repository, this means only the repository owner. If collaborators are ever added, this guarantee extends to them automatically — no additional configuration is needed.

### Ref-trust design: dispatch-on-main with `ref` input

The intuitive dispatch model — trigger the workflow *on the branch being scanned* — carries a security risk: GitHub reads the workflow file **and the action SHAs from the dispatched ref**, not from `main`. A branch that modifies `socket-manual.yml` or updates an action pin would run its own version of the workflow with full `BWS_ACCESS_TOKEN` access. `.mise.toml` is also read from the checked-out tree, so a tampered toolchain version would be used.

The safe design is **dispatch on `main`, checkout target ref**:

```yaml
on:
  workflow_dispatch:
    inputs:
      ref:
        description: 'Branch or SHA to scan'
        required: true
        default: 'main'

steps:
  - uses: actions/checkout@<SHA>   # this SHA comes from main's workflow file
    with:
      ref: ${{ inputs.ref }}       # the dependency tree scanned comes from here
```

With this pattern:
- The workflow definition and all action SHAs always come from `main` (trusted).
- A branch that rewrites `socket-manual.yml` or modifies an action pin cannot affect the code that runs when you dispatch on `main`.
- `pnpm-lock.yaml`, `package.json`, and `.mise.toml` still come from the target ref via the `checkout` step — this is intentional (you want to scan that branch's dependency tree) but it means the toolchain config is not independently anchored to `main`.

**Operational rule:** only dispatch against refs whose toolchain config and lockfile you trust — your own branches and Dependabot's registry-sourced bumps (which modify package versions, not `.mise.toml` or workflow files). This is the real control for the toolchain-tampering risk; dispatch-on-main alone does not close it.

**Usage:** in GitHub → Actions → "Socket Manual Scan" → "Run workflow" → leave the branch as `main`, enter the branch name to scan in the `ref` input.

### Security properties

| Property | Value |
|----------|-------|
| Trigger | Manual only (`workflow_dispatch`) — no auto-trigger path |
| Who can trigger | Repository write-access users |
| Workflow definition source | Always `main` (dispatch-on-main pattern) |
| Action SHAs source | Always `main` |
| Dependency tree source | `inputs.ref` (the branch being scanned) |
| `.mise.toml` source | `inputs.ref` — **not** anchored to `main`; see operational rule above |
| Secret access | `BWS_ACCESS_TOKEN` — same blast radius as the `socket` job in `ci.yml` |
| `--ignore-scripts` | Yes — no lifecycle script executes before the scan |
| Permissions | `permissions: {}` workflow-level; `contents: read` job-level |
| Scan command | `socket scan create . --json --no-interactive --org Ezequiel` — full project scan; `socket ci` is PR-context-only and may no-op in a `workflow_dispatch` context; `--no-interactive` prevents silent failures from interactive prompts in non-TTY environments |

### When to use

| Scenario | Action |
|----------|--------|
| Dependabot PR passed CI but you want CLI-level enforcement before merging | Dispatch on `main`, set `ref` to the PR's branch name |
| Manual dependency addition on a branch before opening a PR | Dispatch on `main`, set `ref` to your branch |
| Ad-hoc audit of `main` itself | Dispatch on `main`, leave `ref` as `main` |

---

## Control 14: Build Provenance Attestation

`actions/attest-build-provenance` generates a Sigstore-backed provenance attestation for the packaged `.deb`, `.rpm`, and `.AppImage` installers in `release.yml`'s `build-linux` job, immediately after "Package with electron-builder".

### What this proves, and what it doesn't

This is the producer-side complement to Control 10 (which verifies *upstream* npm packages' provenance). It answers a different question than the manual `SHA256SUMS` signing already in place (see `docs/release-process.md`'s verification step, and the local, gitignored `CLAUDE.md` for the signing procedure itself):

| Mechanism | Question answered | Trust anchor |
|---|---|---|
| SSH-signed `SHA256SUMS` | Did the maintainer approve these exact bytes? | A long-lived private key, verified via `ssh-keygen -Y verify` against a manually distributed public key |
| `attest-build-provenance` | Did this artifact come out of this specific CI run/commit/repo? | GitHub's OIDC issuer + Sigstore's Fulcio CA issuing short-lived certs per job; verified via `gh attestation verify`, no public-key distribution needed |

They are complementary, not substitutable. A compromised release step between CI-build and manual-signing is caught by the maintainer's review before signing, but not by build provenance alone; conversely, a malicious commit merged before the tag is attested as "legitimately built" either way — attestation proves *provenance*, not *correctness*.

### Implementation

```yaml
permissions:
  contents: write
  id-token: write       # mints the OIDC token used to request the Fulcio cert
  attestations: write   # publishes the attestation to GitHub's attestation store

steps:
  ...
  - name: Attest build provenance
    if: ${{ !github.event.repository.private }}
    uses: actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373  # v4.1.1
    with:
      subject-path: |
        dist-electron/*.deb
        dist-electron/*.rpm
        dist-electron/*.AppImage
```

Scope: the three installers only. Not `SHA256SUMS` itself (already covered by the SSH signature), not the bundled sidecar binary (it ships inside the installers, not as a separate release asset).

### Why the `if:` guard

GitHub's artifact attestation storage/retrieval requires a public repository, or GitHub Enterprise Cloud for private repositories — confirmed against `actions/attest-build-provenance`'s own README, 2026-07-01. This repository is currently private on a non-Enterprise plan. Without the guard, the step would hard-fail on every tag push — and because it runs *before* "Create GitHub Release" with no `continue-on-error`, that failure would block the entire release job, not just skip the attestation.

```yaml
if: ${{ !github.event.repository.private }}
```

The `${{ }}` wrapper is required here — a bare `if: !…` is invalid YAML, since a leading `!` is a tag indicator, not negation. `github.event.repository.private` is populated on tag-push events, so this is self-activating: once the repository goes public, the step starts running with no further edit to `release.yml`.

### Verification (once public)

Not yet exercised end-to-end — deferred until the repository is public (see the project's pending release checklist). At that point:

```bash
gh attestation verify analecta_X.Y.Z_amd64.deb --owner E-zequiel
```

No end-user-facing verification documentation has been added yet — consistent with the same deliberate deferral applied to the SSH-signing procedure (no external party needs to verify a release yet; revisit if a second maintainer joins or a user asks).

---

## Repository-Level Settings

These settings are configured in GitHub → Settings → Actions → General and complement the workflow-level controls. They are not visible in workflow files but are part of the security posture.

### Actions permissions allowlist (configured 2026-05-08)

- **Mode:** "Allow E-zequiel, and select non-E-zequiel, actions"
- Allow actions created by GitHub: ✅ (covers `actions/*`)
- Allow actions by Marketplace verified creators: ❌ (too broad)
- **Explicit allowlist:** `jdx/mise-action@*`, `bitwarden/sm-action@*`
- Require actions to be pinned to a full-length commit SHA: ✅ (enforced as a repo-level backstop)

Any action not in this list — even if added to a workflow file — cannot run. Update the allowlist when adding a new external action, then add it to the inventory table in Control 1.

### Workflow permissions (configured 2026-05-11)

- **Default token permission:** "Read repository contents and packages permissions" (read-only; `GITHUB_TOKEN` has no write access unless a job explicitly declares it)
- **Allow GitHub Actions to create and approve pull requests:** ✅ (required for `deps-update.yml` to run `gh pr create` with `github.token`)

This is consistent with the `permissions: {}` / per-job grant pattern. The read-only default means a workflow author who forgets to declare permissions gets the safe default, not accidental write access.

### Fork pull request workflows (action required when repo goes public)

When the repository is made public, the **"Fork pull request workflows"** section becomes visible under Actions → General. Set it to **"Require approval for first-time contributors"**. This prevents external fork PRs from triggering CI without prior review, blocking CI credit abuse and potential secret exfiltration from untrusted contributors.

---

## Maintenance Checklist

### When Dependabot opens a dependency PR (npm or Python)

1. **CI will pass.** The `socket` job runs (checkout + install) but skips the secret-dependent steps — `bitwarden/sm-action` and `socket ci` — because the PR author is `dependabot[bot]`. The job reports success, satisfying the required status check.
2. **Socket GitHub App provides scan coverage.** The native App integration runs independently of `BWS_ACCESS_TOKEN` and posts its findings as a separate check on the PR. Review those results before merging.
3. **For CLI-level enforcement:** trigger `socket-manual.yml` via GitHub → Actions → "Socket Manual Scan" → "Run workflow". Leave the branch as `main` and enter the Dependabot PR's branch name (e.g., `dependabot/npm_and_yarn/...`) in the `ref` input. See Control 13 for rationale.
4. **Note on scan scope:** `socket ci` scans the pnpm tree. A Dependabot PR that bumps only Python packages (via `uv`) or workflow action SHAs produces no npm-tree diff — the scan would report "no dependency changes." CLI enforcement is only meaningful for PRs that modify `pnpm-lock.yaml`.
5. **Check the release date (3-day cooldown).** The automated cooldown in `deps-update.yml` does not cover Dependabot PRs. Before merging, verify when the updated version was published: for npm packages, check `https://registry.npmjs.org/<pkg>` → `.time.<version>`; for PyPI packages, check `https://pypi.org/pypi/<pkg>/<version>/json` → `.urls[].upload_time`. If the version was published fewer than 3 days ago, hold the merge. Exception: if the PR patches an active CVE, evaluate the CVSS score and architecture-mismatch triage (step 4 above) — it is a deliberate tradeoff between known CVE exposure and supply-chain risk during the early-adoption window.

### When Dependabot opens a SHA-update PR (GitHub Actions)

1. **Verify the new tag**: check the action's release notes for breaking changes or security advisories.
2. **Check the SHA independently**: compare the PR's new SHA against `https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{new-tag}`.
3. **Review the diff**: Dependabot shows only the SHA change in the workflow file — also read the action's own diff for that version range.
4. **Merge only if clean**: do not auto-merge action updates that touch steps with access to `SOCKET_SECURITY_API_TOKEN`.
5. **Update the inventory table** in Control 1 with the new SHA and verification date.

### When adding a new external action

1. Resolve the immutable SHA (see Control 1 for the `curl` procedure).
2. Add the action to the **Actions permissions allowlist** in GitHub Settings before adding it to the workflow file.
3. Add it to the inventory table in Control 1.

### When adding a new npm or Python package

1. Install with `pnpm add` or `uv add` as usual.
2. **Immediately run** `./scripts/socket-audit.sh` — do not commit or push before the scan completes.
3. Review any new alerts against the false-positive catalog in `docs/socket-security.md`.
4. If the alert is a confirmed false positive, add it to the catalog before committing.
5. If the alert indicates a real risk, do not install the package — find an alternative or escalate.
6. For new npm packages: the `verify-provenance` CI job will automatically check whether the package has a SLSA provenance attestation. If it does, the attestation is verified against Sigstore on every PR. No manual action required unless the job fails (see Control 10).

### When auditing `allowBuilds` entries (Control 11)

Run this whenever `pnpm-workspace.yaml` `allowBuilds` changes or when upgrading a package that is in the allowlist:

1. Run the lifecycle script audit (see Control 11) to confirm only the expected packages have lifecycle scripts.
2. For each package in `allowBuilds: true`, verify its lifecycle script against the npm registry tarball:
   ```bash
   curl -sL "https://registry.npmjs.org/<pkg>/-/<pkg>-<version>.tgz" \
     | tar -xzO package/install.js | sha256sum
   ```
3. Compare against the hash of the installed file: `sha256sum node_modules/.pnpm/<pkg>@<version>/node_modules/<pkg>/install.js`
4. Document the sha256 and date in this file's allowlist table.
5. If a package no longer needs a lifecycle script (e.g., it was removed from the dep tree), remove it from `allowBuilds`.

### When running `scripts/deps_update.py` locally

1. Run the script as usual: `mise exec -- python scripts/deps_update.py`.
2. Immediately run `./scripts/socket-audit.sh` — do not commit or push before the scan completes.
3. Review any new alerts. If clean, commit `pnpm-lock.yaml`, `backend/uv.lock`, and any updated `frontend/package.json` or `electron/package.json` (not `node_modules`). The script calls `pnpm add --save-exact` per package, which updates both the lockfile and the relevant `package.json`.
4. The CI `socket` job will re-scan on the resulting PR as the hard enforcement gate.

### When the `deps-update.yml` weekly PR lands

1. Review the PR body — it lists every package updated and every package held back with its eligibility date.
2. Run `./scripts/check.sh` locally against the updated lock files before merging.
3. The `socket` CI job runs on the PR and gates `check-frontend` and `test-backend` — do not merge if socket fails.
4. If a package was skipped due to cooldown and you need it urgently, re-run the workflow via `workflow_dispatch` with `cooldown` set to `0`.
