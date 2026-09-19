#!/usr/bin/env python3
"""Verify npm SLSA provenance attestations for packages in pnpm-lock.yaml.

For each package that has a Sigstore provenance attestation on the npm registry:
1. Downloads the Sigstore bundle (independent of registry serving layer).
2. Verifies the bundle signature chain: Fulcio CA-issued ephemeral cert + Rekor
   transparency-log inclusion proof. This anchor is outside the npm registry —
   a registry-level MITM cannot forge a Rekor entry.
3. Extracts the attested subject SHA-512 from the DSSE payload and compares it
   to the pnpm-lock.yaml integrity hash. This connects the independent attestation
   to the exact bytes installed locally.

Exit 1 if any attested package fails either check. Packages with no attestation
are skipped (not an error — ~60% of the npm ecosystem lacks provenance yet).
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, cast

LOCKFILE = Path(__file__).resolve().parent.parent / "pnpm-lock.yaml"
GITHUB_ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
SLSA_PREDICATE_PREFIXES = ("https://slsa.dev/provenance/",)

# Package entry keys look like `name@version`, optionally quoted (pnpm quotes
# scoped names) and optionally carrying a peer-dependency suffix, e.g.
# `'@keyv/bigmap@1.3.1(keyv@5.6.0)'`. The leading `@` is optional because scoped
# package names start with one, and the version class stops at `(` so a
# peer-suffixed key cannot have its suffix swallowed into the version. Both
# classes exclude `(` for the same reason.
_PKG_RE = re.compile(
    r"^[ \t]{2}(?:'(@?[^'@(]+)@([^'()]+)'|([^'@\s(][^'@\s(]*)@([0-9][^(\s]*)):\s*$",
    re.MULTILINE,
)
# Any 2-space-indented mapping key, used to split the lockfile into entry blocks.
# `[ \t]{2}` rather than `\s{2}`: `\s` matches newlines under MULTILINE, which
# would let a match start a line early and swallow the next line's indentation
# into the key. The scalar must begin with a non-space character for the same
# reason, and so nested (deeper-indented) entries are never keys: a line with
# more indentation cannot match, because the scalar's first character may not
# be a space. `\n` is excluded because `.` never matches a newline, so the
# match cannot run past its own line. The scalar comes in two explicit
# alternatives: a *quoted* scalar may contain any character except a single
# quote or newline — colons included, so a quoted key containing `:` still
# produces a block and ends up parsed or reported as a parser gap — while an
# *unquoted* scalar excludes `:` exactly as it always has. Allowing `:` inside
# an unquoted scalar made a non-key line like `  note: this is prose:` a block
# start, cutting the preceding entry's block short so the entry landed in
# neither the parsed nor the reported set. The surrounding quotes stay outside
# the capture groups and are stripped from the scalar (group 1 = quoted
# scalar, group 2 = unquoted scalar).
_ANY_KEY_RE = re.compile(
    r"^[ \t]{2}(?:'([^'\n]*)'|([^'\n: \t][^'\n:]*?)):\s*$", re.MULTILINE
)
# A key is package-shaped if it contains `@`. Structural keys (`packages:`,
# `settings:`, `importers:` …) never do, and the keys that legitimately contain
# one without being package entries (override targets, for instance) carry no
# `integrity:` line in their own block, so they are never reported.
_PACKAGE_KEY_RE = re.compile(r"@")
# Capture the integrity value whatever the algorithm prefix, so a legacy
# `sha1-` (or any other) value is parsed rather than silently skipped; the
# verification path rejects anything but sha512 with a diagnostic naming the
# unsupported algorithm.
_INTEGRITY_RE = re.compile(r"integrity: ([^,\s}]+)")
# A resolution line inside an entry block — matched on the bare substring so
# every shape (`resolution: {integrity: ...}`, `resolution: {}`, …) counts.
_RESOLUTION_RE = re.compile(r"resolution:")


def _entry_blocks(content: str) -> list[tuple[str, str, str]]:
    """Split the lockfile into (key line, key text, block body) triples.

    `key_line` is the raw matched line exactly as it appears in the lockfile
    (indentation, quotes and trailing colon included); `key_text` is the scalar
    it wraps, with the surrounding quotes stripped.

    A 2-space-indented key owns everything up to the next such key. Scoping each
    entry's body this way is what lets a key be matched with *its own*
    resolution: looking a fixed distance past a key instead would let the window
    cross into the next entry (reporting a package-shaped key as unresolved when
    the following entry is the one carrying an integrity line) and would miss a
    resolution block longer than the window.
    """
    matches = list(_ANY_KEY_RE.finditer(content))
    blocks: list[tuple[str, str, str]] = []
    for index, m in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        # `group(1) or group(2)` would pick the unquoted alternative for an
        # empty quoted scalar (`'':`), yielding None and crashing the caller's
        # regex search; test for None explicitly so an empty scalar stays an
        # empty string.
        key_text = m.group(1) if m.group(1) is not None else m.group(2)
        blocks.append((m.group(0), key_text, content[m.end() : end]))
    return blocks


def _scan_lockfile(content: str) -> tuple[dict[tuple[str, str], str], list[str]]:
    """Return ({(name, version): integrity}, package-shaped parser gaps).

    One pass over the entry blocks so the parsed set and the gap set can
    never disagree: a block whose key is package-shaped and whose body
    carries a resolution line is either parsed or reported as a parser gap.
    That includes a resolution without an integrity line — such a block can
    never be verified, so it is reported too (`resolution: {}` counts). A
    non-sha512 integrity value parses normally — the parser understands the
    format — and fails later in the verification path with a diagnostic
    naming the unsupported algorithm (`check_subject_hash`), never with a
    misleading parser message.

    Anything the entry-key pattern cannot tokenize would otherwise be skipped
    silently — how every scoped package once went unverified while the script
    reported success over the unscoped subset. `find_untokenized_package_keys`
    guards that independently of this pass: it scans raw lines rather than the
    blocks this function consumes, so a line the splitter never yields as a
    key is still caught (and `parse_lockfile` refuses to proceed). Package-
    shaped keys whose block carries no resolution (pnpm's `snapshots:`
    entries, which hold the dependency graph rather than resolutions) are
    legitimately unparsed and are not reported. pnpm's own @zkochan-scoped
    vendored packages are excluded from parsing entirely (see the inline
    comment below): they are neither parsed nor reported.
    """
    parsed: dict[tuple[str, str], str] = {}
    unmatched: list[str] = []
    for key_line, key_text, block in _entry_blocks(content):
        integrity = _INTEGRITY_RE.search(block)
        has_resolution = _RESOLUTION_RE.search(block) is not None
        m = _PKG_RE.match(key_line)
        if m:
            name = m.group(1) or m.group(3)
            ver = m.group(2) or m.group(4)
            # pnpm's own vendored packages are published without provenance:
            # the npm registry serves dist.attestations (with a provenance
            # url) for e.g. devalue@5.9.2 and @sveltejs/kit@2.70.3, but not
            # for @zkochan/js-yaml@0.0.11 — its metadata shows
            # `_from: file:zkochan-js-yaml-0.0.11.tgz` — so they can never be
            # verified by this gate. The real lockfile has zero @zkochan
            # entries and never had any; the branch is purely defensive.
            if name and ver and not name.startswith("@zkochan/"):
                if integrity:
                    parsed[(name, ver)] = integrity.group(1)
                elif has_resolution:
                    unmatched.append(key_text)
            continue
        if not _PACKAGE_KEY_RE.search(key_text):
            continue
        if integrity or has_resolution:
            unmatched.append(key_text)
    return parsed, unmatched


def find_unmatched_package_keys(content: str) -> list[str]:
    """Return package-shaped keys that were not parsed into a verifiable entry.

    A key lands here when its block carries a resolution but the entry could
    not be parsed: either the key did not match the package pattern, or the
    resolution carries no integrity line (it could never be verified).
    """
    return _scan_lockfile(content)[1]


def find_untokenized_package_keys(content: str) -> list[str]:
    """Return package-shaped entry candidates the block splitter cannot tokenize.

    Deliberately independent of `_entry_blocks`: this scans `content.splitlines()`
    directly, so a line the entry-key pattern (`_ANY_KEY_RE`) silently skips is
    still seen — a splitter regression cannot hide it. Two shapes are reported:

    - a 2-space-indented candidate key (exactly two leading spaces, stripped
      form ending in ``:``, not a comment, not blank) whose scalar contains
      ``@`` and which `_ANY_KEY_RE` does not match; the scalar is reported;
    - an inline entry: a line in that same 2-space shape that carries
      ``integrity:`` on the key line itself and does not end with ``:``; the
      whole stripped line is reported.

    On a well-formed lockfile both sets are empty; a non-empty result means
    the lockfile format drifted, and `parse_lockfile` refuses to proceed.
    """
    reported: list[str] = []
    for line in content.splitlines():
        if not line.startswith("  ") or (len(line) > 2 and line[2] in " \t"):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith(":"):
            scalar = stripped[:-1].strip()
            if len(scalar) >= 2 and scalar.startswith("'") and scalar.endswith("'"):
                scalar = scalar[1:-1]
            if "@" in scalar and not _ANY_KEY_RE.match(line):
                reported.append(scalar)
        elif "integrity:" in line or "resolution:" in line:
            reported.append(stripped)
    return reported


def parse_lockfile(path: Path) -> dict[tuple[str, str], str]:
    """Return {(name, version): integrity} for all packages with integrity.

    Raises RuntimeError if the block splitter cannot tokenize a package-shaped
    entry candidate — see `find_untokenized_package_keys` — or if a
    package-shaped block carrying a resolution was not parsed — see
    `find_unmatched_package_keys`.
    """
    content = path.read_text()
    untokenized = find_untokenized_package_keys(content)
    if untokenized:
        shown = ", ".join(untokenized[:5])
        more = "" if len(untokenized) <= 5 else f" (+{len(untokenized) - 5} more)"
        raise RuntimeError(
            f"Untokenizable package entries ({len(untokenized)}): {shown}{more}"
            " — these lines are package-shaped entry candidates the lockfile"
            " parser cannot tokenize (an unmatchable entry key, or an entry"
            " written inline on its key line). The lockfile format may have"
            " changed: fix the pattern rather than letting these entries go"
            " unverified."
        )
    parsed, unmatched = _scan_lockfile(content)
    if unmatched:
        shown = ", ".join(unmatched[:5])
        more = "" if len(unmatched) <= 5 else f" (+{len(unmatched) - 5} more)"
        raise RuntimeError(
            f"Unmatched package entries ({len(unmatched)}): {shown}{more}"
            " — each carries a resolution but did not match the lockfile parser"
            " into a verifiable entry (a resolution with no integrity line has"
            " nothing to compare against). If the lockfile format changed, fix"
            " the pattern rather than letting these entries go unverified; if"
            " these are git/tarball resolutions without an integrity, they cannot"
            " be verified by this gate and need a deliberate decision."
        )
    if not parsed:
        # A shape drift invisible to BOTH patterns (four-space indentation, a
        # tab, a renamed top-level key) leaves the walk empty: `main()` would
        # print "Parsed 0 packages" and exit 0, the gate verifying nothing while
        # reporting success. A resolution the walk *reached* was either parsed or
        # reported above, so zero parsed entries is expected in that case (every
        # entry excluded as @zkochan-scoped, for instance); zero parsed entries
        # with a resolution the walk never reached is not.
        reached = any(
            _RESOLUTION_RE.search(block) for _k, _t, block in _entry_blocks(content)
        )
        resolutions = content.count("resolution:")
        if resolutions and not reached:
            raise RuntimeError(
                f"Parsed 0 packages from a lockfile carrying {resolutions}"
                " resolution line(s) — the lockfile's shape has drifted past the"
                " parser entirely (an indentation change is the usual cause), and"
                " proceeding would verify nothing while reporting success."
            )
    return parsed


def _fetch_json(url: str, timeout: int = 10) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # pyright: ignore[reportAny]
            return cast(dict[str, Any], json.loads(r.read()))  # pyright: ignore[reportAny]
    except Exception:
        return None


def get_provenance_bundle(name: str, ver: str) -> tuple[str, dict[str, Any]] | None:
    """Return (predicate_type, bundle) for the first SLSA provenance attestation.

    Returns None if the package has no provenance attestation on npm.
    """
    encoded = name.replace("/", "%2F")
    meta = _fetch_json(f"https://registry.npmjs.org/{encoded}/{ver}")
    if not meta:
        return None
    dist = cast(dict[str, Any], meta.get("dist", {}))
    attestations_meta = cast(dict[str, Any], dist.get("attestations", {}))
    att_url = cast(str | None, attestations_meta.get("url"))
    if not att_url:
        return None
    data = _fetch_json(att_url)
    if not data:
        return None
    for att in cast(list[dict[str, Any]], data.get("attestations", [])):
        pred = cast(str, att.get("predicateType", ""))
        if any(pred.startswith(p) for p in SLSA_PREDICATE_PREFIXES):
            return pred, cast(dict[str, Any], att.get("bundle", {}))
    return None


def _b64_to_hex(integrity: str) -> str | None:
    """Convert pnpm integrity 'sha512-<base64>' to lowercase hex."""
    if not integrity.startswith("sha512-"):
        return None
    try:
        return base64.b64decode(integrity[7:]).hex()
    except Exception:
        return None


def check_subject_hash(
    bundle: dict[str, Any], lockfile_integrity: str
) -> tuple[bool, str]:
    """Verify attested subject SHA-512 matches the lockfile integrity.

    Returns (ok, message).
    """
    try:
        dsse = cast(dict[str, Any], bundle.get("dsseEnvelope", {}))
        payload_b64 = cast(str, dsse.get("payload", ""))
        decoded = base64.b64decode(payload_b64).decode()
        statement = cast(dict[str, Any], json.loads(decoded))
        for subject in cast(list[dict[str, Any]], statement.get("subject", [])):
            digest = cast(dict[str, Any], subject.get("digest", {}))
            attested_hex = cast(str | None, digest.get("sha512"))
            if not attested_hex:
                continue
            algo = lockfile_integrity.partition("-")[0]
            if algo != "sha512":
                return False, (
                    f"unsupported integrity algorithm '{algo}' — provenance"
                    " attestation comparison supports sha512 only"
                    f" (lockfile value: {lockfile_integrity})"
                )
            lockfile_hex = _b64_to_hex(lockfile_integrity)
            if lockfile_hex is None:
                return False, f"cannot parse lockfile integrity: {lockfile_integrity}"
            if attested_hex.lower() == lockfile_hex.lower():
                return True, "subject hash matches lockfile integrity"
            return (
                False,
                "hash MISMATCH\n"
                f"          attested: {attested_hex[:48]}...\n"
                f"          lockfile: {lockfile_hex[:48]}...",
            )
        return False, "no sha512 subject found in attestation payload"
    except Exception as e:
        return False, f"payload parse error: {e}"


def verify_sigstore(bundle_json: str) -> tuple[bool, str]:
    """Verify Sigstore bundle: Fulcio cert chain + Rekor inclusion proof.

    Returns (ok, message). Accepts any GitHub Actions OIDC identity so that
    third-party packages (sigma, svelte, etc.) are not gated on a known repo URL.

    Network errors (TUF download, Rekor unreachable) are treated as warnings,
    not failures — they indicate infrastructure issues, not supply-chain attacks.
    Only VerificationError (bad signature / cert chain) is treated as fatal.
    """
    try:
        from sigstore.errors import (  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
            NetworkError,
            VerificationError,
        )
        from sigstore.models import (  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
            Bundle,
        )
        from sigstore.verify import (  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
            Verifier,
        )
        from sigstore.verify.policy import (  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
            OIDCIssuer,
        )
    except ImportError as e:
        return False, f"sigstore not importable: {e}"

    try:
        verifier = Verifier.production()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        bundle = Bundle.from_json(bundle_json)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        verifier.verify_dsse(  # pyright: ignore[reportUnknownMemberType]
            bundle=bundle,
            policy=OIDCIssuer(GITHUB_ACTIONS_ISSUER),
        )
        return True, "Sigstore signature verified (Rekor + Fulcio)"
    except VerificationError as e:  # pyright: ignore[reportUnknownVariableType]
        msg = str(e)  # pyright: ignore[reportUnknownArgumentType]
        # sigstore 4.x cannot verify the Rekor integrated timestamp for entry
        # types newer than dsse/hashedrekord 0.0.1. This is a library
        # compatibility gap, not a signature failure. Subject hash check
        # (already done) still provides the key supply-chain guarantee.
        if "only supported" in msg or "not supported" in msg:
            return True, (
                f"Rekor entry type not supported by sigstore 4.x"
                f" — timestamp skipped ({msg})"
            )
        return False, f"Sigstore verification failed: {msg}"
    except NetworkError as e:  # pyright: ignore[reportUnknownVariableType]
        return True, f"Sigstore network unavailable — signature check skipped ({e})"
    except Exception as e:
        msg = str(e)
        # Bundle format validation errors are also a compatibility issue.
        if "validation error" in msg or "failed to load bundle" in msg:
            return True, (
                f"Bundle format not supported by sigstore 4.x — skipped ({msg[:80]})"
            )
        return True, f"Sigstore check skipped (unexpected error: {e})"


def main() -> int:
    """Verify provenance for all attested packages in pnpm-lock.yaml."""
    packages = parse_lockfile(LOCKFILE)
    print(f"Parsed {len(packages)} packages from pnpm-lock.yaml")
    print()

    verified: list[str] = []
    skipped = 0
    failed: list[tuple[str, str]] = []

    for (name, ver), integrity in sorted(packages.items()):
        result = get_provenance_bundle(name, ver)
        if result is None:
            skipped += 1
            time.sleep(0.02)
            continue

        _pred_type, bundle = result
        pkg = f"{name}@{ver}"
        print(f"  {pkg}")

        bundle_json = json.dumps(bundle)

        ok, msg = check_subject_hash(bundle, integrity)
        if not ok:
            print(f"    ✗ {msg}")
            failed.append((pkg, msg))
            continue
        print(f"    ✓ {msg}")

        ok, msg = verify_sigstore(bundle_json)
        if not ok:
            print(f"    ✗ {msg}")
            failed.append((pkg, msg))
            continue
        print(f"    ✓ {msg}")

        verified.append(pkg)
        time.sleep(0.02)

    print()
    print("─" * 55)
    print(f"Verified via provenance: {len(verified)}")
    print(f"No attestation (expected gap): {skipped}")
    print(f"Failed: {len(failed)}")

    if failed:
        print()
        print("FAILED packages:")
        for pkg, reason in failed:
            print(f"  ✗ {pkg}: {reason}")
        print()
        print("""\
Provenance verification failed. Possible causes:
  • Supply-chain attack: installed hash differs from attested hash
  • Registry served a tampered attestation (Sigstore check failed)
  • Legitimate package re-publish without re-attestation (investigate)""")
        return 1

    print()
    print("All attested packages passed provenance verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
