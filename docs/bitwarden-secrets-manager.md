# Bitwarden Secrets Manager

This document describes the secret management architecture for Analecta: which secrets exist, where they live, how they are created, and how they are consumed at runtime and in CI.

---

## Two-Layer Model

| Layer | Tool | Purpose | Who controls it |
|-------|------|---------|-----------------|
| **Developer / CI** | Bitwarden Secrets Manager (BSM) | Single source of truth for all project secrets | Developer (Web App) |
| **Runtime (user)** | System keyring (`keyring` library) | Stores user-supplied secrets on their machine at runtime | End user via app UI |

These layers are independent. BSM never reaches into a user's machine. The keyring is never read by CI.

---

## Constraint: Read-Only Local Machine Account

The machine account configured on the local development machine (`Pop!_OS`) has **read-only** access to BSM. This is intentional — it enforces least privilege on the daily-use environment.

Consequences:
- `bws secret create` **will fail** locally (HTTP 403). Do not attempt it.
- Secret creation must go through the **BSM Web App** (the only path with write access).
- Secret reading locally works via `bws secret get <UUID>` or `bws run -- <command>`.

---

## Creating a Secret (Web App Procedure)

When a new secret is needed:

1. Open the Bitwarden Secrets Manager Web App.
2. Navigate to the active **Project** (`analecta`).
3. Create a new secret:
   - **Key**: follow the naming convention — `UPPERCASE_SNAKE_CASE` (e.g., `TAURI_SIGNING_PRIVATE_KEY`)
   - **Value**: the secret value
4. Note the generated **UUID** — it is needed for CI injection.
5. Do **not** commit the UUID to the repository. Store it in the CI configuration (see below).

---

## CI Integration — `bitwarden/sm-action`

For secrets consumed in GitHub Actions, BSM injects them at runtime via `bitwarden/sm-action`. This avoids storing high-value secrets as GitHub secrets.

### How it works

1. A low-privilege GitHub secret (`BWS_ACCESS_TOKEN`) holds the BSM machine account token.
2. At the start of a job that needs secrets, `sm-action` authenticates to BSM with that token and injects the mapped secrets as masked environment variables.
3. Subsequent steps consume the variables normally.

The machine account used for `BWS_ACCESS_TOKEN` must have **read** access to the relevant BSM project. The existing read-only machine account satisfies this requirement.

### Usage

```yaml
- name: Load secrets from BSM
  uses: bitwarden/sm-action@27c0c9dcab679d7250dbab91227c85b49ffa5e0f  # v3.0.0
  with:
    access_token: ${{ secrets.BWS_ACCESS_TOKEN }}
    secrets: |
      <SECRET_UUID> > ENV_VAR_NAME
```

**SHA pinning is mandatory** for this step — it receives the BSM token and outputs high-value secrets into the environment. An unpinned reference is a supply chain risk. Current pinned SHA: `27c0c9dcab679d7250dbab91227c85b49ffa5e0f` (`v3.0.0`, verified 2026-05-08).

### Blast radius comparison

| Secret | Storage | Blast radius if compromised |
|--------|---------|----------------------------|
| `BWS_ACCESS_TOKEN` | GitHub secret | Read-only BSM access — rotate in BSM, update GitHub secret. Low impact. |
| `TAURI_SIGNING_PRIVATE_KEY` | BSM | Attacker can sign malicious updates accepted silently by all installed clients. **Immediate rotation required.** See rotation procedure in `docs/github-actions-security.md`. |

---

## Known Secrets Inventory

### CI secrets (BSM → GitHub Actions via sm-action)

| BSM Key | GitHub env var | Used by | Purpose |
|---------|---------------|---------|---------|
| `TAURI_SIGNING_PRIVATE_KEY` | `TAURI_SIGNING_PRIVATE_KEY` | `tauri-action` | Signs release bundles for the auto-updater |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | `tauri-action` | Decrypts the signing key (empty if no passphrase) |

### GitHub-only secrets (not in BSM)

| GitHub secret | Purpose |
|---------------|---------|
| `BWS_ACCESS_TOKEN` | Machine account token — grants CI read access to BSM |

### Runtime secrets (BSM → developer keyring → app)

These are secrets the app reads at runtime from the system keyring. The developer stores them in BSM for reference and injects them into the local keyring during development.

| BSM Key | Keyring call | Purpose |
|---------|-------------|---------|
| `VIRUSTOTAL_API_KEY` | `keyring.get_password("analecta", "VIRUSTOTAL_API_KEY")` | VirusTotal URL scanning |

---

## Claude Code Policy

- Never generate, log, print, or hardcode secret values.
- Never run `bws secret create` — it will fail (read-only account) and would violate least privilege.
- When code requires a new secret, emit this block and halt:

```
⚠️  SECRET REQUIRED
    Name   : <BSM_KEY>
    Purpose: <what it's used for>
    Action : 1. Open Bitwarden Secrets Manager Web App.
             2. Create a new secret with Key: "<BSM_KEY>" in the analecta Project.
             3. Note the UUID — add it to the relevant workflow's sm-action secrets block.
             4. (Local dev) inject into keyring if needed:
                import keyring; keyring.set_password("analecta", "<BSM_KEY>", "<VALUE>")
    Runtime: <how the app reads it>
```
