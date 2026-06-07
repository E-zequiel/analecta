---
name: audit-ci
description: Audit GitHub Actions workflow files (.github/workflows/) for supply-chain security vulnerabilities. Use this skill whenever the user asks to review CI/CD security, harden a pipeline, check workflows for issues, verify dependency pinning in CI, or wants to know if their GitHub Actions setup is protected against supply-chain attacks. Also trigger when reviewing PRs that touch workflow files, when a user mentions pnpm dlx / npx / yarn dlx in CI, when adding a new CLI tool to a workflow, or when the user asks about any of: unpinned actions, secrets exposure in CI, OIDC tokens, GitHub Actions permissions, fork PR security, or supply-chain attacks targeting CI runners.
---

# CI Supply-Chain Security Audit

Audit all GitHub Actions workflow files in `.github/workflows/` and report findings with severity, evidence, and remediation guidance.

## The attack surface

CI pipelines run arbitrary code with access to repository secrets. Three layers of risk:

1. **Third-party actions** — a mutable tag (`@v4`, `@main`) can be silently redirected to malicious code after it was last reviewed
2. **CLI tools fetched at runtime** — `pnpm dlx`, `npx`, `yarn dlx` download and execute whatever version the registry currently serves; a compromised package in the same step as a secret can read the process environment and exfiltrate it
3. **Excessive permissions** — jobs with broader `GITHUB_TOKEN` permissions than needed give a compromised step more blast radius

The Mini Shai-Hulud campaign (2026-05-19) demonstrated vector 2 at scale: 323 npm packages in a single automated burst. A supply-chain scanner like Socket CLI is itself a supply-chain attack surface if fetched unverified.

---

## Audit process

### Step 1: Discover workflow files

```bash
find .github/workflows -name "*.yml" -o -name "*.yaml" 2>/dev/null | sort
```

List the files, then read each one.

### Step 2: Check each file for the following issues

#### 2a. Unpinned CLI tool execution

Grep for patterns that download and execute a package from the npm registry at run time:

```bash
grep -n "pnpm dlx\|npx \|npm exec\|npm x \|yarn dlx" .github/workflows/*.yml
```

For each match, check whether the same job or step injects secrets into the environment — either via `env:` containing `secrets.*` or `GITHUB_TOKEN`, or via a `with:` block that passes a secret. If secrets are present in scope → **HIGH**. If not → **MEDIUM**.

**Remediation:** Install the tool as a lockfile-managed devDependency at a version with a verified tag in its GitHub repository. Replace `pnpm dlx <pkg>` with `pnpm exec <pkg>` (after `pnpm install --frozen-lockfile`). Verify the lockfile hash against the npm tarball:

```bash
curl -sL https://registry.npmjs.org/<pkg>/-/<pkg>-<version>.tgz \
  | openssl dgst -sha512 -binary | base64
# Output must match the integrity field in pnpm-lock.yaml
```

Only pin to a version that has a verified tag in the tool's GitHub repository. npm versions without a corresponding GitHub tag are unverifiable — use the last verified release instead.

**Scope of this check:** the tarball comparison detects the case where a maintainer's npm account is compromised and they publish a malicious version to npm *without* creating a corresponding git tag — the npm tarball diverges from the source. It does **not** protect against a registry-level compromise where both the tarball and its hash are served consistently; verifying a tarball against the same registry that served it is circular. For that threat, npm provenance attestations (check 2h) are the correct control.

#### 2b. Unpinned action references

```bash
grep -n "uses:" .github/workflows/*.yml | grep -v "@[0-9a-f]\{40\}"
```

Flag any `uses:` that references a mutable pointer:
- `uses: owner/repo@v1` — mutable tag
- `uses: owner/repo@main` — mutable branch
- `uses: owner/repo@latest` — explicit floating

**The correct form:** `uses: owner/repo@<40-char-sha>  # vX.Y.Z`

