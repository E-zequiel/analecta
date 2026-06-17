# Dependency Integrity Verification

This document describes how dependency integrity is verified before and
after adding or upgrading a direct dependency, for both ecosystems in this
repo (npm/pnpm in `frontend/` and `electron/`, Python/uv in `backend/`) —
distinct from the automated, lockfile-wide attestation sweep in
`scripts/verify-provenance.py` (npm/pnpm only; see
[Relationship to `verify-provenance.py`](#relationship-to-verify-provenancepy)).

---

## Threat model

A registry-level MITM, a compromised maintainer account, or a tampered CDN
edge node could serve a tarball that doesn't match what the package's
`package.json` claims to be. pnpm verifies the downloaded tarball against the
registry's published `dist.integrity` hash automatically during install — but
that only protects against the tarball changing **after** the registry
metadata was fetched. It does not by itself give a person a moment to look at
the hash and notice something is wrong (wrong package, typo-squatted name,
suspiciously fresh publish) before the code lands in `node_modules/` and the
lockfile.

The same threat applies to PyPI, but the npm/pnpm and Python/uv toolchains
satisfy it through different mechanisms — see the two sections below.

The procedure below adds that moment, and produces a paper trail in the PR/commit.

## Procedure (npm / pnpm)

### 1. Pre-install — fetch the published hash

```bash
mise exec -- pnpm view "<pkg>@<version>" dist.integrity
```

This queries the npm registry's metadata endpoint directly — not a web
search, not `raw.githubusercontent.com` (rejected for the same reason given
in `docs/github-actions-security.md` § "When auditing `allowBuilds` entries":
it's GitHub's CDN for raw content, also widely used by malware as free
hosting, so a hit there proves nothing). Save this value; compare it against
the lockfile entry in step 4.

### 2. Pin the exact version

Always pin a direct dependency to the exact version checked in step 1 — never
a range (`^4`, `~4.25`, etc.).

A range doesn't protect anything day to day: `pnpm install --frozen-lockfile`
(used in CI and recommended locally) installs whatever `pnpm-lock.yaml`
already resolved to, regardless of what the range in `package.json` says. What
a range actually does is let a *future* `pnpm update` silently move to a new
version inside that range without touching `package.json` — the diff a
reviewer sees is just the lockfile, with no explicit "X → Y" version bump to
catch in review. An exact pin forces that bump to show up as a one-line
`package.json` diff, which is the more auditable failure mode.

Before pinning a version that just published, check the **10-day minimum
window** policy:

```bash
mise exec -- pnpm view <pkg> time --json
```

The publish timestamp for `<version>` appears under the version key as an
ISO 8601 date string. The version must have been published at least 10 days
before the install date.

If the package is one of a matched pair/family (e.g. a theme's light/dark
siblings from the same publisher), pin both to the same exact version to avoid
drift between them.

This is about *new* dependencies going forward — it is not a proposal to
retroactively migrate every existing range in `frontend/package.json`; that
would be a separate, larger decision.

CVE-driven patches of *transitive* dependencies use a different mechanism
(`pnpm.overrides` — see `CLAUDE.md`) but the same exact-pin-plus-hash-check
principle.

### 3. Install

```bash
mise exec -- pnpm install
```

pnpm verifies the downloaded tarball's hash against the registry's
`dist.integrity` internally and **fails the install** on a mismatch — this
step doesn't trust the hash from step 1 blindly, it's independently re-derived
from the actual bytes pnpm downloaded.

### 4. Post-install — cross-check the lockfile

```bash
grep -A 1 "'<pkg>@<version>'" pnpm-lock.yaml
```

Confirm the `integrity:` line matches the value saved in step 1. If they
differ, do not proceed — abort and investigate the discrepancy before merging.
This value is what pnpm verified the tarball against, written into a file
that's committed and diffable — so any subsequent `pnpm install` on a
different machine gets the same guarantee without re-running this procedure.

## Worked example (npm / pnpm)

Adding `@uiw/codemirror-theme-tokyo-night-day` (light counterpart of an
already-installed dark theme), pinned to match the dark sibling's version
exactly:

```bash
$ mise exec -- pnpm view "@uiw/codemirror-theme-tokyo-night-day@4.25.10" dist.integrity
sha512-n7SJGwAY5KCtikj8xru5OrT3maEtrYnjMI2LGTWjCO+yA5TTMFOTsVLtT7HMesGQ2db61iMHKEgn7Kkhqs6/8g==

$ mise exec -- pnpm install
# ... pnpm downloads + verifies internally ...

$ grep -A 1 "@uiw/codemirror-theme-tokyo-night-day@4.25.10" pnpm-lock.yaml
  '@uiw/codemirror-theme-tokyo-night-day@4.25.10':
    resolution: {integrity: sha512-n7SJGwAY5KCtikj8xru5OrT3maEtrYnjMI2LGTWjCO+yA5TTMFOTsVLtT7HMesGQ2db61iMHKEgn7Kkhqs6/8g==}
```

Hash from step 1 matches the lockfile from step 4 — verified.

## Procedure (Python / uv)

There is **no manual pre-install hash-fetch step here** — this is a deliberate
difference from the npm/pnpm flow above, not an omission. `uv` resolves the
dependency graph and writes the resolved version *and* its hashes (sdist +
every wheel) into `backend/uv.lock` as part of `uv lock`. The verification
moment isn't "fetch a hash and compare it before installing" —
it's "the lockfile — visible in the PR diff — already contains
the hash uv independently derived from PyPI." `uv sync` then verifies
installed packages against `uv.lock`, the same way pnpm verifies against
`pnpm-lock.yaml`.

### 1. Force the version with a constraint

CVE-driven patches of *transitive* Python dependencies use a version floor in
`backend/pyproject.toml`:

```toml
# (request.url.hostname poisoning). starlette is a transitive dep (fastapi,
# sse-starlette); fastapi only requires >=0.46.0, so a floor here is sufficient.
constraint-dependencies = ["starlette>=1.3.1"]
```

Note this is a **floor** (`>=`), not an exact pin — unlike the npm/pnpm
convention above. uv still resolves to one exact version and locks it; the
floor just expresses "anything below this has the CVE," letting uv pick the
lowest version that satisfies both the floor and every other package's
constraints, rather than forcing a specific patch release.

### 2. Re-lock

```bash
cd backend && mise exec -- uv lock
```

This re-resolves the graph and rewrites `backend/uv.lock`, including the
hashes for whatever version the constraint settled on.

### 3. Review the lockfile diff

```bash
git diff backend/uv.lock
```

is the equivalent checkpoint to step 4 of the npm/pnpm procedure — except here
the diff itself shows you the hash uv recorded, instead of you fetching a hash
separately and comparing it to the diff.

### 4. Gate on `check.sh`

Both the npm/pnpm and Python/uv flows end the same way: `./scripts/check.sh`
must pass before the change is considered done (see `docs/quality-gate.md`).

## Worked example (Python / uv)

The `starlette>=1.3.1` constraint above resolved and locked like this:

```toml
# backend/uv.lock
[[package]]
name = "starlette"
version = "1.3.1"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "...", hash = "sha256:05d0213193f2fbaae60e2ecb593b4add4262ad4e46536b54abe36f11a71724e0", ... }
wheels = [
    { url = "...", hash = "sha256:c7372aae11c3c3f26a42df7bd626cec2f47d03483d261d369516a615a53714c6", ... },
]
```

No separate `pip show`/PyPI-API round trip was run by hand — the `hash =`
fields are uv's own resolution output, committed and reviewable as-is.

## Relationship to `verify-provenance.py`

`verify-provenance.py` only parses `pnpm-lock.yaml` — it has no Python/uv
counterpart in this repo. The table below is npm/pnpm-specific.

| | This procedure | `scripts/verify-provenance.py` |
|---|---|---|
| Scope | One package, at the moment it's added/upgraded | Every package in `pnpm-lock.yaml`, on demand |
| What it checks | `dist.integrity` hash, fetched and cross-checked manually | Sigstore/SLSA provenance attestation (Fulcio cert chain + Rekor inclusion proof), where one exists |
| When it runs | Manually, during the PR that touches `package.json` | Manually or in CI, as a standing sweep |
| Catches | Tarball/metadata mismatch at install time, with a person checking it | Forged attestation, missing/broken provenance for already-installed packages |

They're complementary, not redundant: this procedure is the one
person-in-the-loop check at the moment a *new* dependency enters the tree; the
script is a broader, automatable check for packages that *publish*
attestations (~40% of the ecosystem as of this writing).

## See also

- `CLAUDE.md` → CVE-driven patch policy (`pnpm.overrides` for transitive deps,
  `[tool.uv] constraint-dependencies` for Python) — same verification step,
  different mechanism for *why* the version is being forced.
- `docs/syntax-highlighting.md` — documents the Shiki side of light-theme
  syntax highlighting (the markdown viewer). `@uiw/codemirror-theme-tokyo-night-day`,
  the package used as the worked example above, is the equivalent fix for the
  CodeMirror editor, applied separately in `MarkdownEditor.svelte`.
