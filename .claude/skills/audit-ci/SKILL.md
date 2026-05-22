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
| MEDIUM | Unpinned action ref, missing permissions guard, missing repo guard on a job that writes or accesses secrets |
| LOW | `cancel-in-progress: true` on release, `id-token: write` without apparent need |

---

## After the audit

Once findings are reported, offer to remediate. Always confirm with the user before editing any workflow file. For `pnpm dlx` → `pnpm exec` migrations, follow the full pinning + hash-verification procedure in check 2a: install the devDependency, verify the SHA-512, then update the workflow.