Git tags are mutable pointers. Pinning to a commit SHA means the code that runs is exactly the code that existed when the SHA was recorded — even if the tag is later moved.

**How to resolve a SHA:**
```bash
# Resolve tag to SHA (dereference annotated tags if needed)
curl -s "https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{tag}" \
  | jq '{sha: .object.sha, type: .object.type}'
# If type == "tag" (annotated), dereference to the underlying commit:
curl -s "https://api.github.com/repos/{owner}/{repo}/git/tags/{sha}" \
  | jq '.object.sha'
```

Severity: **MEDIUM** (becomes HIGH if the action has access to `secrets.*` and its maintainer account could be compromised).

#### 2c. Missing workflow-level `permissions: {}`

Check whether each workflow file has `permissions: {}` at the top level (before the `jobs:` key). Without it, GitHub defaults the `GITHUB_TOKEN` to `contents: write` in every job, giving any compromised step write access to repository code.

The correct pattern:
```yaml
permissions: {}   # deny-all at workflow level

jobs:
  my-job:
    permissions:
      contents: read   # only what the job actually needs
```

Severity: **MEDIUM**

#### 2d. `id-token: write` without clear purpose

Flag any job that declares `id-token: write`. This grants the job an OIDC token that can be used to authenticate as the repository to cloud providers (AWS, GCP, Azure). If the workflow doesn't use Trusted Publishing or cloud OIDC authentication, it's unnecessary exposure.

Note it, explain what it enables, and ask the user whether it is intentional.

Severity: **LOW–MEDIUM** depending on whether cloud credentials are in scope.

#### 2e. Missing repository guard on sensitive jobs

Flag jobs that create GitHub Releases, push code or tags, comment on PRs with `GITHUB_TOKEN`, or access secrets via `sm-action` or similar — without an `if: github.repository == 'owner/repo'` guard.

Without this guard, a fork of the repository can trigger these jobs. Depending on the repo's fork PR settings, this can produce confusing failures at best and a confused-deputy attack at worst.

Severity: **MEDIUM**

#### 2f. `cancel-in-progress: true` on release workflows

Flag release workflows (those triggered by version tags) where `cancel-in-progress: true` is set. Cancelling a release job mid-run leaves the GitHub Release in a broken state — some assets uploaded, some missing — requiring manual cleanup.

Release jobs should use `cancel-in-progress: false`.

Severity: **LOW**

#### 2g. Missing `packageManager` field (pnpm projects)

For repositories using pnpm, check whether `package.json` at the workspace root declares a `packageManager` field with a corepack SHA:

```bash
grep "packageManager" package.json
```

The correct form is `"packageManager": "pnpm@X.Y.Z+sha512.<hash>"`. Without it, any pnpm version can run the install — including a version that was unintentionally downgraded, or a compromised binary on a developer's machine. Corepack uses the hash to verify the pnpm binary before executing it.

Generate or update with:
```bash
corepack use pnpm@<version>
```

Severity: **LOW**

#### 2h. Missing lifecycle script restriction (pnpm projects)

Check whether `pnpm-workspace.yaml` restricts which packages are allowed to run install-time lifecycle scripts (`preinstall`, `install`, `postinstall`):

```bash
grep -A10 "allowBuilds\|onlyBuiltDependencies" pnpm-workspace.yaml
```

Without this restriction, any installed package — including transitive dependencies — can execute arbitrary code on every `pnpm install`. A compromised package with a malicious `postinstall` script would run with full user privileges.

The correct form is an explicit allowlist of only the packages that genuinely need to run scripts (typically only those that download platform-specific binaries):

```yaml
# pnpm-workspace.yaml
allowBuilds:
  electron: true       # downloads Electron binary
  esbuild: true        # downloads platform binary (if in tree)
  some-evil-pkg: false # explicitly blocked
```

To audit what packages actually have lifecycle scripts in the installed tree:
```python
import json, os
# check package.json files under node_modules/.pnpm for install/postinstall scripts
```

