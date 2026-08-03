# Release Process

> Living document. Update whenever `release.yml`, the manifest files, or the branching
> convention change.

---

## Day-to-day development

- Each feature or fix lives on its own short-lived branch, merged to `main` individually
  as soon as it is ready — never accumulated on one long-lived branch.
- Every PR that changes user-facing behavior adds its line under `## [Unreleased]` in
  `CHANGELOG.md`, in the same commit (enforced by `.githooks/commit-msg` for `feat`/`fix`
  commits). This keeps `## [Unreleased]` on `main` current at all times, so cutting a
  release is a rename, not a reconstruction.
- All merges into `main` go through a GitHub pull request and are merged there. Branch
  protection is configured on `main`.

---

## Cutting a release

1. Create a branch `release/vX.Y.Z` from `main`.
2. Bump the version in all 4 manifests:
   - `package.json` (repo root)
   - `frontend/package.json`
   - `electron/package.json`
   - `backend/pyproject.toml`
3. Run `mise exec -- uv lock` from `backend/` so `uv.lock` records the matching version.
4. In `CHANGELOG.md`: rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and add a
   fresh empty `## [Unreleased]` above it. Update the link references at the bottom of
   the file (`[Unreleased]: .../compare/vX.Y.Z...HEAD` and
   `[X.Y.Z]: .../releases/tag/vX.Y.Z`).
5. Open a PR `release/vX.Y.Z → main`, get it reviewed, merge via GitHub.

---

## Tagging

Once the release PR is merged, tag `main` as `vX.Y.Z` (signed) and push the tag.

---

## CI release build

Pushing a `vX.Y.Z` tag triggers `.github/workflows/release.yml`:

1. Builds the Python sidecar (PyInstaller), the frontend (Vite), and the Electron shell.
2. Packages `.deb`, `.rpm`, and `.AppImage` with `electron-builder`.
3. Generates `SHA256SUMS` over the three packaged installers.
4. Generates a Sigstore build provenance attestation for the three installers via
   `actions/attest-build-provenance` (confirmed running since the repo went public,
   `v0.5.0`); see `docs/github-actions-security.md` Control 14.
5. Extracts the `## [X.Y.Z]` section from `CHANGELOG.md` as the release notes. The job
   fails if no heading matches the tagged version — the CHANGELOG rename in the release
   PR must land before the tag is pushed.
6. Verifies `dist-electron/latest-linux.yml` was generated (electron-builder writes it
   automatically whenever `publish:` is configured, regardless of `--publish never`) —
   `electron-updater`'s GitHub provider needs this file to resolve the latest version and
   its SHA-512 checksum; see `docs/auto-update.md`. Fails the job loudly if it's missing,
   rather than silently shipping a release existing installs can't discover.
7. Creates the GitHub Release as a **draft**, with the built packages, `SHA256SUMS`, and
   `latest-linux.yml` attached as assets.

The workflow only runs against `E-zequiel/analecta` (guarded), so it never fires on forks.

---

## Verification before publishing

- Download the installer package and `SHA256SUMS` from the draft release's assets instead of
  building locally.
- Verify the download against the checksums file: `sha256sum -c SHA256SUMS` (run from the
  directory containing the downloaded installer).
- Install it (e.g. `sudo apt install ./analecta_X.Y.Z_amd64.deb`) and confirm the app
  launches and behaves as expected. Package/artifact naming is set explicitly in
  `electron-builder.yml`'s `deb`/`rpm` blocks — see
  `docs/electron-builder-linux-package-naming.md` if it ever reverts to `analecta-electron`.

---

## Publishing

`electron-updater`'s GitHub provider cannot see draft releases. Once verification passes,
publish the release manually on GitHub — only then will existing installs pick it up as
an update.
