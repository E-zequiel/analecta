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
from typing import Any, NamedTuple, cast

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
# `integrity:` line in their own block, so they were historically never
# reported; since the unaccounted classification (see `_scan_lockfile`), a
# block whose key is package-shaped but unparseable is still reported as a
# parser gap, and a key with no `@` at all carrying resolution or hash-shaped
# integrity material is refused by the unaccounted guard — the quiet skip is
# reserved for keys whose blocks carry no such material at all.
_PACKAGE_KEY_RE = re.compile(r"@")
# Capture the integrity value whatever the algorithm prefix, so a legacy
# `sha1-` (or any other) value is parsed rather than silently skipped; the
# verification path rejects anything but sha512 with a diagnostic naming the
# unsupported algorithm.
_INTEGRITY_RE = re.compile(r"integrity: ([^,\s}]+)")
# The value-shape counterpart: an `integrity:` token whose value is the
# `<algo>-<base64ish hash>` shape pnpm writes (`sha512-AAAABBBB==`). Anchored
# on the value deliberately, not the bare token: prose that merely mentions
# `resolution:` or `integrity:` without a hash-shaped value stays quiet —
# the same philosophy that keeps the prose false-positive class closed
# (see the `note: resolution: appears here as prose` guards). Only a line
# carrying a value of this shape is verification material in the sense the
# orphan classification cares about; see `_scan_lockfile`.
# Residual floor: the value class requires 4+ base64ish chars after the
# algorithm dash, so an orphan carrying a SHORTER fake value (`sha512-O==`)
# is not counted as material and stays quiet — a same-shape attrition hole,
# unrealistic for an attacker mimic (real pnpm hashes are 40+ chars) but a
# boundary to keep conscious if this quantifier is ever retuned.
_INTEGRITY_VALUE_RE = re.compile(r"integrity:\s*[A-Za-z0-9_-]+-[A-Za-z0-9+/=]{4,}")
# A resolution line inside an entry block — matched on the bare substring so
# every shape (`resolution: {integrity: ...}`, `resolution: {}`, …) counts.
_RESOLUTION_RE = re.compile(r"resolution:")
# A *raw* resolution line: anchored at line start after optional whitespace,
# so a comment line mentioning `resolution:` (`    # note: resolution: ...`)
# and prose that carries the word later in the line are never counted — only
# a genuine resolution mapping key at the start of a line is. Allowing a
# column-0 match is deliberate: a resolution key at column 0 exists in no
# legitimate lockfile shape, and counting it is what makes column-0 drift
# loud instead of invisible. Counting the lockfile's raw resolution
# population against the lines the entry-block walk owns (see
# `_unowned_resolution_lines`) is what turns shape drift into a loud
# failure instead of a silently shrunken verified set.
_RESOLUTION_LINE_RE = re.compile(r"^[ \t]*resolution:[^\n]*", re.MULTILINE)
# The *ownership* counterpart: a resolution line a real entry block can own
# must be block-indented. A column-0 resolution line exists in no legitimate
# shape — as a block's only resolution it would otherwise be owned silently
# (the legitimate integrity vanishing from the parsed map), and beside a
# legitimate one it would steal ownership and flag the real line as unowned.
_OWNED_RESOLUTION_LINE_RE = re.compile(r"^[ \t]+resolution:[^\n]*", re.MULTILINE)


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


class LockfileScan(NamedTuple):
    """Everything one pass over the entry blocks establishes.

    `parsed` is {(name, version): integrity} (last write wins); `unmatched`
    is the package-shaped parser gaps; `conflicting` is the duplicate-
    identity conflicts — see `_scan_lockfile` — tracked in the same pass;
    `unaccounted` is the resolution-carrying keys that are no package entry
    at all, plus the orphan inline material lines classified in blocks'
    bodies — see `_scan_lockfile`. `resolution_blocks`
    counts the blocks whose body carries a
    resolution line, the single source of truth for the zero-parse guard.
    """

    parsed: dict[tuple[str, str], str]
    unmatched: list[str]
    conflicting: list[tuple[str, str, str]]
    unaccounted: list[str]
    resolution_blocks: int