Severity: **MEDIUM** (install-time RCE vector for any package that runs an unrestricted postinstall)

### Step 3: Cross-check lockfile coverage

For any `pnpm exec <tool>` or `npx --no-install <tool>` calls found (the already-correct patterns), verify the tool is actually present in the lockfile — a missing entry causes a silent runtime failure:

```bash
grep "^  <tool>@" pnpm-lock.yaml      # pnpm
grep '"<tool>"' package-lock.json     # npm/yarn
```

### Step 4: Report

Produce the findings table, clean file list, and summary.

---

## Output format

```
## CI Security Audit — <project>

### Findings

| Severity | File | Job | Step | Issue | Remediation |
|----------|------|-----|------|-------|-------------|
| HIGH     | ci.yml | socket | Socket scan | `pnpm dlx socket ci` runs with `SOCKET_SECURITY_API_TOKEN` in env | Pin `socket` as devDependency; replace with `pnpm exec socket ci` |
| MEDIUM   | release.yml | build | checkout | `uses: actions/checkout@v4` (mutable tag) | Pin to full SHA: `actions/checkout@<sha>  # v4.x.y` |

### Clean files
- (workflow files with no findings)

### Summary
(One paragraph: overall posture, most critical issue, top recommendation.)
```

**Severity scale:**

| Level | Meaning |
|-------|---------|
| HIGH | Unpinned CLI tool in a step that has secrets in environment scope |
| MEDIUM | Unpinned action ref, missing permissions guard, missing repo guard on a job that writes or accesses secrets, missing lifecycle script restriction |
| LOW | `cancel-in-progress: true` on release, `id-token: write` without apparent need, missing `packageManager` SHA field |

---

## After the audit

Once findings are reported, offer to remediate. Always confirm with the user before editing any workflow file. For `pnpm dlx` → `pnpm exec` migrations, follow the full pinning + hash-verification procedure in check 2a: install the devDependency, verify the SHA-512, then update the workflow.

#### 2j. Scan ordering: scan before lifecycle scripts

Check that the Socket scan job installs with `--ignore-scripts`, that downstream jobs are gated on socket approval, and that `deps-update.yml`'s install step also skips scripts.

**What to grep for:**

```bash
# socket job must use --ignore-scripts
grep -A5 "name: Install" .github/workflows/ci.yml | grep "ignore-scripts"

# check-frontend and test-backend must declare needs: [socket]
grep -n "needs:" .github/workflows/ci.yml

# downstream job if-conditions must use !cancelled(), not always()
grep -n "cancelled\|always()" .github/workflows/ci.yml

# deps-update.yml install step must also use --ignore-scripts
grep -n "ignore-scripts" .github/workflows/deps-update.yml
```

**What correct looks like:**

```yaml
# socket job: no lifecycle scripts run before the scan
- name: Install dependencies
  run: mise exec -- pnpm install --frozen-lockfile --ignore-scripts

# downstream jobs: only run after socket approves
check-frontend:
  needs: [socket]
  if: >-
    !cancelled() &&
    github.repository == 'E-zequiel/analecta' &&
    (needs.socket.result == 'success' || needs.socket.result == 'skipped')
