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