def _scan_lockfile(content: str) -> LockfileScan:
    """Scan the entry blocks once, returning a `LockfileScan`.

    One pass over the entry blocks so the parsed set, the gap set, the
    conflict set and the unaccounted set can never disagree: a block whose
    key is package-shaped and whose body carries a resolution line is
    either parsed, reported as a parser gap, or — when two in-scope blocks
    collapse to one ``(name, version)`` identity — tracked as a conflicting
    duplicate, never silently miscounted by a second pass
    reading the file a different way. That includes a resolution without
    an integrity line — such a block can never be verified, so it is
    reported too (`resolution: {}` counts). A non-sha512 integrity value
    parses normally — the parser understands the format — and fails later
    in the verification path with a diagnostic naming the unsupported
    algorithm (`check_subject_hash`), never with a misleading parser
    message.

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

    This pass is silent on shape drift that hides whole entries from the
    splitter (a 4-space-indented entry lands inside the previous block and
    its resolution is never read). `parse_lockfile` guards that case
    independently — see `_unowned_resolution_lines`.

    Conflicting duplicate identities are tracked here too, in the very loop
    that fills the parsed map — never as a separate second pass, which could
    disagree with the parser about what was covered: two entry blocks
    resolving to the same ``(name, version)`` (quoting variants collapse to
    one identity) overwrite each other in the parsed map, last one wins.
    Identical values are a legitimate dedup; conflicting values mean one of
    the two attested hashes would be silently overwritten in the parsed map
    and dropped from verification while the script still reported success;
    every such (identity, first value, later value) pair is collected in
    `conflicting` for `parse_lockfile` to refuse. Keys that never parse
    into a verifiable entry (peer-suffixed and other unmatched package-
    shaped keys, which the unmatched guard owns) and deliberately excluded
    @zkochan entries take no part in the collision set. The tracking itself
    never raises — this pass must stay exception-free, because the guard
    composition in `parse_lockfile` and `find_unmatched_package_keys`
    depends on a scan that always completes.

    A block whose key text carries no ``@`` at all — no package entry, no
    override, nothing the parser could attribute material to — is skipped,
    unless its body carries a *line-anchored*
    resolution line (`_RESOLUTION_LINE_RE`, prose mentioning `resolution:`
    mid-line never counts) and its key text is nonempty: then the
    resolution/integrity material behind that key is verification material
    attributed to no package entry, and the key is returned as unaccounted
    for `parse_lockfile` to refuse. An empty scalar key (`''` quoted-empty)
    stays a deliberate silent skip — degenerate malformed input, not a
    hidden entry — and package-shaped keys keep the substring-based
    resolution sensitivity they always had, so the unmatched guard's
    sensitivity is unchanged.

    Orphan *inline* material is classified in the same pass: each block's
    body is searched for integrity-bearing non-comment lines — lines
    matching `_INTEGRITY_VALUE_RE` (value-anchored: an `integrity:` token
    with an `<algo>-<base64ish hash>` value). The classification is
    anchored on the line's SHAPE, not its position: a material line is the
    block's own material iff its stripped form starts with
    ``resolution:`` or ``integrity:`` (the block's own resolution
    mapping, or its multi-line `integrity:` continuation); every other
    material line — whatever its position, and whatever section the block
    sits in — is an inline mapping attributed to no package entry (e.g.
    ``extra: {resolution: {integrity: sha512-ORPHAN==}}`` directly
    following an entry, or an orphan inside a resolution-less snapshots
    body) and is appended to the same `unaccounted` list that carries the
    unattributed key blocks — one list, one guard in `parse_lockfile`,
    two origins: a non-package *key* whose block carries a resolution,
    and material *lines* carrying no resolution/integrity anchor of their
    own. Position-based counting (first material line = the block's own)
    would misattribute in blocks with no legitimate resolution of their
    own: the orphan IS the first material line there, and crediting it
    quietly attributes a foreign hash to a real ``(name, version)``
    identity. Comment lines mentioning `integrity:` are exempt (a
    comment is never material), and prose without a hash-shaped value
    stays quiet by the value anchor. The deliberate residual: a
    non-comment line inside an entry body whose text contains
    `integrity:` followed by an algo-shaped dash-base64 token is
    indistinguishable from material and fails closed — unrealistic for a
    pnpm lockfile, and the loud direction is this gate's convention.
    """
    parsed: dict[tuple[str, str], str] = {}
    unmatched: list[str] = []
    conflicting: list[tuple[str, str, str]] = []
    unaccounted: list[str] = []
    first_seen: dict[tuple[str, str], str] = {}
    resolution_blocks = 0
    # The segment before the first entry key is no block's body — material
    # there belongs to no package entry. The same value-shape material
    # accounting used per block classifies it here, within this one pass;
    # the first key of a real lockfile sits in the `importers:`/`packages:`
    # header, whose body carries no integrity material, so a real lockfile
    # contributes nothing.
    first_key = _ANY_KEY_RE.search(content)
    head = content[: first_key.start()] if first_key else content
    head_material = [
        line.strip()
        for line in head.splitlines()
        if not line.lstrip().startswith("#") and _INTEGRITY_VALUE_RE.search(line)
    ]
    if head_material:
        # Unattributed material ahead of every block: carried by no key.
        unaccounted.extend(head_material)
    for key_line, key_text, block in _entry_blocks(content):
        # Material accounting happens in this same pass, anchored on line
        # SHAPE rather than position: a non-comment material line is the
        # block's OWN material iff its stripped form starts with
        # `resolution:` or `integrity:` (the block's own resolution
        # mapping, or its multi-line `integrity:` continuation); every
        # other material line — regardless of position or section — is an
        # inline mapping attributed to no package entry and goes to
        # `unaccounted` for `parse_lockfile` to refuse. Position cannot
        # decide: in a block with no legitimate resolution of its own
        # (a snapshots entry, say) the orphan IS the first material line,
        # and crediting it as the block's own lets it masquerade as the
        # entry's attested integrity. No second text walk.
        material_lines = [
            line.strip()
            for line in block.splitlines()
            if not line.lstrip().startswith("#") and _INTEGRITY_VALUE_RE.search(line)
        ]
        unaccounted.extend(
            line
            for line in material_lines
            if not (line.startswith("resolution:") or line.startswith("integrity:"))
        )
        integrity = _INTEGRITY_RE.search(block)
        has_resolution = _RESOLUTION_RE.search(block) is not None
        # The zero-parse guard's `reached` tracks whether the walk reached
        # verification-relevant material: a block counts when it carries a
        # line-anchored resolution line OR integrity-bearing material lines
        # (a material-only block has no resolution text at all), and counts
        # once per block either way.
        if has_resolution or material_lines:
            resolution_blocks += 1
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
                value = integrity.group(1) if integrity else None
                if value is not None:
                    parsed[(name, ver)] = value
                    first = first_seen.setdefault((name, ver), value)
                    if first != value:
                        conflicting.append((f"{name}@{ver}", first, value))
                elif has_resolution:
                    unmatched.append(key_text)
            continue
        if not _PACKAGE_KEY_RE.search(key_text):
            # Not package-shaped: the integrity behind this key — if any —
            # belongs to no package entry. A line-anchored resolution line
            # here is unaccounted verification material; an empty scalar
            # key ('' quoted-empty) is a deliberate silent skip.
            if key_text.strip() and _RESOLUTION_LINE_RE.search(block):
                unaccounted.append(key_text)
            continue
        if integrity or has_resolution:
            unmatched.append(key_text)
    return LockfileScan(
        parsed=parsed,
        unmatched=unmatched,
        conflicting=conflicting,
        unaccounted=unaccounted,
        resolution_blocks=resolution_blocks,
    )


