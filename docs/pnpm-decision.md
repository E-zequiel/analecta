# Decision: pnpm as the Node.js Package Manager

**Status:** Accepted  
**Date:** 2026-05-05

---

## Problem with npm

npm installs dependencies into a **flat** `node_modules/` tree: the resolver places all packages — direct and transitive — at the same level. This allows any package to `require()` modules it did not declare in its own `package.json` ("phantom dependencies").

This is a real supply-chain attack vector:

- A malicious nested package can import modules it should never see.
- The attack surface grows with every transitive dependency.
- Several recent npm CVEs exploit exactly this mechanism.

## Solution: pnpm

pnpm resolves this with a **non-flat** `node_modules/` layout:

- Each package gets its own `node_modules/` containing symlinks only to its declared dependencies.
- Transitive dependencies live in a global content-addressable store (`~/.pnpm-store/`) and are linked without duplication.
- A package that attempts to access an undeclared dependency gets an error instead of silent access.

| Aspect | npm | pnpm |
|--------|-----|------|
| Phantom dependency security | ✗ flat, unrestricted | ✓ strict, per-package isolation |
| Install speed | baseline | faster (store + hard links) |
| Disk usage | duplicates deps across projects | global shared store |
| Lockfile | `package-lock.json` | `pnpm-lock.yaml` (more readable) |
| Workspaces | supported | supported, `--filter` syntax |

## Compatibility

- **Electron:** works natively with pnpm workspaces; no configuration needed.
- **SvelteKit:** first-class support; `pnpm create svelte@latest` is the recommended scaffold.
- **mise:** pnpm is managed as a native tool (`pnpm = "latest"` in `.mise.toml`), with no dependency on `npm install -g` or corepack.
- **GitHub Actions:** `pnpm/action-setup` is an official, widely-adopted action.

## Decision

**Use pnpm exclusively. Never npm.**

Managed via mise:

```toml
# .mise.toml
[tools]
python = "3.13"
node   = "lts"
rust   = "stable"
pnpm   = "latest"
```

Workspace declaration at root:

```yaml
# pnpm-workspace.yaml
packages:
  - frontend
  - electron
```

All Node.js commands are invoked as `mise exec -- pnpm <cmd>`.

## Alternatives Rejected

- **npm** — rejected for the phantom dependency problem described above.
- **yarn** — equivalent to npm on security grounds; no meaningful advantage here.
- **bun** — faster, but less mature for production and less tested in the Electron ecosystem.
