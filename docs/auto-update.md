# Auto-Update Mechanism — Analecta

> Explains how in-app updates work end to end: the update-check flow, the draft-release
> gotcha, integrity verification, and the per-target install behavior on Linux.

---

## Overview

Analecta uses `electron-updater`, configured against the GitHub Releases provider
(`electron-builder.yml` → `publish: { provider: github, releaseType: draft }`). All three
Linux targets (`.deb`, `.rpm`, `.AppImage`) are auto-update-capable, but the install
mechanism — and the resulting UX — differs per target (see below).

## Flow

1. On startup, the renderer calls `checkUpdate()` (`frontend/src/routes/+layout.svelte`),
   which invokes IPC channel `check-update` → `autoUpdater.checkForUpdates()`
   (`electron/main/updater.ts`). No-op if `!app.isPackaged` — dev builds never check.
2. If a newer published GitHub Release exists, `autoUpdater` emits `update-available`;
   `updater.ts` forwards it to the renderer over the same-named IPC event.
3. The renderer shows `UpdateBanner.svelte` with the new version and an "Install &
   restart" button.
4. Clicking it calls `downloadAndInstallUpdate()` → IPC `download-and-install-update` →
   `downloadUpdate()` then `quitAndInstall()` (`electron/main/updater.ts`), followed by
   `relaunch()`.
5. `autoDownload` is `false` (set in `initUpdater`) — nothing downloads until the user
   explicitly clicks the banner. There is no silent background install.

## Draft releases are invisible

`electron-builder.yml` sets `releaseType: draft`. `electron-updater`'s GitHub provider
only resolves the `latest` **published** release — draft and prerelease releases never
appear in the feed. A release must be manually published (see `docs/release-process.md`
→ Publishing) before any existing install picks it up as an update. This applies to
every release, not only the first.

## Integrity verification

No code-signing key is used or stored anywhere in this pipeline. Instead:

- `electron-builder` auto-generates `latest-linux.yml` alongside the installers on every
  build, containing a SHA-512 checksum of each artifact.
- `electron-updater` downloads and checks the artifact against that checksum before
  installing.

This is separate from, and unrelated to, the maintainer-facing `SHA256SUMS` + SSH
signature + Sigstore build provenance attestation described in `docs/github-actions-security.md` (Control 14).
Those exist for a human verifying a downloaded installer by hand; `latest-linux.yml`'s
SHA-512 is what `electron-updater` itself checks automatically, unconditionally, on
every in-app update.

## Per-target install behavior

`electron-updater` selects the updater class matching how the running binary was
packaged (read from the bundled `app-update.yml`):

| Target       | Install mechanism                                                              | Privilege prompt |
|--------------|----------------------------------------------------------------------------------|-------------------|
| `.AppImage`  | In-place file replace (`mv` new file over old, `chmod +x`), then re-exec        | None — runs from a user-writable path |
| `.deb`       | `dpkg -i`, falling back to `apt-get install -f -y` on dependency failure         | Yes |
| `.rpm`       | First of `zypper` / `dnf` / `yum` / `rpm`, with `--nogpgcheck` / `--allow-unsigned-rpm` (packages aren't GPG-signed — the SHA-512 check above is the only integrity check) | Yes |

For `.deb`/`.rpm`, "Install & restart" surfaces an OS-level authentication dialog
(PolicyKit via `pkexec` on most modern distros, including Pop!_OS/COSMIC, falling back
to `gksudo`/`kdesudo`/`beesu`/`sudo` — first one found) mid-flow, since installing a
package requires root. `.AppImage` installs silently, with no prompt.

## Scope

Implemented and wired end to end in code (`updater.ts`, `ipc.ts`, `+layout.svelte`,
`UpdateBanner.svelte`), verified 2026-07-02 by reading the full call chain plus the
relevant `electron-updater` source. **Not yet exercised against a real packaged
build** — no release has been published yet, so the full check → download → install →
relaunch cycle, including which privilege-escalation prompt actually appears on Pop!_OS
24.04/COSMIC for `.deb`, remains unverified. Tracked as the deferred "E9-13 updater
banner smoke test."

## See also

- `docs/release-process.md` — publishing steps, including the draft→published transition.
- `docs/github-actions-security.md` (Control 14) — build provenance attestation.
- `docs/electron-shell-security.md` — IPC channel whitelist entries for `check-update`,
  `download-and-install-update`, `relaunch`.