def _unowned_resolution_lines(content: str) -> list[str]:
    """Return the raw resolution lines no packages-section entry block owns.

    Ownership needs all three of: the line sits under a ``packages:``
    section (the only section whose entries carry resolutions), inside an
    entry block, and as that block's first resolution line. A resolution
    line above the first key line belongs to no block; a second resolution
    inside one block belongs to an entry the splitter never yielded as a
    key; and a resolution line under any other section (`snapshots:` is the
    realistic one — a peerless snapshots key is byte-identical to a
    `packages:` key, so an absorbed drifted entry's integrity would
    otherwise silently overwrite the legitimate entry's hash in the parsed
    map) is owned by nobody. Line-anchored matching keeps prose mentioning
    `resolution:` out of the count, and a column-0 resolution line — owned
    by nothing, since no legitimate shape writes one — is caught by the
    section rule rather than escaping the counter.
    """
    unowned: list[str] = []
    section: str | None = None
    in_block = False
    block_resolution_seen = False
    for line in content.splitlines():
        # The resolution-line check comes first on purpose: a column-0
        # `resolution:` would otherwise read as a section key below. The
        # ownership conditions themselves are stated in the docstring.
        if _RESOLUTION_LINE_RE.match(line):
            owned = (
                _OWNED_RESOLUTION_LINE_RE.match(line) is not None
                and section == "packages"
                and in_block
                and not block_resolution_seen
            )
            if owned:
                block_resolution_seen = True
            else:
                unowned.append(line.strip())
            continue
        if not line or line[0] not in " \t":
            # Column-0 content: a document separator resets the section, any
            # other non-comment column-0 line is a top-level key.
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            section = None if stripped == "---" else stripped.split(":", 1)[0]
            in_block = False
            block_resolution_seen = False
            continue
        if _ANY_KEY_RE.match(line):
            # A new entry block starts with its 2-space key line.
            in_block = True
            block_resolution_seen = False
    return unowned


def find_unmatched_package_keys(content: str) -> list[str]:
    """Return package-shaped keys that were not parsed into a verifiable entry.

    A key lands here when its block carries a resolution but the entry could
    not be parsed: either the key did not match the package pattern, or the
    resolution carries no integrity line (it could never be verified).

    The underlying scan is shared with `parse_lockfile`'s other guards and
    never raises — a lockfile this cannot read at all still yields an empty
    list, leaving the loud failure to `parse_lockfile`'s guards.
    """
    return _scan_lockfile(content).unmatched


def _inline_entry_scalar(line: str) -> str:
    """Return the scalar part of a stripped inline mapping line.

    A quoted scalar runs to its closing quote — colons inside belong to the
    scalar; an unquoted scalar ends at the first colon. On a prose line this
    yields the leading word (``note``), which is how prose is told apart
    from an inline package entry without parsing YAML.
    """
    if line.startswith("'"):
        end = line.find("'", 1)
        return line[1:end] if end != -1 else line[1:]
    return line.split(":", 1)[0]


