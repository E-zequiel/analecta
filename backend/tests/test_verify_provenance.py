"""Tests for scripts/verify-provenance.py's lockfile parser.

The script's filename contains a hyphen, so it cannot be imported by name; the
`vp` fixture loads it from its path. The parser and the pure comparison helpers
are exercised here — the script's network sweep (registry + Sigstore round trips
per package) is out of scope.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify-provenance.py"


@pytest.fixture(scope="module")
def vp() -> Any:
    """Load scripts/verify-provenance.py without touching global import state."""
    spec = importlib.util.spec_from_file_location("verify_provenance", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_lockfile(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pnpm-lock.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("@sveltejs/kit", "2.70.3"),
        ("@codemirror/state", "6.7.0"),
        ("devalue", "5.9.2"),
    ],
)
def test_parses_quoted_entries(
    vp: Any, tmp_path: Path, name: str, version: str
) -> None:
    """Quoted entries parse for both scoped and unscoped names."""
    path = _write_lockfile(
        tmp_path,
        f"packages:\n\n  '{name}@{version}':\n"
        "    resolution: {integrity: sha512-AAAABBBBCCCC==}\n",
    )
    assert vp.parse_lockfile(path) == {(name, version): "sha512-AAAABBBBCCCC=="}


def test_parses_scoped_entry_from_env_document(vp: Any, tmp_path: Path) -> None:
    """An entry in the leading env document is seen (two-document lockfile)."""
    path = _write_lockfile(
        tmp_path,
        "---\n"
        "lockfileVersion: '9.0'\n\n"
        "packages:\n\n"
        "  '@pnpm/exe.darwin-arm64@12.4.2':\n"
        "    resolution: {integrity: sha512-ENVENVENV==}\n\n"
        "---\n"
        "lockfileVersion: '9.0'\n\n"
        "packages:\n\n"
        "  '@sveltejs/kit@2.70.3':\n"
        "    resolution: {integrity: sha512-PROJPROJ==}\n",
    )
    assert vp.parse_lockfile(path) == {
        ("@pnpm/exe.darwin-arm64", "12.4.2"): "sha512-ENVENVENV==",
        ("@sveltejs/kit", "2.70.3"): "sha512-PROJPROJ==",
    }


def test_resolution_without_integrity_is_reported_as_a_parser_gap(
    vp: Any, tmp_path: Path
) -> None:
    """A resolution with no integrity can never be verified — report it.

    Formerly `test_skips_entry_without_integrity`, which pinned the old silent
    skip: a package-shaped block whose body carries a `resolution:` but no
    `integrity:` (the `resolution: {}` form included) was dropped without a
    word. The documented `_scan_lockfile` contract is that such a block is
    either parsed or reported as a parser gap — a resolution without an
    integrity can never be verified, so silence would hide the package while
    the script still reported success.
    """
    body = "packages:\n\n  'no-integrity@1.0.0':\n    resolution: {}\n"
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == ["no-integrity@1.0.0"]
    with pytest.raises(RuntimeError, match="did not match the lockfile parser"):
        vp.parse_lockfile(path)


def test_non_key_line_ending_in_colon_does_not_split_the_block(
    vp: Any, tmp_path: Path
) -> None:
    """Only a *key* line may start a block — prose ending in `:` must not.

    Regression test for the widened entry-key class: allowing `:` anywhere in
    the unquoted scalar made a non-key line like `  note: this is prose:` a
    block start, cutting the preceding entry's block short so the entry landed
    in neither the parsed nor the reported set. The unquoted alternative must
    keep excluding `:` (only a quoted scalar may contain one).
    """
    body = (
        "packages:\n\n"
        "  'foo@1.0.0':\n"
        "  note: this is prose:\n"
        "    resolution: {integrity: sha512-AAAA==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.find_untokenized_package_keys(body) == []
    assert vp.parse_lockfile(path) == {("foo", "1.0.0"): "sha512-AAAA=="}


def test_untokenized_package_key_fails_loudly(vp: Any, tmp_path: Path) -> None:
    """An untokenized package-shaped key is reported, never ignored.

    The block splitter and the package pattern are two different primitives: a
    key line neither can handle used to be invisible to both the parsed and the
    reported set at once. `find_untokenized_package_keys` scans raw lines,
    independent of the splitter, and `parse_lockfile` refuses to proceed when
    it is non-empty. The inline-entry shape (integrity on the key line itself)
    is covered by the same guard and asserted here too.
    """
    body = "packages:\n\n  foo:bar@1.0.0:\n    resolution: {integrity: sha512-AAAA==}\n"
    path = _write_lockfile(tmp_path, body)
    assert vp.find_untokenized_package_keys(body) == ["foo:bar@1.0.0"]
    with pytest.raises(RuntimeError, match="cannot tokenize"):
        vp.parse_lockfile(path)

    inline = "packages:\n\n  foo@1.0.0: {resolution: {integrity: sha512-BBBB==}}\n"
    assert vp.find_untokenized_package_keys(inline) == [
        "foo@1.0.0: {resolution: {integrity: sha512-BBBB==}}"
    ]


def test_empty_quoted_key_fails_cleanly_instead_of_crashing(
    vp: Any, tmp_path: Path
) -> None:
    """A malformed empty scalar is a gap, never a TypeError.

    The quoted alternative of the key pattern can match an empty scalar; the
    key-text extraction must treat that as `""` rather than falling through to
    the (absent) unquoted group and handing `None` to the package-shape search,
    which crashes instead of failing cleanly.
    """
    body = "packages:\n\n  '':\n    resolution: {integrity: sha512-AAAA==}\n"
    path = _write_lockfile(tmp_path, body)
    assert vp.find_untokenized_package_keys(body) == []
    assert vp.find_unmatched_package_keys(body) == []
    # An empty key is not package-shaped, so there is nothing to report — and
    # crucially nothing to crash on: the block is read and skipped, not blown up.
    assert vp.parse_lockfile(path) == {}


def test_indentation_drift_fails_loudly_instead_of_parsing_nothing(
    vp: Any, tmp_path: Path
) -> None:
    """A lockfile the parser cannot see at all must not report success.

    Four-space indentation is invisible to the block splitter *and* to the
    package pattern, so every pass returns empty and `main()` would print
    "Parsed 0 packages", verify nothing and exit 0. The zero-parse check turns
    that into a loud failure.
    """
    body = (
        "packages:\n\n"
        "    'foo@1.0.0':\n"
        "        resolution: {integrity: sha512-AAAA==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.find_untokenized_package_keys(body) == []
    with pytest.raises(RuntimeError, match="Parsed 0 packages"):
        vp.parse_lockfile(path)


def test_excludes_zkochan_entries(vp: Any, tmp_path: Path) -> None:
    path = _write_lockfile(
        tmp_path,
        "packages:\n\n  '@zkochan/internal@1.0.0':\n"
        "    resolution: {integrity: sha512-ZKOCHAN==}\n",
    )
    assert vp.parse_lockfile(path) == {}


def test_peer_suffixed_key_with_integrity_fails_loudly(vp: Any, tmp_path: Path) -> None:
    """A peer-suffixed key carrying a resolution is a parser gap, not a skip.

    pnpm writes peer suffixes on `snapshots:` keys (`'@keyv/bigmap@1.3.1(keyv@5.6.0)'`),
    which carry no integrity and are legitimately unparsed. If such a key ever
    carries an integrity line, the parser must report it instead of silently
    dropping it or swallowing the suffix into the name/version.
    """
    body = (
        "packages:\n\n"
        "  '@keyv/bigmap@1.3.1(keyv@5.6.0)':\n"
        "    resolution: {integrity: sha512-AAAA==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == ["@keyv/bigmap@1.3.1(keyv@5.6.0)"]
    with pytest.raises(RuntimeError, match="did not match the lockfile parser"):
        vp.parse_lockfile(path)


def test_peer_suffixed_key_without_integrity_is_skipped(
    vp: Any, tmp_path: Path
) -> None:
    """Snapshots-style keys are unparsed without raising — no false alarm."""
    body = (
        "snapshots:\n\n"
        "  '@keyv/bigmap@1.3.1(keyv@5.6.0)':\n"
        "    dependencies:\n"
        "      keyv: 5.6.0\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.parse_lockfile(path) == {}


def test_parenthesised_key_with_integrity_fails_loudly(vp: Any, tmp_path: Path) -> None:
    """A name containing a parenthesis must not be parsed as a truncated name."""
    body = (
        "packages:\n\n"
        "  'weird(thing)@1.0.0':\n"
        "    resolution: {integrity: sha512-BBBB==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == ["weird(thing)@1.0.0"]
    with pytest.raises(RuntimeError, match="did not match the lockfile parser"):
        vp.parse_lockfile(path)


def test_peer_suffixed_key_adjacent_to_a_resolved_entry_is_not_reported(
    vp: Any, tmp_path: Path
) -> None:
    """A key is matched with its own block, never with its neighbour's.

    Regression test for the fixed-distance window this guard used to rely on: a
    peer-suffixed `snapshots:` key immediately followed by an entry carrying an
    integrity line was falsely reported, which would have failed CI on a valid
    lockfile.
    """
    body = (
        "snapshots:\n\n"
        "  '@keyv/bigmap@1.3.1(keyv@5.6.0)':\n"
        "    dependencies:\n"
        "      keyv: 5.6.0\n\n"
        "packages:\n\n"
        "  '@sveltejs/kit@2.70.3':\n"
        "    resolution: {integrity: sha512-PROJ==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.parse_lockfile(path) == {("@sveltejs/kit", "2.70.3"): "sha512-PROJ=="}


def test_resolution_block_longer_than_the_old_window_is_parsed(
    vp: Any, tmp_path: Path
) -> None:
    """An entry's resolution is found wherever it sits in that entry's block.

    Regression test for the other half of the fixed-window problem: a resolution
    more than 300 characters below its key used to be dropped silently.
    """
    body = (
        "packages:\n\n"
        "  '@sveltejs/kit@2.70.3':\n"
        "    peerDependencies:\n"
        + "".join(f"      dep{index}: 1.0.0\n" for index in range(30))
        + "    resolution: {integrity: sha512-LONGBLOCK==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.parse_lockfile(path) == {
        ("@sveltejs/kit", "2.70.3"): "sha512-LONGBLOCK=="
    }


def test_repo_lockfile_has_no_unparsed_package_entries(vp: Any) -> None:
    """Structural invariant on the real lockfile: the parser covers it.

    This is the guard the review asked for: if pnpm's lockfile shape drifts and
    the pattern stops matching package entries that carry a resolution, this
    fails here rather than silently under-reporting in CI.
    """
    assert vp.find_unmatched_package_keys(vp.LOCKFILE.read_text()) == []
    # Every guard the parser relies on must be empty on the real file — the
    # splitter-independent one included, which is asserted nowhere else.
    content = vp.LOCKFILE.read_text()
    assert vp.find_untokenized_package_keys(content) == []
    assert vp.parse_lockfile(vp.LOCKFILE)


def test_non_sha512_integrity_entry_is_parsed_or_reported(
    vp: Any, tmp_path: Path
) -> None:
    """An entry whose integrity is not sha512- is parsed or reported — never lost.

    The module's documented contract (`_scan_lockfile`'s docstring) is that a
    block whose key is package-shaped and whose body carries an integrity line
    is either parsed or reported as a parser gap. Whatever the parser thinks of
    a legacy `sha1-` value, silently skipping the entry would hide the package
    from provenance verification entirely while the script still reported
    success.
    """
    body = (
        "packages:\n\n"
        "  'legacy-pkg@1.0.0':\n"
        "    resolution: {integrity: sha1-AAAABBBB==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    unmatched = vp.find_unmatched_package_keys(body)
    if unmatched:
        assert "legacy-pkg@1.0.0" in unmatched
        with pytest.raises(RuntimeError, match="did not match the lockfile parser"):
            vp.parse_lockfile(path)
    else:
        assert ("legacy-pkg", "1.0.0") in vp.parse_lockfile(path)


def test_non_sha512_integrity_is_rejected_naming_the_algorithm(vp: Any) -> None:
    """A non-sha512 value parses, then fails with the algorithm named.

    Accepting the value at parse time is only half the contract: whatever the
    script does with an entry it cannot verify must be loud *and* accurate, not
    a misleading lockfile-format message. Assert the diagnostic, not just the
    failure.
    """
    payload = base64.b64encode(
        json.dumps({"subject": [{"digest": {"sha512": "ab" * 32}}]}).encode()
    ).decode()
    bundle = {"dsseEnvelope": {"payload": payload}}

    ok, message = vp.check_subject_hash(bundle, "sha1-AAAABBBB==")
    assert ok is False
    assert "unsupported integrity algorithm" in message
    assert "sha1" in message

    ok, message = vp.check_subject_hash(
        bundle, "sha512-" + base64.b64encode(bytes.fromhex("ab" * 32)).decode()
    )
    assert ok is True
    assert message == "subject hash matches lockfile integrity"


def test_quoted_key_with_colon_is_parsed_or_reported(vp: Any, tmp_path: Path) -> None:
    """A quoted key containing a colon is parsed or reported — never lost.

    The module's documented contract (`_scan_lockfile`'s docstring) is that a
    block whose key is package-shaped and whose body carries an integrity line
    is either parsed or reported as a parser gap. A key whose quoted scalar
    contains a colon must not make that coverage vanish silently, whatever the
    parser ultimately decides to do with the colon.
    """
    body = (
        "packages:\n\n"
        "  'weird:name@1.0.0':\n"
        "    resolution: {integrity: sha512-AAAABBBB==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    unmatched = vp.find_unmatched_package_keys(body)
    if unmatched:
        assert any("weird:name@1.0.0" in key for key in unmatched)
        with pytest.raises(RuntimeError, match="did not match the lockfile parser"):
            vp.parse_lockfile(path)
    else:
        assert ("weird:name", "1.0.0") in vp.parse_lockfile(path)


def test_repo_lockfile_yields_scoped_entries(vp: Any) -> None:
    """Regression guard: the parser covers the real lockfile — all of it.

    Before the fix the parser returned zero scoped entries while the lockfile
    carried hundreds — the script reported success over the unscoped subset only.
    Two independent anchors guard the coverage. The splitter-based check
    recomputes the expected count from `_entry_blocks` and the package-shape
    test; it catches regressions in the single parse pass (`_scan_lockfile`),
    but it is blind to a partial splitter regression because it uses the very
    primitive in question. The raw-text anchor therefore compares `len(parsed)`
    against a count of resolution-integrity lines taken directly from the file
    text, with no `_entry_blocks` involvement. That comparison is exact only
    while the lockfile has no @zkochan-scoped entries (they are excluded from
    `parsed` by design) and every integrity sits in a resolution line; the
    @zkochan guard below fails loudly — asking for this anchor to be updated —
    instead of silently comparing wrong numbers.
    """
    content = vp.LOCKFILE.read_text()
    if any(re.match(r"^  '?@zkochan/", line) for line in content.splitlines()):
        pytest.fail(
            "pnpm-lock.yaml now contains @zkochan-scoped entries: the raw-text"
            " anchor below assumes zero (they are excluded from `parsed` by"
            " design) — update this test's anchor before trusting its counts."
        )
    raw_integrity_lines = len(re.findall(r"resolution: \{integrity: sha512-", content))
    parsed, unmatched = vp._scan_lockfile(content)
    assert any(name.startswith("@") for name, _ in parsed)
    assert len(parsed) == raw_integrity_lines
    expected = 0
    for _key_line, key_text, block in vp._entry_blocks(content):
        if "integrity:" not in block and "resolution:" not in block:
            continue
        if "@" in key_text and not key_text.startswith("@zkochan/"):
            expected += 1
    assert len(parsed) + len(unmatched) == expected