```

The `|| 'skipped'` clause is required: on `push` to `main`, the `socket` job does not run (it is scoped to PRs), so its result is `'skipped'` — without this clause, `check-frontend` and `test-backend` would never run on main pushes.

**Why `!cancelled()` and not `always()`:** `always()` fires the job even on a cancelled run. `!cancelled()` lets the job run normally (including when socket is skipped) but suppresses it on cancellation.

**`--ignore-scripts` in `deps-update.yml`:** The update workflow holds the highest-privilege token (`contents: write` + `pull-requests: write`). Its initial `pnpm install --frozen-lockfile --ignore-scripts` step blocks `electron`'s binary download (the only script permitted by `allowBuilds`) because the job only needs `pnpm outdated`/`pnpm update` tooling — the Electron binary is not required there.

**Local limitation:** `pnpm update` (called by `scripts/deps_update.py` for Node updates) has no `--lockfile-only` flag — it updates both `pnpm-lock.yaml` and `node_modules` in one step. `electron`'s postinstall may therefore run before `socket-audit.sh` can scan the updated lockfile. The compensating control is to run `./scripts/socket-audit.sh` immediately after `deps_update.py`, before committing or pushing. CI is the hard enforcement gate; local is advisory.

**Branch protection (manual):** For `needs: [socket]` to block merges, add `socket` as a required status check in GitHub → Settings → Branches → `main`. A skipped required check is treated as "not passed."

**Fork PR caveat:** The `socket` job is scoped to non-fork PRs (`head.repo.full_name == github.repository`). If the repo goes public and `socket` is a required status check, fork PRs will have a permanently unresolvable check. Tracked in `project_public_repo_checklist.md`.

Severity: **MEDIUM** (gap: a compromised `allowBuilds`-listed package's postinstall runs before the scan; `--ignore-scripts` closes this for CI, advisory workflow mitigates it locally)

#### 2k. Dependabot PR secret-access pattern

When a `pull_request`-triggered job passes a fork guard (`head.repo.full_name == github.repository`) but contains steps that access secrets — via `bitwarden/sm-action`, `secrets.*`, or a similar injector — check whether those steps have a step-level Dependabot guard:

```bash
grep -n "head.repo.full_name\|sm-action\|secrets\." .github/workflows/*.yml
```

**Why the fork guard is insufficient:** GitHub strips secret access from `dependabot[bot]`-authored PRs even when they originate from the same repository. `head.repo.full_name == github.repository` evaluates to `true` for Dependabot PRs — the fork guard passes, the secret step fires, and the job fails with "access token is required."

**What correct looks like:**

```yaml
- uses: bitwarden/sm-action@<SHA>
  if: github.event.pull_request.user.login != 'dependabot[bot]'
  with:
    access_token: ${{ secrets.BWS_ACCESS_TOKEN }}

- name: Secret-dependent step
  if: github.event.pull_request.user.login != 'dependabot[bot]'
  ...
```

**Critical: use `user.login`, not `github.actor`.** When a maintainer re-runs a failed workflow on a Dependabot PR, `github.actor` changes to the maintainer's username but the secret restriction on that PR does not lift. The secret steps would fire and fail again. `github.event.pull_request.user.login` is the PR author — immutable across re-runs.

**Critical: step-level guard, not job-level.** A job-level guard causes the entire job to be skipped. If the job is a required status check, GitHub treats "skipped" as "not passed" — the PR stays blocked. The guard must be at the step level so the job runs to completion (reporting success) while only the secret-dependent steps are bypassed.

**`[bot]` spoofability:** The `[bot]` suffix is reserved for GitHub App accounts and cannot be registered as a regular user. This condition cannot be spoofed.

**Analecta instance:** `ci.yml` → `socket` job. The `bitwarden/sm-action` and `socket ci` steps carry `if: github.event.pull_request.user.login != 'dependabot[bot]'`. The Socket GitHub App (native integration) provides scan coverage for Dependabot PRs independently of `BWS_ACCESS_TOKEN`. For CLI-level enforcement before merging a Dependabot PR, use `socket-manual.yml` (see check 2l).

Severity: **MEDIUM** (Dependabot PRs cause required-check failures; fails CI without compromising secrets)

#### 2l. `workflow_dispatch` ref-trust

When a workflow triggered by `workflow_dispatch` accesses secrets and uses `actions/checkout` without `ref: ${{ inputs.<name> }}`:

```bash
# Find workflow_dispatch workflows that access secrets
grep -l "workflow_dispatch" .github/workflows/*.yml | xargs grep -l "secrets\."

# Check whether each uses a ref input for checkout
grep -A5 "actions/checkout" .github/workflows/<workflow>.yml | grep "ref:"
```

**The risk:** GitHub reads the workflow file AND all action SHAs from the dispatched ref. A branch that modifies the workflow file or updates an action pin runs its own version of the workflow — with full secret access.

**The safe pattern — dispatch on `main`, checkout by input:**

```yaml
on:
  workflow_dispatch:
    inputs:
      ref:
        description: 'Branch or SHA to target'
        required: true
        default: 'main'

steps:
  - uses: actions/checkout@<SHA>   # SHA from main's workflow definition — always trusted
    with:
      ref: ${{ inputs.ref }}       # the content being examined comes from the target ref
```

With this pattern the user dispatches the workflow *on `main`*, not on the target branch. The workflow definition and action SHAs come from `main`; a branch that rewrites the workflow cannot affect the code that runs.

**Important scope of this guarantee:** only the workflow definition and action SHAs are anchored to `main`. With `ref: ${{ inputs.ref }}`, the checkout populates the working tree from the target ref — so `.mise.toml`, lockfiles, and any config read from the tree still come from the branch being scanned. This is intentional (you want to examine that branch's content) but it means toolchain config is not independently anchored. The real control for this residual risk is operational: **only dispatch against refs whose toolchain config and lockfile you trust** (your own branches, Dependabot's registry-sourced bumps). Do not dispatch against refs from untrusted contributors.

**Also check the scan command:** `socket ci` is designed for PR context — it diffs against a base branch. In `workflow_dispatch` (no PR), it may no-op and report green without scanning anything. For manual/release contexts, use `socket scan create . --json` instead.

**When this check does not apply:** `workflow_dispatch` workflows that do not access secrets and have no significant blast radius. The risk is specifically relevant when the workflow accesses high-value secrets such as `BWS_ACCESS_TOKEN`.

**Analecta instance:** `socket-manual.yml` correctly uses dispatch-on-main + `ref` input, and `socket scan create . --json --no-interactive --org Ezequiel` (not `socket ci`). `--no-interactive` is required: without it, the CLI falls into an org-discovery prompt in non-TTY environments and exits silently with code 0 without running any scan. `deps-update.yml` also uses `workflow_dispatch` but accesses no high-value secrets directly — it uses only `github.token` with a scoped `pull-requests: write` grant. No finding on either.

Severity: **MEDIUM** (a branch that modifies the workflow could run with secret access when dispatched directly; mitigated if dispatch is manually supervised and restricted to write-access users)

---

## Analecta-specific checks (2i)

Run these in addition to 2a–2h when auditing this repository.

#### 2i. Provenance verification infrastructure

Check that the npm provenance attestation verification setup (Control 10) is intact:

```bash
# requirements-provenance.lock must exist and be tracked in git
git ls-files scripts/requirements-provenance.lock

# .venv-provenance/ must be gitignored
grep "\.venv-provenance" .gitignore

# verify-provenance job must exist in CI
grep "verify-provenance" .github/workflows/ci.yml

# sigstore must be pinned in the lock file
grep "^sigstore==" scripts/requirements-provenance.lock
```

| Finding | Severity | Remediation |
|---|---|---|
| `requirements-provenance.lock` not committed | MEDIUM | Regenerate: `echo "sigstore==4.2.0" \| uv pip compile --generate-hashes - -o scripts/requirements-provenance.lock` |
| `verify-provenance` job missing from `ci.yml` | MEDIUM | Re-add the job — see `docs/github-actions-security.md` Control 10 |
| `.venv-provenance/` not in `.gitignore` | LOW | Add `.venv-provenance/` to `.gitignore` |
| `sigstore` not pinned (version range instead of `==`) | LOW | Pin to exact version in lock file |

**Context:** `scripts/verify-provenance.py` verifies Sigstore provenance attestations for the 52/386 installed npm packages (13%) that publish them. It cross-checks the attested subject SHA-512 against `pnpm-lock.yaml` (independent of registry) and verifies the Sigstore bundle against Rekor + Fulcio. See `docs/github-actions-security.md` Control 10 for full threat model and residual gap documentation.