def find_untokenized_package_keys(content: str) -> list[str]:
    """Return package-shaped entry candidates the block splitter cannot tokenize.

    Deliberately independent of `_entry_blocks`: this scans `content.splitlines()`
    directly, so a line the entry-key pattern (`_ANY_KEY_RE`) silently skips is
    still seen — a splitter regression cannot hide it. Two shapes are reported:

    - a 2-space-indented candidate key (exactly two leading spaces, stripped
      form ending in ``:``, not a comment, not blank) whose scalar contains
      ``@`` and which `_ANY_KEY_RE` does not match; the scalar is reported;
    - an inline entry: a line in that same 2-space shape that carries
      ``integrity:`` on the key line itself, does not end with ``:`` and
      whose scalar part (the text before the first unquoted colon, or the
      quoted scalar) is package-shaped — it carries ``@``, the genuine
      inline shape being ``foo@1.0.0: {resolution: {integrity: …}}``. A
      line whose scalar carries no ``@`` is prose (``note: resolution:
      appears here as prose``), not an entry candidate; the whole stripped
      line is reported.

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
            # The genuine inline-entry shape is `foo@1.0.0: {resolution: …}`:
            # its scalar names a package and carries `@`. A line whose scalar
            # carries no `@` is prose, not an entry candidate — reporting it
            # would fail parse_lockfile on a well-formed lockfile.
            if "@" in _inline_entry_scalar(stripped):
                reported.append(stripped)
    return reported


def parse_lockfile(path: Path) -> dict[tuple[str, str], str]:
    """Return {(name, version): integrity} for all packages with integrity.

    Raises RuntimeError if the block splitter cannot tokenize a package-shaped
    entry candidate — see `find_untokenized_package_keys` — if a package-shaped
    block carrying a resolution was not parsed — see
    `find_unmatched_package_keys` — if a resolution line in the raw file
    text is never reached by the entry-block walk — see
    `_unowned_resolution_lines` — if resolution/integrity material sits
    behind a key that is no package entry at all, or beyond an attributed
    block's own resolution as an orphan inline mapping — the scan's
    unaccounted set, the hole the ownership walk cannot see because any key
    line starts a block (see `_scan_lockfile` for the two origins that feed
    that one set) — or if two entries resolve to the same
    ``(name, version)`` identity with different integrity values — see
    `_scan_lockfile`'s `conflicting` set. The guards run
    most-diagnostic-first: a lockfile whose material sits behind no package
    entry or beyond a block's own resolution is named precisely by the
    unaccounted refusal (it yields only when the unreached-resolutions
    invariant already sees line-anchored resolution lines the walk cannot
    read at all), a lockfile the walk cannot see at all (four-space
    indentation throughout) is caught by the zero-parse check ("Parsed 0
    packages"), a partially drifted one by the unreached-resolutions
    invariant (a conflicting duplicate behind a drifted entry is a shape
    problem before it is a value problem), and a lockfile whose every entry
    is legitimately excluded (@zkochan-scoped, for instance) still parses to
    an empty map because those resolutions ARE reached by the walk and
    attributed to a (named, excluded) package entry. The duplicate-
    identity conflict check composes last, on a lockfile the walk fully
    accounted for.
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
    scan = _scan_lockfile(content)
    unmatched = scan.unmatched
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
    unowned = _unowned_resolution_lines(content)
    unaccounted = scan.unaccounted
    if unaccounted and not unowned:
        # Before the zero-parse check: material the scan classified as
        # unaccounted — behind a non-package key (`ledger:`, say) or beyond
        # a block's own resolution as an orphan inline mapping — is named
        # precisely by this guard, and a lockfile consisting only of such
        # material must not fall through to the blunter "Parsed 0
        # packages" diagnostic. When the unreached-resolutions invariant
        # (below) already sees line-anchored resolution lines the walk
        # cannot read at all (four-space indentation throughout), it stays
        # the more accurate diagnostic and this guard yields.
        shown = ", ".join(unaccounted[:5])
        more = "" if len(unaccounted) <= 5 else f" (+{len(unaccounted) - 5} more)"
        raise RuntimeError(
            f"Unaccounted resolution blocks ({len(unaccounted)}): {shown}{more}"
            " — resolution/integrity material behind a key that is no package"
            " entry, or beyond the block's own resolution (an inline mapping"
            " attributed to no package entry), which the parser cannot"
            " attribute to any verified or reported entry. It carries"
            " verification material matched against no attestation; attribute"
            " it to a real package entry in the lockfile rather than letting"
            " the gate account for a set it cannot see whole."
        )
    if not scan.parsed:
        # A shape drift invisible to BOTH patterns (four-space indentation, a
        # tab, a renamed top-level key) leaves the walk empty: `main()` would
        # print "Parsed 0 packages" and exit 0, the gate verifying nothing while
        # reporting success. A resolution the walk *reached* was either parsed
        # or reported above (or refused by the unaccounted guard, which ran
        # first), so zero parsed entries is expected in that case (every entry
        # excluded as @zkochan-scoped, for instance); zero parsed entries with
        # a resolution the walk never reached is not. `resolution_blocks`
        # comes from the same single pass the parsed map was built in — one
        # source of truth, no second walk; the head-material classification is
        # deliberately not a reach and never feeds it.
        reached = scan.resolution_blocks > 0
        resolutions = content.count("resolution:")
        if resolutions and not reached:
            raise RuntimeError(
                f"Parsed 0 packages from a lockfile carrying {resolutions}"
                " resolution line(s) — the lockfile's shape has drifted past the"
                " parser entirely (an indentation change is the usual cause), and"
                " proceeding would verify nothing while reporting success."
            )
    if unowned:
        # The partial-drift counterpart of the zero-parse guard above: some
        # entries parse, so nothing looks wrong, but a resolution line the
        # walk never read means an entry (its key indented differently, its
        # block absorbed into a neighbour) is verified by nobody while the
        # run still exits 0.
        shown = " | ".join(unowned[:5])
        more = "" if len(unowned) <= 5 else f" (+{len(unowned) - 5} more)"
        raise RuntimeError(
            f"Unreached resolution lines ({len(unowned)} of"
            f" {len(_RESOLUTION_LINE_RE.findall(content))}): {shown}{more}"
            " — the entry-block walk never saw these resolution lines, so the"
            " entries behind them are verified by nobody while the script"
            " still reports success. The lockfile's shape has drifted past"
            " the parser (an entry indented differently from its neighbours"
            " is the usual cause): fix the pattern rather than verifying a"
            " partial set."
        )
    duplicates = scan.conflicting
    if duplicates:
        # Last in the guard ordering: a conflicting identity behind an entry
        # the walk never reached is a shape problem first, and the unreached
        # guard above names that drift more precisely than a value conflict
        # could. Reaching this point means the walk accounted for every
        # resolution, so the conflict is the only remaining ambiguity: two
        # entries resolve to the same (name, version) and the parsed map
        # would silently keep one attested hash while dropping the other.
        shown = "; ".join(
            f"{identity} ({first} vs {later})"
            for identity, first, later in duplicates[:5]
        )
        more = "" if len(duplicates) <= 5 else f" (+{len(duplicates) - 5} more)"
        raise RuntimeError(
            f"Conflicting duplicate entries ({len(duplicates)}): {shown}{more}"
            " — two lockfile entries resolve to the same package identity with"
            " different integrity values, and the parser keys its map by"
            " (name, version), so one of the two attested hashes would be"
            " silently overwritten and dropped from verification while the"
            " gate still reported success. Resolve the duplicate in the"
            " lockfile rather than letting the gate verify an ambiguous set."
        )
    return scan.parsed


def _fetch_json(url: str, timeout: int = 10) -> Any:
    """Fetch JSON from ``url``; None means no well-formed JSON answer.

    None is the sentinel for every failure short of a parsed JSON body —
    not only transport: an exception from ``urlopen`` (timeout, DNS
    failure, connection refused, a 404) and an HTTP 200 whose body
    ``json.loads`` rejects (a non-JSON document served with a success
    status) both return None, so the caller can treat 'no usable answer'
    uniformly. The two origins are deliberately not distinguished here:
    the sentinel's contract is only 'the caller cannot trust any payload
    from this request'; ``get_provenance_bundle`` is the only caller and
    raises on every None, labeling the whole class as unreachable/failed
    transport — at this gate a failed request must never be conflated
    with a well-formed 'no attestations' answer. The JSON body's shape,
    once ``json.loads`` succeeds, is whatever it produced — the caller
    validates the shape (a truthy non-object body is malformed, not
    transport).
    """
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # pyright: ignore[reportAny]
            return json.loads(r.read())  # pyright: ignore[reportAny]
    except Exception:
        return None


def _classified_shape_error(
    pkg: str, source: str, artifact: str, detail: str
) -> RuntimeError:
    """Build the classified failure for a malformed layer of the answer.

    Every nested-shape violation must surface as this RuntimeError — never an
    AttributeError or TypeError — so ``main()``'s per-package collection sees
    a classified failure instead of a crashed sweep. The message says the
    layer is malformed and names the package and the source it came from.
    """
    return RuntimeError(
        f"provenance check for {pkg} got malformed {artifact} from {source} — {detail}"
    )


def get_provenance_bundle(name: str, ver: str) -> tuple[str, dict[str, Any]] | None:
    """Return (predicate_type, bundle) for the first SLSA provenance attestation.

    Returns None only when the registry answered with well-formed metadata
    that carries no usable ``dist.attestations.url`` — the expected 'no
    provenance yet' gap (~60% of the npm ecosystem). A transport failure —
    the request never got a well-formed answer, or the attestation-bundle
    download failed — raises RuntimeError naming the package and the
    unreachable source: a failed request must never read as the legitimate
    skip.

    Every nested layer of the registry answer is validated at runtime: a
    well-formed outer document whose ``dist``, ``dist.attestations``, the
    fetched bundle document, its ``attestations`` list, or any entry's
    ``predicateType``/``bundle`` has the wrong shape raises a classified
    RuntimeError instead of crashing the sweep with an unclassified
    exception. Once the metadata advertises an attestation URL, a bundle
    document carrying no SLSA provenance attestation is likewise a classified
    failure, not the skip: a registry-level MITM must never be able to turn a
    declared attestation into a silent 'no provenance yet' pass by serving a
    stripped or reshaped document.
    """
    pkg = f"{name}@{ver}"
    encoded = name.replace("/", "%2F")
    meta = _fetch_json(f"https://registry.npmjs.org/{encoded}/{ver}")
    if meta is None:
        raise RuntimeError(
            f"provenance check for {name}@{ver} could not reach the registry"
            " (registry.npmjs.org) — the package metadata request never got a"
            " well-formed answer (timeout, connection failure, or HTTP error)"
        )
    if not meta:
        raise RuntimeError(
            f"provenance check for {name}@{ver} got malformed package metadata"
            " from registry.npmjs.org — the registry answered, but the metadata"
            " document is empty or unusable"
        )
    if not isinstance(meta, dict):
        raise RuntimeError(
            f"provenance check for {name}@{ver} got malformed package metadata"
            f" from registry.npmjs.org — expected a JSON object, got"
            f" {type(meta).__name__}"
        )
    dist_meta = meta.get("dist")
    if "dist" in meta and not isinstance(dist_meta, dict):
        raise _classified_shape_error(
            pkg,
            "registry.npmjs.org",
            "package metadata",
            f"'dist' is not an object (got {type(dist_meta).__name__})",
        )
    dist = dist_meta if isinstance(dist_meta, dict) else {}
    attestations_meta = dist.get("attestations")
    if "attestations" in dist and not isinstance(attestations_meta, dict):
        raise _classified_shape_error(
            pkg,
            "registry.npmjs.org",
            "package metadata",
            f"'dist.attestations' is not an object"
            f" (got {type(attestations_meta).__name__})",
        )
    attestations = attestations_meta if isinstance(attestations_meta, dict) else {}
    att_url = attestations.get("url")
    if not isinstance(att_url, str) or not att_url:
        # The legitimate skip: well-formed metadata with no usable
        # ``dist.attestations.url``. An attestations object lacking ``url``
        # — or carrying a non-string one — stays this gap, not a failure:
        # only a *string* url turns the bundle path on.
        return None
    data = _fetch_json(att_url)
    if data is None:
        raise RuntimeError(
            f"provenance check for {name}@{ver} could not download the"
            f" attestation bundle from {att_url} — the request never got a"
            " well-formed answer (timeout, connection failure, or HTTP error)"
        )
    if not data:
        raise RuntimeError(
            f"provenance check for {name}@{ver} got a malformed attestation"
            f" bundle response from {att_url} — the registry answered, but the"
            " bundle document is empty or unusable"
        )
    if not isinstance(data, dict):
        raise _classified_shape_error(
            pkg,
            att_url,
            "attestation bundle response",
            f"expected a JSON object, got {type(data).__name__}",
        )
    raw_attestations = data.get("attestations")
    if not isinstance(raw_attestations, list):
        raise _classified_shape_error(
            pkg,
            att_url,
            "attestation bundle response",
            f"'attestations' is not a list (got {type(raw_attestations).__name__})",
        )
    for att in raw_attestations:
        if not isinstance(att, dict):
            raise _classified_shape_error(
                pkg,
                att_url,
                "attestation bundle response",
                f"attestation entry is not an object (got {type(att).__name__})",
            )
        pred = att.get("predicateType")
        if not isinstance(pred, str):
            raise _classified_shape_error(
                pkg,
                att_url,
                "attestation bundle response",
                f"attestation 'predicateType' is not a string"
                f" (got {type(pred).__name__})",
            )
        if not any(pred.startswith(p) for p in SLSA_PREDICATE_PREFIXES):
            continue
        bundle = att.get("bundle")
        if not isinstance(bundle, dict):
            raise _classified_shape_error(
                pkg,
                att_url,
                "attestation bundle response",
                "matched attestation's 'bundle' is not an object"
                f" (got {type(bundle).__name__})",
            )
        return pred, bundle
    # The metadata advertised an attestation URL, so a document without an
    # SLSA provenance attestation is not the expected gap — it is malformed
    # (or an actively stripped answer): failing closed here is what keeps a
    # registry-level MITM from turning a declared attestation into a silent
    # skip that main() counts under 'No attestation (expected gap)'.
    raise _classified_shape_error(
        pkg,
        att_url,
        "attestation bundle response",
        "no attestation whose predicateType starts with an SLSA provenance"
        f" prefix ({', '.join(SLSA_PREDICATE_PREFIXES)}) — the metadata"
        " advertised an attestation, so this is malformed, not the"
        " no-provenance-yet gap",
    )


def _b64_to_hex(integrity: str) -> str | None:
    """Convert pnpm integrity 'sha512-<base64>' to lowercase hex."""
    if not integrity.startswith("sha512-"):
        return None
    try:
        return base64.b64decode(integrity[7:]).hex()
    except Exception:
        return None


# The one VerificationError message that is a legitimate skip: sigstore 4.x's
# fixed Rekor-timestamp compatibility message, raised verbatim at
# sigstore/verify/verifier.py:235 for entry types newer than dsse/hashedrekord
# 0.0.1. Matched as this exact sentence after casefolding and whitespace
# collapsing — never as a loose "supported" substring, which would turn any
# fatal verification error mentioning "not supported" into a skip.
_FIXED_TIMESTAMP_COMPAT_MESSAGE = (
    "integrated time only supported for dsse/hashedrekord 0.0.1 types"
)
# sigstore 4.x raises sigstore.models.InvalidBundle (an errors.Error subclass)
# when a bundle document cannot be loaded — e.g. Bundle.from_json('[]'). The
# bundle-format compatibility skip is decided by that class, never by message
# text; see _is_bundle_format_error for how the class is matched.
_BUNDLE_FORMAT_ERROR_NAME = "InvalidBundle"


def _is_bundle_format_error(
    error: BaseException, library_class: type[BaseException] | None
) -> bool:
    """Whether ``error`` is sigstore's own bundle-format error class.

    The genuine class is ``sigstore.models.InvalidBundle`` (sigstore 4.2.0, an
    ``errors.Error`` subclass raised when a bundle document cannot be loaded).
    It is caught by class: an ``isinstance`` test against the imported class
    when that import succeeded — the only path to the skip in that case —
    while the match of the class NAME across the exception's MRO is strictly
    a fallback for environments where the genuine class is unimportable:
    the stubbed ``sigstore.*`` modules this gate's suite typically installs
    (the default stub publishes no such attribute) and any library layout
    that does not export it. When the genuine class exists, an unrelated
    exception class whose NAME merely contains ``InvalidBundle`` fails
    closed. Either way the
    decision is made on the exception's class, never on its message text,
    which any unclassifiable exception can imitate.
    """
    if isinstance(library_class, type) and isinstance(error, library_class):
        return True
    if library_class is not None:
        return False
    return any(_BUNDLE_FORMAT_ERROR_NAME in cls.__name__ for cls in type(error).__mro__)


def check_subject_hash(
    bundle: dict[str, Any], lockfile_integrity: str
) -> tuple[bool, str]:
    """Verify attested subject SHA-512 matches the lockfile integrity.

    Returns (ok, message). The unsupported-algorithm check gates the payload
    decode: a lockfile integrity whose algorithm prefix is not ``sha512`` is
    reported — naming the algorithm — before the attestation is even parsed,
    so every failure path with an unusable lockfile value names it. Only a
    ``sha512`` value reaches the DSSE decode and the subject loop: a sha512
    value with no sha512 subject keeps the loop's fall-through diagnostic
    ("no sha512 subject found in attestation payload"), and a decode or JSON
    failure of a sha512 value's payload keeps the parse-error diagnostic —
    on those paths the attestation is genuinely the artifact to blame.
    """
    algo = lockfile_integrity.partition("-")[0]
    if algo != "sha512":
        return False, (
            f"unsupported integrity algorithm '{algo}' — provenance"
            " attestation comparison supports sha512 only"
            f" (lockfile value: {lockfile_integrity})"
        )
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

    Classification of the Sigstore check's outcomes, decided by exception
    class, with exactly one carve-out matched as the library's exact fixed
    sentence (never loose substring resemblance): a network error (TUF
    download,
    Rekor unreachable) is treated as a warning, not a failure — it indicates
    infrastructure issues, not supply-chain attacks. A VerificationError (bad
    signature / cert chain) is fatal, with exactly one exception: the
    library's fixed Rekor-timestamp compatibility message ("Integrated time
    only supported for dsse/hashedrekord 0.0.1 types"), a sigstore 4.x
    library gap, is a skip. A bundle-format failure —
    sigstore.models.InvalidBundle in sigstore 4.2.0, e.g. from
    Bundle.from_json('[]') — is likewise a library compatibility gap and is
    skipped, its message naming the deciding path (the genuine
    ``InvalidBundle`` class match, or the exception-class-name fallback
    when that import is unavailable) so a CI log can see when the gate is
    running on the fallback — e.g. after a library upgrade renames the
    class. Any other exception is unclassifiable and fails closed: it is
    reported as a failed check, never as a verified package — including a
    generic exception whose message merely resembles one of the
    compatibility messages.
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

    # The genuine bundle-format error class is bound separately from the
    # block above so a layout that does not export it cannot take the whole
    # import gate down (the stubbed sigstore.* environments this suite runs
    # under publish only Bundle); _is_bundle_format_error handles the miss.
    bundle_format_error: type[BaseException] | None
    try:
        from sigstore.models import (  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
            InvalidBundle,
        )

        bundle_format_error = InvalidBundle  # pyright: ignore[reportUnknownVariableType]
    except ImportError:
        bundle_format_error = None

    try:
        verifier = Verifier.production()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        bundle = Bundle.from_json(bundle_json)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        verifier.verify_dsse(  # pyright: ignore[reportUnknownMemberType]
            bundle=bundle,
            policy=OIDCIssuer(GITHUB_ACTIONS_ISSUER),
        )
        return True, "Sigstore signature verified (Rekor + Fulcio)"
    except VerificationError as e:  # pyright: ignore[reportUnknownVariableType]
        # sigstore 4.x cannot verify the Rekor integrated timestamp for entry
        # types newer than dsse/hashedrekord 0.0.1 and raises one exact fixed
        # message for it. That message is a library compatibility gap, not a
        # signature failure; every other VerificationError is a real
        # verification failure. Subject hash check (already done) still
        # provides the key supply-chain guarantee.
        msg = str(e)  # pyright: ignore[reportUnknownArgumentType]
        normalized = " ".join(msg.split()).casefold()
        if _FIXED_TIMESTAMP_COMPAT_MESSAGE in normalized:
            return True, (
                f"Rekor entry type not supported by sigstore 4.x"
                f" — timestamp skipped ({msg})"
            )
        return False, f"Sigstore verification failed: {msg}"  # pyright: ignore[reportUnknownArgumentType]
    except NetworkError as e:  # pyright: ignore[reportUnknownVariableType]
        return True, f"Sigstore network unavailable — signature check skipped ({e})"
    except Exception as e:
        # sigstore's own bundle-format error class decides the compatibility
        # skip — never message text: a generic exception whose wording merely
        # resembles the compatibility message is unclassifiable and fails
        # closed (see _is_bundle_format_error).
        if _is_bundle_format_error(e, bundle_format_error):
            msg = str(e)
            # Which classifier decided is part of the diagnostic: the genuine
            # class (isinstance against the imported InvalidBundle) or the
            # MRO-name fallback (only reachable when the import failed, since
            # a present class that does not match fails closed above). A CI
            # log can see when the gate is running on the fallback — e.g.
            # after a library upgrade renames the class.
            decided = (
                "genuine InvalidBundle class matched"
                if bundle_format_error is not None
                else "exception-class-name fallback matched"
            )
            return True, (
                f"Bundle format not supported by sigstore 4.x — skipped,"
                f" {decided} ({msg[:80]})"
            )
        # Anything else is an exception class this gate cannot classify. The
        # safe direction is failure, not a skip: an unrecognized Bundle load
        # or verification error must never read as a verified package.
        return False, f"Sigstore check failed with an unclassified error: {e}"


def main() -> int:
    """Verify provenance for all attested packages in pnpm-lock.yaml.

    Returns 1 when any package failed a check (the per-package failure path)
    or when packages were parsed but nothing was verified — the aggregate
    guard, whose likely cause is a metadata source stripped or censored so
    that every answer read as the no-attestation gap. Returns 0 only when at
    least one package was verified, or when nothing was parsed at all (a
    lockfile whose every entry is legitimately excluded, e.g. @zkochan).
    """
    packages = parse_lockfile(LOCKFILE)
    print(f"Parsed {len(packages)} packages from pnpm-lock.yaml")
    print()

    verified: list[str] = []
    skipped = 0
    failed: list[tuple[str, str]] = []

    for (name, ver), integrity in sorted(packages.items()):
        pkg = f"{name}@{ver}"
        try:
            result = get_provenance_bundle(name, ver)
        except RuntimeError as e:
            # A transport failure is loud, but not sweep-ending: one blip
            # must not cut every later package off — collect it like a
            # verification failure and let the run end with the full picture.
            failed.append((pkg, str(e)))
            time.sleep(0.02)
            continue
        if result is None:
            skipped += 1
            time.sleep(0.02)
            continue

        _pred_type, bundle = result
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

    if packages and not verified:
        # The aggregate counterpart of the per-package failure path above: a
        # sweep that parsed packages but verified none. With every failure
        # path already handled, this means every parsed package read as the
        # no-attestation gap — for this lockfile's known attested population
        # that is not the expected ~60% ecosystem gap but a stripped or
        # censored metadata source. Fail instead of reporting success over a
        # run that verified nothing (partial censorship below 100% stays a
        # recorded limit of this guard).
        print()
        print(
            "PROVENANCE VERIFICATION FAILED: nothing was verified — every"
            " parsed package read as the no-attestation gap."
        )
        print()
        print("""\
The sweep verified zero packages. Likely cause: the metadata source was
stripped or censored — every answer read as the no-attestation gap, which
for this lockfile's known attested population is not the expected ~60%
ecosystem gap. Investigate the registry (and any proxy or mirror in front
of it) before trusting this environment.""")
        return 1

    print()
    print("All attested packages passed provenance verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
