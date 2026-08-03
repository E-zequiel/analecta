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
- Generation alone isn't sufficient — `latest-linux.yml` also has to be uploaded as a
  release asset, the same as the installers themselves. `release.yml`'s release-creation
  step does this explicitly; see `docs/release-process.md`.

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
`UpdateBanner.svelte`).

**`v0.3.1`, `v0.4.0`, and `v0.5.0` all shipped without `latest-linux.yml` attached as a
release asset** — `release.yml`'s release-creation step never included it in the upload,
even though electron-builder generated it correctly on every build. `electron-updater`'s
GitHub provider had nothing to resolve, so every update check failed — silently, since
only a `console.error` in the main process observes the `error` event, with nothing wired
to the UI. This was masked for a while by the repo being private (which independently
blocks the GitHub provider's unauthenticated API calls), but going public didn't fix it —
the missing asset was the real gate. Fixed by adding the file to the upload step and a
build-time check that fails the release job if it's ever missing again (see
`docs/release-process.md`). `v0.5.0` itself was not patched retroactively — a
locally-rebuilt `latest-linux.yml` isn't guaranteed to hash-match the installers already
signed and distributed for that release.

## See also

- `docs/release-process.md` — publishing steps, including the draft→published transition.
- `docs/github-actions-security.md` (Control 14) — build provenance attestation.
- `docs/electron-shell-security.md` — IPC channel whitelist entries for `check-update`,
  `download-and-install-update`, `relaunch`.
