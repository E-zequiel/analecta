# Linux Package Naming: deb/rpm Artifact and Package Name

**Status:** Implemented and confirmed against real CI-built releases
**Date:** 2026-07-01

---

## Problem

Packaged `.deb`/`.rpm` artifacts, and the installed Debian/RPM package name itself, were
`analecta-electron`, not `analecta` — despite `electron-builder.yml` already setting
`productName: Analecta` and `linux.executableName: analecta`.

---

## Root Cause

electron-builder's Linux packaging (via the bundled `fpm` tool, used for both the `deb` and
`rpm` targets) does not use `productName` or `executableName` for two things:

1. The artifact filename's `${name}` macro.
2. The Debian/RPM control file's `Package:` field.

Both instead resolve from `appInfo.name`, which is the raw `package.json` `"name"` field of
the electron-builder project directory — in this repo, `electron/package.json`'s
`"name": "analecta-electron"` (the pnpm workspace identifier for that package).
`productName` and `executableName` only affect the display name (menu, window title), the
installed binary's filename, and — via `productFilename` — the `AppImage` artifact name, which
is why `AppImage` builds were already correctly named.

Confirmed by reading electron-builder's own source (`app-builder-lib`) — there is no config
hook that redirects `${name}` away from `appInfo.name` for these two outputs.

---

## Why Not Rename `package.json`

The obvious fix — renaming `electron/package.json`'s `"name"` to `"analecta"` — was rejected.
The pnpm workspace root (`package.json` at the repo root) is already named `"analecta"`.
Confirmed empirically with `pnpm -r list --depth -1`, which lists the workspace root as a
filterable project even though it is not matched by any glob in `pnpm-workspace.yaml`'s
`packages:` field. Two workspace packages sharing a name would make `pnpm --filter analecta`
ambiguous — and the root's own `dist` script already invokes the electron package's `dist`
script, so a rename risks silent recursion or an outright pnpm resolution error.

---

## Solution

`electron-builder.yml` sets both the internal package name and the artifact filename
explicitly per target, without touching any `package.json`:

```yaml
deb:
  packageName: analecta
  artifactName: 'analecta_${version}_${arch}.${ext}'

rpm:
  packageName: analecta
  artifactName: 'analecta-${version}.${arch}.${ext}'
```

`packageName` is a `LinuxTargetSpecificOptions` field consumed by `FpmTarget` for the control
file's `Package:` field. `artifactName` overrides the default `${name}_${version}_${arch}.${ext}`
(deb) / `${name}-${version}.${arch}.${ext}` (rpm) templates — these cannot reference
`packageName` instead of `${name}`, since the macro expander always resolves `${name}` from
`appInfo.name`.

---

## Scope

Fixed in `electron-builder.yml` only; no `package.json` or `pnpm --filter` references changed.
`AppImage` was never affected.

Confirmed against CI-built releases (`v0.4.0`, `v0.5.0`): downloaded artifact filenames
match `artifactName` (`analecta_X.Y.Z_amd64.deb`), and `dpkg -s analecta` on an installed
system reports `Package: analecta`, matching `packageName`.
