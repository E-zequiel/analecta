# GitHub Actions Security Policy

This document explains the security controls applied to all CI/CD workflows in this repository and the reasoning behind each decision.

---

## Threat Model

GitHub Actions workflows run arbitrary code with access to repository secrets. The primary threat vectors are:

1. **Supply chain compromise of third-party actions** — a malicious actor pushes a new release to a dependency action (e.g., `tauri-apps/tauri-action`), which then exfiltrates secrets or tampers with build artifacts during the next workflow run.
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
| `actions/checkout` | `v6.0.2` | `de0fac2e4500dabe0009e67214ff5f5447ce83dd` | 2026-05-08 |
| `jdx/mise-action` | `v4.0.1` | `1648a7812b9aeae629881980618f079932869151` | 2026-05-08 |
| `tauri-apps/tauri-action` | `action-v0.6.2` | `84b9d35b5fc46c1e45415bdb6144030364f7ebc5` | 2026-05-08 |
| `bitwarden/sm-action` | `v3.0.0` | `27c0c9dcab679d7250dbab91227c85b49ffa5e0f` | 2026-05-08 |

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

`contents: write` is required because `tauri-apps/tauri-action` calls the GitHub Releases API to create the release and upload `.deb`, `.AppImage`, and `.rpm` assets. No other permission (`packages`, `id-token`, `pull-requests`, etc.) is granted.

### Known platform limitation

`contents: write` is broader than strictly needed — it also grants write access to repository code, branches, and refs, not just releases. GitHub's `GITHUB_TOKEN` has no dedicated `releases` scope; release operations are bundled under `contents`. This is a GitHub platform constraint with no workaround short of provisioning a GitHub App token (via `actions/create-github-app-token`), which would be over-engineering for this project at its current scale.

### `github.token` vs `secrets.GITHUB_TOKEN`

Both expressions resolve to the same auto-generated job token, but `${{ github.token }}` is the canonical form. It signals clearly that this is the runner's automatic token — not a user-managed secret — and is preferred by security scanners (OSSF Scorecard). All workflows in this repo use `github.token`.

---

## Control 3: Secrets Scoping

### Secrets used in release.yml

| Secret | Source | Used by | Purpose |
|--------|--------|---------|---------|
| `github.token` | Auto (runner) | `tauri-action` | Create GitHub Release, upload assets |
| `BWS_ACCESS_TOKEN` | GitHub secret | `sm-action` | Read-only BSM machine account token |
| `TAURI_SIGNING_PRIVATE_KEY` | BSM via `sm-action` | `tauri-action` | Sign release bundles (Tauri updater) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | BSM via `sm-action` | `tauri-action` | Decrypt the signing key if passphrase-protected |

`TAURI_SIGNING_PRIVATE_KEY` is **not** stored as a GitHub secret. It lives in Bitwarden Secrets Manager and is injected at runtime by `bitwarden/sm-action`. See `docs/bitwarden-secrets-manager.md` for the full secret management architecture.

`github.token` auto-expires when the job ends. `BWS_ACCESS_TOKEN` is low-blast-radius (read-only BSM access). `TAURI_SIGNING_PRIVATE_KEY` has no expiry — see rotation procedure below.

### Why `TAURI_SIGNING_PRIVATE_KEY` is the highest-risk secret

- It signs every release bundle. A leaked key allows an attacker to publish malicious updates that the auto-updater will accept and install silently on users' machines.
- Rotation requires re-generating the keypair and publishing a new `pubkey` in `tauri.conf.json` — existing installations cannot auto-update until users manually reinstall.
- **If this key is ever suspected compromised: rotate immediately and publish a forced manual update.**
- Storing it in BSM (not as a GitHub secret) reduces the attack surface: GitHub credential leaks do not expose it.

### Rotation procedure for `TAURI_SIGNING_PRIVATE_KEY`

1. Generate a new keypair without writing to disk: `mise exec -- cargo tauri signer generate` (no `-w` flag — keys printed to stdout only).
2. Update the secret value in the **BSM Web App** (Web App only — local machine account is read-only).
3. Update `plugins.updater.pubkey` in `src-tauri/tauri.conf.json` with the new public key.
4. Tag and release a new version — signed with the new key.
5. Users must manually install this version; the old auto-updater cannot verify the new signature.

---

## Control 4: Repository Guard

The release job includes a repository check:

```yaml
if: github.repository == 'E-zequiel/analecta'
```

This prevents forked repositories from triggering the release job. In a fork, `TAURI_SIGNING_PRIVATE_KEY` does not exist and the job would fail with a confusing error. The guard makes it an explicit, clean skip instead, and eliminates the surface area for confused-deputy attacks where a fork PR could manipulate the release workflow.

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
| Rust | `Cargo.lock` is checked into the repository and used by `cargo build` automatically |

The sidecar build (`scripts/build_sidecar.py`) runs inside the locked Python environment established by `uv sync --frozen`. PyInstaller and all its dependencies are resolved deterministically.

---

## Maintenance Checklist

When Dependabot opens a SHA-update PR:

1. **Verify the new tag**: check the action's release notes for breaking changes or security advisories.
2. **Check the SHA independently**: compare the PR's new SHA against `https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{new-tag}`.
3. **Review the diff**: Dependabot shows only the SHA change in the workflow file — also read the action's own diff for that version range.
4. **Merge only if clean**: do not auto-merge action updates that touch steps with access to `TAURI_SIGNING_PRIVATE_KEY`.
