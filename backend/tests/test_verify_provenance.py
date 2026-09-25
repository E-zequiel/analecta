"""Tests for scripts/verify_provenance.py's lockfile parser.

The script's filename contains a hyphen, so it cannot be imported by name; the
`vp` fixture loads it from its path. The parser and the pure comparison helpers
are exercised here, along with the sweep loop's failure classification and
transport handling — all with mocked network responses; the script's real
registry round trips (one per package) remain out of scope.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_provenance.py"


@pytest.fixture(scope="module")
def vp() -> Any:
    """Load scripts/verify_provenance.py without touching global import state."""
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
    scan = vp._scan_lockfile(content)
    parsed, unmatched = scan.parsed, scan.unmatched
    assert any(name.startswith("@") for name, _ in parsed)
    assert len(parsed) == raw_integrity_lines
    expected = 0
    for _key_line, key_text, block in vp._entry_blocks(content):
        if "integrity:" not in block and "resolution:" not in block:
            continue
        if "@" in key_text and not key_text.startswith("@zkochan/"):
            expected += 1
    assert len(parsed) + len(unmatched) == expected


def test_partially_drifted_lockfile_fails_loudly(vp: Any, tmp_path: Path) -> None:
    """A partially drifted lockfile must never shrink the verified set silently.

    An entry indented at 4/8 spaces is invisible to the block splitter and to
    the package pattern at once: both guard sets come back empty, the two
    well-indented neighbours parse, and the drifted entry — resolution line
    and all — is verified by nobody while the run still exits 0. A lockfile
    whose text carries a resolution line no parsed entry accounts for must
    fail loudly rather than verify a partial set.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOODGOOD==}\n\n"
        "    'drifted@1.0.0':\n"
        "        resolution: {integrity: sha512-DRIFTED==}\n\n"
        "  'good2@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD2==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    with pytest.raises(RuntimeError):
        vp.parse_lockfile(path)


def test_fully_indented_lockfile_with_prose_notes_still_parses(
    vp: Any, tmp_path: Path
) -> None:
    """A well-formed lockfile with prose notes inside entry blocks parses fine.

    Companion to the loud-failure guard for drifted lockfiles: a prose comment
    inside a well-indented entry block — even one mentioning `resolution:` —
    is not an entry, every resolution in the file is reached by the block
    walk, and parsing must succeed with both entries present.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    # note: resolution: appears here as prose\n"
        "    resolution: {integrity: sha512-GOOD==}\n\n"
        "  'good2@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD2==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.parse_lockfile(path) == {
        ("good", "1.0.0"): "sha512-GOOD==",
        ("good2", "1.0.0"): "sha512-GOOD2==",
    }


def test_all_zkochan_lockfile_with_resolutions_still_parses(
    vp: Any, tmp_path: Path
) -> None:
    """A lockfile whose every entry is @zkochan-scoped parses to an empty map.

    parse_lockfile legitimately returns {} when every entry is excluded as
    @zkochan-scoped. Those entries' resolutions are reached by the block
    walk, so the loud-failure path for unreached resolutions must not fire on
    this shape.
    """
    body = (
        "packages:\n\n"
        "  '@zkochan/internal@1.0.0':\n"
        "    resolution: {integrity: sha512-ZKOCHAN==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.parse_lockfile(path) == {}


def test_unclassifiable_sigstore_exception_fails_closed(vp: Any, mocker) -> None:
    """An exception class the script cannot classify must not verify as ok.

    The blanket exception handler in the Sigstore verification path reports
    every unrecognized exception as a skippable hiccup and returns ok=True —
    a Bundle load failure of unknown origin becomes indistinguishable from a
    network blip. An unclassifiable exception must produce a fail-closed
    verdict, never a verified/skipped-ok one.
    """
    errors_mod = types.ModuleType("sigstore.errors")
    errors_mod.NetworkError = type("NetworkError", (Exception,), {})  # pyright: ignore[reportAttributeAccessIssue]
    errors_mod.VerificationError = type(  # pyright: ignore[reportAttributeAccessIssue]
        "VerificationError", (Exception,), {}
    )

    class _StubBundle:
        @classmethod
        def from_json(cls, raw: str) -> None:
            raise RuntimeError("unexpected internal")

    models_mod = types.ModuleType("sigstore.models")
    models_mod.Bundle = _StubBundle  # pyright: ignore[reportAttributeAccessIssue]
    verify_mod = types.ModuleType("sigstore.verify")
    verify_mod.Verifier = type(  # pyright: ignore[reportAttributeAccessIssue]
        "Verifier", (), {"production": staticmethod(lambda: object())}
    )
    policy_mod = types.ModuleType("sigstore.verify.policy")
    policy_mod.OIDCIssuer = type("OIDCIssuer", (), {})  # pyright: ignore[reportAttributeAccessIssue]
    sigstore_mod = types.ModuleType("sigstore")
    mocker.patch.dict(
        sys.modules,
        {
            "sigstore": sigstore_mod,
            "sigstore.errors": errors_mod,
            "sigstore.models": models_mod,
            "sigstore.verify": verify_mod,
            "sigstore.verify.policy": policy_mod,
        },
    )
    ok, _message = vp.verify_sigstore("{}")
    assert ok is False


def test_registry_transport_failure_is_loud_not_none(vp: Any, mocker) -> None:
    """A metadata transport failure must be loud, never read as no-attestation.

    A request that never got a well-formed answer (timeout, connection
    refused, an HTTP error) is a transport failure, not a package verdict:
    treating it as the ~60% no-attestation gap lets a sweep whose every
    request failed exit 0 having verified nothing. The transport sentinel
    must surface as an error naming the package and the unreachable source,
    while a well-formed answer without attestations stays the legitimate
    skip.
    """
    mocker.patch.object(vp, "_fetch_json", return_value=None)
    with pytest.raises(
        RuntimeError,
        match=r"provenance check for suspicious-package@1\.0\.0"
        r" could not reach the registry",
    ):
        vp.get_provenance_bundle("suspicious-package", "1.0.0")


def test_malformed_empty_metadata_is_loud_with_an_accurate_label(
    vp: Any, mocker
) -> None:
    """A well-formed but unusable registry answer is malformed, not unreachable.

    The transport sentinel is ``None`` (the request never got a well-formed
    answer). A well-formed HTTP response whose JSON body is not a package
    metadata object — an empty document, a list, a string, a number — is a
    different failure: the registry answered, so reporting it as "could not
    reach the registry" mislabels it, and a truthy non-object body must not
    crash the sweep with an unclassified exception.
    """
    for body_value in ({}, [1, 2], "abc", 42):
        mocker.patch.object(vp, "_fetch_json", return_value=body_value)
        with pytest.raises(RuntimeError, match=r"malformed package metadata"):
            vp.get_provenance_bundle("suspicious-package", "1.0.0")


def test_col0_resolution_inside_a_packages_block_fails_loudly(
    vp: Any, tmp_path: Path
) -> None:
    """A resolution line at column 0 can never belong to an entry block.

    Ownership tracks the block through the splitter's 2-space keys, but a
    column-0 resolution line is no legitimate entry's resolution: as the
    block's only resolution it is silently parsed with the drifted hash
    (the legitimate integrity vanishes from the map entirely), and with a
    legitimate line also present the col-0 line is owned while the real one
    is flagged unowned — loud, but naming the wrong line. Ownership must
    require block indentation: a column-0 resolution is unowned drift
    wherever it sits.
    """
    col0_only = (
        "packages:\n\n  'good@1.0.0':\nresolution: {integrity: sha512-COLZERO==}\n"
    )
    col0_first = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "resolution: {integrity: sha512-COLZERO==}\n"
        "    resolution: {integrity: sha512-GOOD==}\n"
    )
    for body, expected_total in ((col0_only, 1), (col0_first, 2)):
        path = _write_lockfile(tmp_path, body)
        with pytest.raises(
            RuntimeError, match=rf"Unreached resolution lines \(1 of {expected_total}\)"
        ):
            vp.parse_lockfile(path)


def test_transport_failures_are_collected_and_the_sweep_fails_at_the_end(
    vp: Any, tmp_path: Path, mocker, capsys
) -> None:
    """A mid-sweep transport failure must not cut the remaining packages off.

    Failing loudly is the transport contract, but aborting the sweep at the
    first failure leaves every later package unchecked and gives a single
    blip an outsize report: the run cannot say which other packages would
    have been fine. Collect the transport failures, keep sweeping, and fail
    at the end with the complete list — mirroring how verification failures
    are already reported.
    """
    body = (
        "packages:\n\n"
        "  'a@1.0.0':\n    resolution: {integrity: sha512-A==}\n\n"
        "  'b@1.0.0':\n    resolution: {integrity: sha512-B==}\n\n"
        "  'c@1.0.0':\n    resolution: {integrity: sha512-C==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    mocker.patch.object(vp, "LOCKFILE", path)
    results = [RuntimeError("transport down a"), RuntimeError("transport down"), None]

    def sweep(name: str, ver: str) -> tuple[str, dict[str, Any]] | None:
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    mocker.patch.object(vp, "get_provenance_bundle", side_effect=sweep)
    assert vp.main() == 1
    output = capsys.readouterr().out
    assert "a@1.0.0" in output
    assert "b@1.0.0" in output
    assert "Failed: 2" in output
    assert "No attestation (expected gap): 1" in output


def test_drift_absorbed_into_snapshot_blocks_fails_loudly(
    vp: Any, tmp_path: Path
) -> None:
    """A resolution line outside a `packages:` section can never verify silently.

    pnpm's peerless `snapshots:` keys are byte-identical to `packages:` keys,
    so a resolution line drifting into a snapshots block — with its key or
    without — is matched by the package pattern and its integrity silently
    overwrites the legitimate packages-section entry's hash in the parsed
    map: the gate would then compare the wrong package's attested hash, or
    exit 0 having never verified the drifted entry at all. Only a
    `packages:` section's entry blocks own resolution lines; anywhere else
    a resolution line is unowned and the parse must fail loudly.
    """
    drifted_entry = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD==}\n\n"
        "snapshots:\n\n"
        "  'good@1.0.0':\n"
        "    dependencies:\n"
        "      snap: 2.0.0\n\n"
        "    'drifted@1.0.0':\n"
        "        resolution: {integrity: sha512-DRIFT==}\n"
    )
    bare_resolution = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD==}\n\n"
        "snapshots:\n\n"
        "  'good@1.0.0':\n"
        "    dependencies:\n"
        "      snap: 2.0.0\n"
        "        resolution: {integrity: sha512-DRIFT==}\n"
    )
    for body in (drifted_entry, bare_resolution):
        path = _write_lockfile(tmp_path, body)
        with pytest.raises(RuntimeError, match=r"Unreached resolution lines"):
            vp.parse_lockfile(path)


def test_metadata_without_attestations_still_returns_none(vp: Any, mocker) -> None:
    """A well-formed registry answer without attestations is an expected skip.

    Metadata that parses cleanly but carries no `dist.attestations.url` is a
    legitimate 'no provenance yet' answer: get_provenance_bundle must keep
    returning None for it without raising. This is the case a transport
    failure must never be allowed to masquerade as.
    """
    metadata = {"dist": {"tarball": "https://registry.npmjs.org/x/-/x-1.0.0.tgz"}}
    mocker.patch.object(vp, "_fetch_json", return_value=metadata)
    assert vp.get_provenance_bundle("suspicious-package", "1.0.0") is None


def test_nested_metadata_shape_errors_are_classified_failures(vp: Any, mocker) -> None:
    """A well-formed answer with a wrong nested metadata shape fails classified.

    The outer layers are validated (`test_malformed_empty_metadata_is_loud_with_
    an_accurate_label`), but every nested layer of the metadata document must be
    too: a truthy object whose ``dist`` is not an object, or whose
    ``dist.attestations`` is not an object, crashes the sweep with an
    unclassified AttributeError that escapes ``main()``'s ``except RuntimeError``
    collection. A wrong nested shape is a classified failure — a RuntimeError
    whose message says the metadata is malformed and names the package and the
    source — never a crash and never the legitimate skip.
    """
    for metadata in (
        {"dist": None},
        {"dist": "abc"},
        {"dist": {"attestations": None}},
        {"dist": {"attestations": "no"}},
    ):
        mocker.patch.object(vp, "_fetch_json", return_value=metadata)
        with pytest.raises(RuntimeError, match=r"malformed") as excinfo:
            vp.get_provenance_bundle("suspicious-package", "1.0.0")
        message = str(excinfo.value)
        assert "suspicious-package@1.0.0" in message
        assert "registry.npmjs.org" in message


def test_advertised_url_with_unusable_bundle_document_fails_loudly(
    vp: Any, mocker
) -> None:
    """Once the metadata advertises an attestation URL, never read as a skip.

    The legitimate no-attestation skip (None) means 'well-formed metadata
    without ``dist.attestations.url``'. Once that URL is advertised, a bundle
    answer that carries no SLSA-provenance attestation is malformed or unusable
    — a truthy non-object document, an object with no attestations list, a
    non-list attestations value, a non-object entry, an entry without a string
    predicateType, or a list whose every entry is a non-SLSA predicate — and
    must fail loudly as a classified RuntimeError naming the package and the
    bundle source, never resolve silently to None (main() would count the
    package under 'No attestation (expected gap)' and exit 0).
    """
    metadata = {"dist": {"attestations": {"url": "https://bundler.example/att"}}}
    for bundle_document in (
        [1, 2],
        {"unexpected": "shape"},
        {"attestations": []},
        {"attestations": "no"},
        {"attestations": [1, 2]},
        {"attestations": [{"bundle": {}}]},
        {
            "attestations": [
                {"predicateType": "https://example.com/other", "bundle": {}}
            ]
        },
    ):

        def _fetch(url: str, timeout: int = 10, _doc: Any = bundle_document) -> Any:
            return metadata if url.endswith("/1.0.0") else _doc

        mocker.patch.object(vp, "_fetch_json", side_effect=_fetch)
        with pytest.raises(RuntimeError, match=r"malformed") as excinfo:
            vp.get_provenance_bundle("suspicious-package", "1.0.0")
        message = str(excinfo.value)
        assert "suspicious-package@1.0.0" in message
        assert "https://bundler.example/att" in message


def test_non_string_predicate_type_is_a_classified_failure(vp: Any, mocker) -> None:
    """A non-string predicateType is a malformed attestation, not a crash.

    The predicateType comparison calls ``startswith`` on each attestation's
    ``predicateType``; a non-string value (``42``) raises an unclassified
    TypeError that escapes ``main()``'s ``except RuntimeError`` collection. A
    well-formed answer with a wrong nested shape must be a classified
    RuntimeError saying the bundle is malformed, naming the package and source.
    """
    metadata = {"dist": {"attestations": {"url": "https://bundler.example/att"}}}

    def _fetch(url: str, timeout: int = 10) -> Any:
        if url.endswith("/1.0.0"):
            return metadata
        return {"attestations": [{"predicateType": 42, "bundle": {}}]}

    mocker.patch.object(vp, "_fetch_json", side_effect=_fetch)
    with pytest.raises(RuntimeError, match=r"malformed") as excinfo:
        vp.get_provenance_bundle("suspicious-package", "1.0.0")
    message = str(excinfo.value)
    assert "suspicious-package@1.0.0" in message
    assert "https://bundler.example/att" in message


def test_matching_attestation_with_null_bundle_is_a_classified_failure(
    vp: Any, mocker
) -> None:
    """An SLSA attestation whose bundle is None is malformed, not a None skip.

    The matching attestation's ``bundle`` is returned unvalidated; a ``None``
    bundle flows into ``check_subject_hash(None, ...)`` and crashes with an
    unclassified AttributeError downstream. The bundle object is a nested layer
    of the registry answer: a well-formed answer carrying ``bundle: None`` must
    be a classified RuntimeError saying the bundle is malformed, naming the
    package and source — never the (predicateType, None) tuple, which also
    masquerades as a falsy no-attestation result at the call site.
    """
    metadata = {"dist": {"attestations": {"url": "https://bundler.example/att"}}}

    def _fetch(url: str, timeout: int = 10) -> Any:
        if url.endswith("/1.0.0"):
            return metadata
        return {
            "attestations": [
                {"predicateType": "https://slsa.dev/provenance/v1", "bundle": None}
            ]
        }

    mocker.patch.object(vp, "_fetch_json", side_effect=_fetch)
    with pytest.raises(RuntimeError, match=r"malformed") as excinfo:
        vp.get_provenance_bundle("suspicious-package", "1.0.0")
    message = str(excinfo.value)
    assert "suspicious-package@1.0.0" in message
    assert "https://bundler.example/att" in message


def test_attestations_object_without_url_still_returns_none(vp: Any, mocker) -> None:
    """An attestations object lacking ``url`` is still the legitimate skip.

    Companion guard for the nested-validation contract: only a *string*
    ``dist.attestations.url`` turns the bundle path on. A well-formed metadata
    document whose ``dist.attestations`` object carries no ``url`` keeps
    returning None without raising — the no-provenance-yet gap this gate must
    never be silenced by, but also never falsely raised on.
    """
    metadata = {"dist": {"attestations": {"unexpected": "shape"}}}
    mocker.patch.object(vp, "_fetch_json", return_value=metadata)
    assert vp.get_provenance_bundle("suspicious-package", "1.0.0") is None


def test_prose_line_is_not_reported_as_untokenizable(vp: Any, tmp_path: Path) -> None:
    """A prose line mentioning `resolution:` is not an untokenizable entry.

    The raw-line scan reports any exactly-two-space-indented line carrying
    `resolution:` or `integrity:` that does not end in `:` — a prose note
    under `settings:` matches that shape and is falsely reported as a
    package-shaped entry candidate, failing parse_lockfile on a well-formed
    lockfile. Prose must not be reported; only genuine inline entries (key
    line carrying integrity) may be, and those stay covered by the existing
    inline-entry test.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD==}\n\n"
        "settings:\n"
        "  note: resolution: appears here as prose\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_untokenized_package_keys(body) == []
    assert vp.parse_lockfile(path) == {("good", "1.0.0"): "sha512-GOOD=="}


def test_duplicate_key_with_different_integrity_fails_loudly(
    vp: Any, tmp_path: Path
) -> None:
    """Two entries resolving to the same identity with different hashes fail.

    The parser keys its map by ``(name, version)`` and discards the key line's
    quoting style and peer suffix, so two lockfile entries resolving to the
    same identity collide in that map. With different attested integrity
    values a collision is a real ambiguity — one of the two attested hashes
    would be verified and the other silently dropped — so the parse must fail
    loudly, naming the colliding identity and surfacing both conflicting
    values, never overwrite silently and never verify a partial set.
    """
    body = (
        "packages:\n\n"
        "  'dup@1.0.0':\n"
        "    resolution: {integrity: sha512-FIRST==}\n\n"
        "  'dup@1.0.0':\n"
        "    resolution: {integrity: sha512-SECOND==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    with pytest.raises(RuntimeError) as excinfo:
        vp.parse_lockfile(path)
    message = str(excinfo.value)
    assert "dup@1.0.0" in message
    assert "sha512-FIRST==" in message
    assert "sha512-SECOND==" in message


def test_mixed_quoting_duplicate_with_different_integrity_fails_loudly(
    vp: Any, tmp_path: Path
) -> None:
    """Quoting style must not launder a conflicting duplicate into a parse.

    A quoted and an unquoted spelling of the same identity (`'mix@1.0.0':`
    and `mix@1.0.0:`) resolve to the same ``(name, version)`` key. If the
    parser treats the two spellings as different key lines but the same
    identity — which it must — then different integrity values behind them
    are the same conflicting-duplicate case as the exact-duplicate one, and
    must fail loudly with the identity and both values, not silently let
    whichever spelling comes last win.
    """
    body = (
        "packages:\n\n"
        "  'mix@1.0.0':\n"
        "    resolution: {integrity: sha512-QUOTED==}\n\n"
        "  mix@1.0.0:\n"
        "    resolution: {integrity: sha512-UNQUOTED==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    with pytest.raises(RuntimeError) as excinfo:
        vp.parse_lockfile(path)
    message = str(excinfo.value)
    assert "mix@1.0.0" in message
    assert "sha512-QUOTED==" in message
    assert "sha512-UNQUOTED==" in message


def test_duplicate_key_with_identical_integrity_dedupes_quietly(
    vp: Any, tmp_path: Path
) -> None:
    """The same identity attested twice with the same hash is a quiet dedup.

    The loud failure is reserved for *conflicting* duplicates. The same
    integrity written for the same identity — whether as byte-identical
    duplicate key lines or across quoting variants — carries no ambiguity:
    it parses quietly to that single entry. Guards the fix against
    over-correction: refusing every duplicate, identical or not, would fail
    a legitimate lockfile.
    """
    body = (
        "packages:\n\n"
        "  'dup@1.0.0':\n"
        "    resolution: {integrity: sha512-SAME==}\n\n"
        "  'dup@1.0.0':\n"
        "    resolution: {integrity: sha512-SAME==}\n\n"
        "  'mix2@2.0.0':\n"
        "    resolution: {integrity: sha512-ALSO-SAME==}\n\n"
        "  mix2@2.0.0:\n"
        "    resolution: {integrity: sha512-ALSO-SAME==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.parse_lockfile(path) == {
        ("dup", "1.0.0"): "sha512-SAME==",
        ("mix2", "2.0.0"): "sha512-ALSO-SAME==",
    }


def test_duplicate_identity_across_documents_fails_loudly(
    vp: Any, tmp_path: Path
) -> None:
    """The env and project documents are one text — duplicates span both.

    The parser reads the whole lockfile text, so an identity written in the
    env document's packages section and again in the project document's is
    the same collapsing pair as an in-document duplicate: with different
    integrity values one of the two attested hashes would be silently
    overwritten. The conflicting-duplicate guard must catch it across the
    document boundary, naming the identity and both values.
    """
    body = (
        "---\n"
        "lockfileVersion: '9.0'\n\n"
        "packages:\n\n"
        "  'dup@1.0.0':\n"
        "    resolution: {integrity: sha512-ENVDOC==}\n\n"
        "---\n"
        "lockfileVersion: '9.0'\n\n"
        "packages:\n\n"
        "  'dup@1.0.0':\n"
        "    resolution: {integrity: sha512-PROJECT==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    with pytest.raises(RuntimeError) as excinfo:
        vp.parse_lockfile(path)
    message = str(excinfo.value)
    assert "dup@1.0.0" in message
    assert "sha512-ENVDOC==" in message
    assert "sha512-PROJECT==" in message


def test_three_way_identity_conflict_reports_the_pairs(vp: Any, tmp_path: Path) -> None:
    """Three entries on one identity report the conflicts that exist.

    The conflict tracker keeps each identity's first value, so a third entry
    with a further-different value is another conflict against the same
    first, not a replacement: the message must surface the identity and the
    disagreeing values so the operator can see the set is ambiguous, and the
    parse must fail rather than let the last entry win.
    """
    body = (
        "packages:\n\n"
        "  'tri@1.0.0':\n"
        "    resolution: {integrity: sha512-FIRST==}\n\n"
        "  'tri@1.0.0':\n"
        "    resolution: {integrity: sha512-SECOND==}\n\n"
        "  tri@1.0.0:\n"
        "    resolution: {integrity: sha512-THIRD==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    with pytest.raises(RuntimeError) as excinfo:
        vp.parse_lockfile(path)
    message = str(excinfo.value)
    assert "tri@1.0.0" in message
    assert "sha512-FIRST==" in message
    assert "sha512-THIRD==" in message


def test_peer_suffixed_twin_with_integrity_fails_as_a_parser_gap_first(
    vp: Any, tmp_path: Path
) -> None:
    """A peer-suffixed twin stays the unmatched guard's case, not the conflict's.

    The identity deliberately excludes peer suffixes, and a suffixed packages:
    key carrying integrity never parses at all — the unmatched-keys guard owns
    it before the conflict guard composes (and the conflict pass takes no part
    in unmatchable keys). This pins the guard composition: a suffixed twin of
    a parsed identity with a different integrity fails as a parser gap naming
    the suffixed key, not as a value conflict between the two.
    """
    body = (
        "packages:\n\n"
        "  'plain@1.0.0':\n"
        "    resolution: {integrity: sha512-PLAIN==}\n\n"
        "  'plain@1.0.0(peer@2.0.0)':\n"
        "    resolution: {integrity: sha512-SUFFIXED==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == ["plain@1.0.0(peer@2.0.0)"]
    with pytest.raises(RuntimeError, match="did not match the lockfile parser"):
        vp.parse_lockfile(path)


# ---------------------------------------------------------------------------
# Sigstore classification (verify_sigstore): substring-based skip misfires.
# ---------------------------------------------------------------------------
#
# verify_sigstore classifies NetworkError and VerificationError by class; the
# VerificationError branch exempts exactly one library compatibility gap — the
# sigstore 4.x fixed message "Integrated time only supported for dsse/hashedrekord
# 0.0.1 types" — and the blanket handler skips only the genuine bundle-format
# error class (sigstore.models.InvalidBundle, e.g. from Bundle.from_json('[]')),
# matched by isinstance with an MRO-name fallback for layouts that do not export
# it. Message text is never the classifier: a generic exception whose wording
# merely resembles a compatibility message must fail closed. The stubs below
# install fake sigstore.* modules (same pattern as
# test_unclassifiable_sigstore_exception_fails_closed) and raise from inside
# the verification path, so the only variable under test is that classification
# logic. Every other failure must fail closed.

_FIXED_COMPAT_MESSAGE = (
    "Integrated time only supported for dsse/hashedrekord 0.0.1 types"
)


class _StubInvalidBundle(Exception):
    """Stand-in for sigstore.models.InvalidBundle (a sigstore.errors.Error subclass)."""


def _sigstore_stub_modules(
    *,
    verification_error_message: str | None = None,
    network_error_message: str | None = None,
    bundle_error: tuple[type[Exception], str] | None = None,
    publish_invalid_bundle: type[Exception] | None = None,
) -> dict[str, types.ModuleType]:
    """Build sigstore.* module stubs for verify_sigstore tests.

    ``verification_error_message`` and ``network_error_message`` make
    verify_dsse raise that message as the stub's own VerificationError /
    NetworkError class (so the raised instance matches the class the stub
    publishes). ``bundle_error`` is a (class, message) pair raised by
    Bundle.from_json. With nothing set, verification succeeds (the success
    path).

    ``publish_invalid_bundle`` opt-in publishes the given class as
    ``sigstore.models.InvalidBundle`` on the stub module, flipping the
    implementation's guarded import onto its isinstance path; when omitted
    (the default, used by every pre-existing test) the attribute stays
    absent and the environment is byte-identical to before.
    """
    errors_mod = types.ModuleType("sigstore.errors")
    errors_mod.NetworkError = type("NetworkError", (Exception,), {})  # pyright: ignore[reportAttributeAccessIssue]
    errors_mod.VerificationError = type(  # pyright: ignore[reportAttributeAccessIssue]
        "VerificationError", (Exception,), {}
    )

    class _StubVerifier:
        @staticmethod
        def production() -> _StubVerifier:
            return _StubVerifier()

        def verify_dsse(self, bundle: Any, policy: Any) -> None:
            if verification_error_message is not None:
                raise errors_mod.VerificationError(verification_error_message)  # pyright: ignore[reportAttributeAccessIssue]
            if network_error_message is not None:
                raise errors_mod.NetworkError(network_error_message)  # pyright: ignore[reportAttributeAccessIssue]

    class _StubBundle:
        @classmethod
        def from_json(cls, raw: str) -> Any:
            if bundle_error is not None:
                raise bundle_error[0](bundle_error[1])
            return object()

    models_mod = types.ModuleType("sigstore.models")
    models_mod.Bundle = _StubBundle  # pyright: ignore[reportAttributeAccessIssue]
    if publish_invalid_bundle is not None:
        models_mod.InvalidBundle = publish_invalid_bundle  # pyright: ignore[reportAttributeAccessIssue]
    verify_mod = types.ModuleType("sigstore.verify")
    verify_mod.Verifier = _StubVerifier  # pyright: ignore[reportAttributeAccessIssue]

    def _stub_issuer_init(self: Any, issuer: str) -> None:
        del issuer  # the stub records nothing; the real class validates the issuer

    policy_mod = types.ModuleType("sigstore.verify.policy")
    policy_mod.OIDCIssuer = type(  # pyright: ignore[reportAttributeAccessIssue]
        "OIDCIssuer", (), {"__init__": _stub_issuer_init}
    )
    sigstore_mod = types.ModuleType("sigstore")
    return {
        "sigstore": sigstore_mod,
        "sigstore.errors": errors_mod,
        "sigstore.models": models_mod,
        "sigstore.verify": verify_mod,
        "sigstore.verify.policy": policy_mod,
    }


def test_generic_validation_error_message_fails_closed(vp: Any, mocker) -> None:
    """A generic exception mentioning 'validation error' must fail closed.

    RED: the blanket exception handler returns ok=True (a skip) whenever the
    exception's str() merely CONTAINS "validation error" or "failed to load
    bundle", so an unrecognized exception whose message happens to carry that
    substring is reported as a verified/skipped-ok package instead of a failed
    check. The function's own docstring promises the opposite: any non-
    NetworkError, non-VerificationError exception is unclassifiable and fails
    closed. A bundle load failure of unknown origin must never read as a
    verified package merely because its wording resembles a known
    compatibility gap.
    """
    stub = _sigstore_stub_modules(
        bundle_error=(RuntimeError, "cryptography validation error: unknown origin")
    )
    mocker.patch.dict(sys.modules, stub)
    ok, _message = vp.verify_sigstore("{}")
    assert ok is False


def test_generic_failed_to_load_bundle_message_fails_closed(vp: Any, mocker) -> None:
    """A generic exception mentioning 'failed to load bundle' must fail closed.

    RED: same mechanism as the 'validation error' case — the substring match
    in the blanket exception handler turns an unclassifiable exception (not a
    NetworkError, not a VerificationError) into a skip (ok=True). The
    fail-closed contract requires ok=False.
    """
    stub = _sigstore_stub_modules(
        bundle_error=(RuntimeError, "failed to load bundle: unknown origin")
    )
    mocker.patch.dict(sys.modules, stub)
    ok, _message = vp.verify_sigstore("{}")
    assert ok is False


def test_verification_error_with_similar_not_supported_message_fails_closed(
    vp: Any, mocker
) -> None:
    """A VerificationError that is not the fixed compat message fails closed.

    RED: the VerificationError handler returns ok=True (a skip) whenever its
    str() merely CONTAINS "only supported" or "not supported", so any other
    verification failure whose message happens to carry those substrings — an
    unsupported key format, an unsupported certificate policy — is reported as
    a verified package instead of a failed check. Only the library's exact
    fixed compatibility message is the legitimate skip; every other
    VerificationError is a real verification failure and must produce
    ok=False.
    """
    stub = _sigstore_stub_modules(
        verification_error_message="key format not supported by this verifier build"
    )
    mocker.patch.dict(sys.modules, stub)
    ok, _message = vp.verify_sigstore("{}")
    assert ok is False


def test_verification_error_with_only_supported_similar_message_fails_closed(
    vp: Any, mocker
) -> None:
    """A 'only supported' VerificationError that is not the fixed one fails.

    RED: same substring mechanism as the 'not supported' case, exercising the
    other substring ("only supported") with a message that is NOT the fixed
    compatibility text: the skip must not fire on substring resemblance.
    """
    stub = _sigstore_stub_modules(
        verification_error_message="threshold only supported for trusted root sets"
    )
    mocker.patch.dict(sys.modules, stub)
    ok, _message = vp.verify_sigstore("{}")
    assert ok is False


# -- GREEN companions: the classification's legitimate outcomes, pinned so a
# -- fix cannot over-correct into failing every skip. -------------------------


def test_sigstore_success_path_verifies(vp: Any, mocker) -> None:
    """The success path verifies: ok=True on a clean verification.

    GREEN companion: with every stub in place and no exception raised, the
    success path returns ok=True with the verification-success message.
    """
    stub = _sigstore_stub_modules()
    mocker.patch.dict(sys.modules, stub)
    ok, message = vp.verify_sigstore("{}")
    assert ok is True
    assert message == "Sigstore signature verified (Rekor + Fulcio)"


def test_other_verification_error_fails_closed(vp: Any, mocker) -> None:
    """A VerificationError carrying no compat substring still fails closed.

    GREEN companion: a VerificationError whose message contains neither skip
    substring is a fatal verification failure (ok=False) — the class-based
    branch itself is correct and must survive the fix.
    """
    stub = _sigstore_stub_modules(verification_error_message="signature mismatch")
    mocker.patch.dict(sys.modules, stub)
    ok, _message = vp.verify_sigstore("{}")
    assert ok is False


def test_network_error_is_a_skip(vp: Any, mocker) -> None:
    """A NetworkError is an infrastructure warning, not a failure.

    GREEN companion: the NetworkError branch returns ok=True (skip) — the
    documented treatment for TUF/Rekor transport trouble.
    """
    stub = _sigstore_stub_modules(network_error_message="rekor unreachable")
    mocker.patch.dict(sys.modules, stub)
    ok, message = vp.verify_sigstore("{}")
    assert ok is True
    assert "network unavailable" in message


def test_fixed_integrated_time_message_is_a_skip(vp: Any, mocker) -> None:
    """The exact integrated-time compatibility message is a skip.

    GREEN companion: a VerificationError carrying the library's exact fixed
    compatibility message ("Integrated time only supported for dsse/
    hashedrekord 0.0.1 types") is the one VerificationError that legitimately
    skips (ok=True) — pinning the exact message prevents the fix from
    over-correcting into failing every Rekor-timestamp incompatibility.
    """
    stub = _sigstore_stub_modules(verification_error_message=_FIXED_COMPAT_MESSAGE)
    mocker.patch.dict(sys.modules, stub)
    ok, message = vp.verify_sigstore("{}")
    assert ok is True
    assert "timestamp skipped" in message


def test_genuine_bundle_format_error_is_a_skip(vp: Any, mocker) -> None:
    """A genuine sigstore bundle-format error type is a skip.

    GREEN companion: sigstore 4.x raises sigstore.models.InvalidBundle (an
    errors.Error subclass) — not VerificationError — when a bundle document
    cannot be loaded, e.g. Bundle.from_json('[]'). Stub Bundle.from_json to
    raise that genuine error class carrying the library's "validation error"
    wording: the generic-exception branch must keep treating the *class* as
    the known compatibility gap and return ok=True even though a same-worded
    generic exception must now fail closed (see the RED tests above).
    """
    stub = _sigstore_stub_modules(
        bundle_error=(_StubInvalidBundle, "validation error: bundle is not valid")
    )
    mocker.patch.dict(sys.modules, stub)
    ok, message = vp.verify_sigstore("{}")
    assert ok is True
    assert "Bundle format not supported" in message


def test_name_resembling_invalid_bundle_fails_closed_when_genuine_class_importable(
    vp: Any, mocker
) -> None:
    """An unrelated '*InvalidBundle' class fails closed when the real one exists.

    RED: the class-name fallback in _is_bundle_format_error is an OR, not a
    fallback — it runs unconditionally even when the genuine
    sigstore.models.InvalidBundle IS importable in the process and the
    isinstance test already failed. With the stub environment publishing a
    genuine InvalidBundle class, an exception from an UNRELATED class whose
    name merely contains "InvalidBundle" (types.new_class(
    "WeirdInvalidBundle", (Exception,))) raised through the bundle-load path
    currently matches the name fallback and returns ok=True (a skip) —
    reporting an unclassifiable exception as a verified/skipped-ok package.
    The fail-closed contract requires ok=False: when the genuine class is
    importable, only instances of that exact class may take the
    compatibility skip; a name-collision class must never read as one.
    """
    genuine = type("InvalidBundle", (Exception,), {})
    unrelated = types.new_class("WeirdInvalidBundle", (Exception,))
    stub = _sigstore_stub_modules(
        bundle_error=(unrelated, "unrelated failure of unknown origin"),
        publish_invalid_bundle=genuine,
    )
    mocker.patch.dict(sys.modules, stub)
    ok, _message = vp.verify_sigstore("{}")
    assert ok is False


def test_published_genuine_invalid_bundle_is_a_skip_via_isinstance(
    vp: Any, mocker
) -> None:
    """The genuine published InvalidBundle class skips via the isinstance path.

    GREEN companion: with the stub environment PUBLISHING
    sigstore.models.InvalidBundle, the implementation's guarded import
    succeeds and _is_bundle_format_error takes its isinstance primary branch
    (no existing test exercises this path — the pre-existing companion only
    covers the name fallback, since its stub publishes no such attribute).
    An instance of exactly that published class raised through the
    bundle-load path must keep skipping (ok=True) so a fix cannot
    over-correct into failing every genuine bundle-format incompatibility.
    """
    published = type("InvalidBundle", (Exception,), {})
    stub = _sigstore_stub_modules(
        bundle_error=(published, "validation error: bundle is not valid"),
        publish_invalid_bundle=published,
    )
    mocker.patch.dict(sys.modules, stub)
    ok, message = vp.verify_sigstore("{}")
    assert ok is True
    assert "Bundle format not supported" in message


def test_sigstore_unimportable_fails_closed(vp: Any, mocker) -> None:
    """A sigstore package that cannot be imported at all fails closed.

    GREEN companion: ImportError from the import block is a classified
    failure (ok=False), not a crash and not a skip. The stub module raises
    ImportError from its module-level __getattr__, so every
    `from sigstore.errors import ...` fails regardless of what the developer
    machine's site-packages carry (sigstore is not installed in the backend
    venv, and this must not depend on that either way).
    """
    errors_mod = types.ModuleType("sigstore.errors")

    def _unimportable(name: str) -> Any:
        raise ImportError("sigstore not installed")

    errors_mod.__getattr__ = _unimportable  # pyright: ignore[reportAttributeAccessIssue]
    sigstore_mod = types.ModuleType("sigstore")
    mocker.patch.dict(
        sys.modules, {"sigstore": sigstore_mod, "sigstore.errors": errors_mod}
    )
    ok, message = vp.verify_sigstore("{}")
    assert ok is False
    assert "sigstore not importable" in message


# Marker constants shared by the Observation-3 tests: each names the deciding
# classifier so a CI log can tell which path chose the skip. These are NOT
# what the implementation returns today — the tests assert against them and
# (for the composition test) against each environment's message, all of
# which is currently one identical string.
_CLASS_MATCH_MARKER = "genuine InvalidBundle class matched"
_FALLBACK_MARKER = "exception-class-name fallback matched"


# ---------------------------------------------------------------------------
# Observation 1 — the unsupported-algorithm diagnostic vanishes on two failure
# paths of check_subject_hash.
# ---------------------------------------------------------------------------
#
# check_subject_hash's algorithm check lives INSIDE the subject loop, after a
# sha512 digest is matched. Two lifecycle paths then reach the loop's exit or
# except without ever evaluating it, and the returned diagnostic names no
# algorithm at all:
#
#   - an attestation payload with no sha512 subject (e.g. only a sha256 digest)
#     exits the loop and reads "no sha512 subject found in attestation payload"
#   - a malformed payload (base64 that does not decode to JSON) hits the
#     blanket except and reads "payload parse error: ..."
#
# The documented contract (check_subject_hash's docstring and the pre-existing
# pinned test with a sha512 subject) is that a non-sha512 lockfile integrity
# fails with a message NAMING the unsupported algorithm. On both paths above
# the message instead blames the attestation ("no sha512 subject found") or
# just says parse error — the operator is pointed at the wrong artifact.
# Requirement: on EVERY failure path where the lockfile integrity's algorithm
# prefix is not sha512, the returned (ok=False) message names the prefix.


def test_unsupported_algo_named_when_no_sha512_subject(vp: Any) -> None:
    """No-sha512-subject path: a non-sha512 integrity names the algorithm.

    The bundle payload carries only a sha256 digest, so the subject loop finds
    no sha512 subject and exits via the loop's fall-through. The lockfile value
    is sha1 — the diagnostic must name that unsupported algorithm, not blame
    the attestation with "no sha512 subject found in attestation payload".
    The payload content is irrelevant here: the lockfile value alone decides
    the verdict, and the message must say which algorithm it refused.
    """
    payload = base64.b64encode(
        json.dumps({"subject": [{"digest": {"sha256": "cd" * 32}}]}).encode()
    ).decode()
    bundle = {"dsseEnvelope": {"payload": payload}}

    ok, message = vp.check_subject_hash(bundle, "sha1-AAAABBBB==")
    assert ok is False
    # The mechanism assertion: the diagnostic names the unsupported algorithm.
    assert "sha1" in message
    # Not the misattributed skip-shaped message this failure path returns today.
    assert "no sha512 subject found in attestation payload" != message


def test_unsupported_algo_named_on_payload_parse_error(vp: Any) -> None:
    """Payload-parse-error path: a non-sha512 integrity names the algorithm.

    The payload base64 does not decode to JSON, so the blanket except fires
    before the subject loop runs at all. The lockfile value is sha256 — the
    diagnostic must still name that unsupported algorithm, not a bare
    "payload parse error: ..." that names only the payload problem.
    """
    malformed_bundle = {"dsseEnvelope": {"payload": "not-base64!"}}

    ok, message = vp.check_subject_hash(malformed_bundle, "sha256-BBBBBBBB==")
    assert ok is False
    assert "sha256" in message
    # The message must not be the bare parse message alone: the unsupported
    # algorithm is failure-causing material and must be identified.
    assert "payload parse error" not in message


def test_sha512_lockfile_with_no_sha512_subject_keeps_its_message(vp: Any) -> None:
    """Control: a sha512 lockfile value with no sha512 subject keeps its text.

    GREEN companion pinning the non-target path: when the lockfile integrity
    IS sha512 and the attestation payload carries no sha512 subject, the
    loop's fall-through is exactly the right diagnostic — the attestation
    genuinely lacks the compared digest. The anti-overfire check for the
    requirement above: a fix must not bolt the algorithm diagnostic onto this
    legitimate path (the prefix IS sha512, there is no unsupported algorithm
    to name).
    """
    payload = base64.b64encode(
        json.dumps({"subject": [{"digest": {"sha256": "cd" * 32}}]}).encode()
    ).decode()
    bundle = {"dsseEnvelope": {"payload": payload}}

    ok, message = vp.check_subject_hash(
        bundle, "sha512-" + base64.b64encode(bytes(range(32))).decode()
    )
    assert ok is False
    assert message == "no sha512 subject found in attestation payload"


# ---------------------------------------------------------------------------
# Observation 2 — verification material invisible to the whole gate.
# ---------------------------------------------------------------------------
#
# The gate's invariant: verification material it cannot attribute fails
# loudly. The `extra:` line in the reproduction below is an inline mapping
# carrying `resolution: {integrity: …}` for no package entry at all:
#
#   - it is not a key line, so the block splitter yields no block for it and
#     neither _scan_lockfile's package/unaccounted handling nor the
#     unmatched guard ever considers it;
#   - its `resolution:` sits mid-line, so the unreached-resolutions guard
#     (_RESOLUTION_LINE_RE, line-anchored) cannot see it;
#   - the inline-entry detector (find_untokenized_package_keys) requires the
#     scalar to carry `@`, and `extra` carries none.
#
# Every guard is silent at once, so `sha512-ORPHAN==` is verification
# material compared against no attestation while the run exits 0. The
# requirement: parse_lockfile must refuse to proceed (RuntimeError) when an
# inline non-package mapping carries integrity material, naming the
# offending line/key.


def test_inline_non_package_entry_with_integrity_fails_loudly(
    vp: Any, tmp_path: Path
) -> None:
    """An inline non-package mapping carrying integrity must fail the parse.

    The `extra:` line is an inline mapping carrying `resolution:
    {integrity: …}` for no package entry at all: it is not a key line (the
    splitter yields no block for it), its `resolution:` sits mid-line so the
    unreached-resolutions guard cannot see it, and the inline-entry detector
    requires the scalar to carry `@` — so `sha512-ORPHAN==` is verification
    material compared against no attestation while the run exits 0. The
    gate's invariant — verification material it cannot attribute fails
    loudly — requires the parse to refuse, naming the offending key.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD==}\n"
        "  extra: {resolution: {integrity: sha512-ORPHAN==}}\n"
    )
    path = _write_lockfile(tmp_path, body)
    # The parse must refuse to proceed... (RED: today it happily returns).
    with pytest.raises(RuntimeError) as excinfo:
        vp.parse_lockfile(path)
    # ...naming the offending line/key so the operator can locate the drift.
    assert "extra" in str(excinfo.value)


def test_inline_non_package_entry_alone_with_integrity_fails_loudly(
    vp: Any, tmp_path: Path
) -> None:
    """The inline non-package shape fails loudly with no genuine entry beside it.

    Companion re-check that the refusal stands on its own, not only when a
    well-formed neighbour parses: with no other entry in the lockfile, the
    inline block's integrity is STILL verification material attributed to no
    package entry, and parse_lockfile must refuse (not return a map, not
    lean on the zero-parse/unreached guards) naming the offending key.
    """
    body = "packages:\n\n  extra: {resolution: {integrity: sha512-ORPHAN==}}\n"
    path = _write_lockfile(tmp_path, body)
    with pytest.raises(RuntimeError) as excinfo:
        vp.parse_lockfile(path)
    assert "extra" in str(excinfo.value)


def test_comment_mentioning_resolution_in_an_entry_block_stays_a_quiet_parse(
    vp: Any, tmp_path: Path
) -> None:
    """Pinned GREEN re-check (:445): a comment mentioning `resolution:` parses.

    A comment line inside a well-indented entry block carrying the text
    `resolution:` (an integrity in prose) must stay a quiet parse — the inline
    refusal must not over-fire onto a comment. The original shape from the
    pinned test at :445 is replayed here with the same assertion target:
    parse_lockfile returns both entries.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    # note: resolution: appears here as prose\n"
        "    resolution: {integrity: sha512-GOOD==}\n\n"
        "  'good2@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD2==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.parse_lockfile(path) == {
        ("good", "1.0.0"): "sha512-GOOD==",
        ("good2", "1.0.0"): "sha512-GOOD2==",
    }


def test_prose_line_carries_resolution_as_prose_not_entry(
    vp: Any, tmp_path: Path
) -> None:
    """Pinned GREEN re-check (:829): prose prose lines are not reported.

    A prose line under `settings:` mentioning `resolution:` is not an inline
    package entry: the untokenized-package-key scan must keep returning [] for
    it, and the file must parse to its one entry — exactly the contract the
    pinned test at :829 asserts, replayed here as the composition's over-fire
    check so the fix cannot over-correct into failing a well-formed lockfile.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD==}\n\n"
        "settings:\n"
        "  note: resolution: appears here as prose\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_untokenized_package_keys(body) == []
    assert vp.parse_lockfile(path) == {("good", "1.0.0"): "sha512-GOOD=="}


def test_quoted_colon_scalar_is_not_the_inline_non_package_shape(
    vp: Any, tmp_path: Path
) -> None:
    """A quoted scalar carrying `:` is not misdetected as the inline shape.

    The inline-entry detector's scalar part-of `_inline_entry_scalar` splits an
    unquoted line at the first colon — a quoted scalar like 'weird:name@1.0.0'
    must survive both the quoted-scalar branch and the detector's reported
    shape, without being reported as an inline non-package mapping. If its
    scalar were split at `weird`, the line would read as a non-package inline
    mapping and the file would refuse to parse. Pinned GREEN: the file parses
    into its map, the entry lands in the map, no refusal, no gap label.
    """
    body = (
        "packages:\n\n"
        "  'weird:name@1.0.0':\n"
        "    resolution: {integrity: sha512-AAAABBBB==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_untokenized_package_keys(body) == []
    assert vp.parse_lockfile(path) == {("weird:name", "1.0.0"): "sha512-AAAABBBB=="}


def test_prose_tick_note_with_colon_prefix_is_not_an_inline_entry(
    vp: Any, tmp_path: Path
) -> None:
    """A prose line with a colon-set prefix stays prose, never an inline entry.

    A prose line whose first section ends before the first colon (`note:
    something:`) reads as a non-package inline mapping's scalar `note` —
    exactly the tension shape staged above. This shape is indistinguishable
    from `extra:` today only to the token-level reader: `note` carries no `@`,
    so the detector's current rule keeps it quiet. FLAGGED, not resolved
    here: any fix's detector must keep this quiet, without leaking the
    inline refusal onto it, while still refusing `extra:` — the same
    conclusion both ways: a colon-bearing decorative prefix is not an
    entry candidate.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD==}\n\n"
        "settings:\n"
        "  note: something prose-like, not an entry\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_untokenized_package_keys(body) == []
    assert vp.parse_lockfile(path) == {("good", "1.0.0"): "sha512-GOOD=="}


# ---------------------------------------------------------------------------
# Observation 3 — a skip message that hides which classifier decided.
# ---------------------------------------------------------------------------
#
# verify_sigstore's bundle-format compatibility skip returns the same string —
# "Bundle format not supported by sigstore 4.x — skipped (...)" — whether the
# decision came from the genuine exception class (isinstance against the
# imported sigstore.models.InvalidBundle) or from the MRO-name fallback used
# when that import is unavailable. The CI log shows no difference, so a CI
# run cannot reveal when the gate is running on the fallback (e.g. after a
# library upgrade renames the class). Requirement: the skip message must
# reveal which path decided — distinguishable text for the class-match path
# vs the fallback path. One test per environment, same stubbing pattern as
# the pre-existing tests at :1259-1332.


def test_bundleformat_skip_message_differs_on_isinstance_path(vp: Any, mocker) -> None:
    """The genuine-class path's skip message is distinguishable in a CI log.

    Stub environment publishing sigstore.models.InvalidBundle (the genuine
    class importable) — the isinstance primary branch decides the skip. The
    resulting message must carry a marker naming the classifier: the class
    match, not the name fallback, decided here.
    """
    published = type("InvalidBundle", (Exception,), {})
    stub = _sigstore_stub_modules(
        bundle_error=(published, "validation error: bundle is not valid"),
        publish_invalid_bundle=published,
    )
    mocker.patch.dict(sys.modules, stub)
    ok, message = vp.verify_sigstore("{}")
    assert ok is True
    assert "Bundle format not supported" in message
    # RED: the message must name the deciding classifier — the genuine class
    # match, not the undifferentiated text the fallback path produces.
    assert _CLASS_MATCH_MARKER in message


def test_bundleformat_skip_message_differs_on_fallback_path(vp: Any, mocker) -> None:
    """The fallback path's skip message is distinguishable from the class match.

    Stub environment NOT publishing sigstore.models.InvalidBundle (the default
    of every pre-existing stub) — the MRO-name fallback decides the skip. The
    resulting message must carry a marker naming the classifier: the name
    fallback, not the class match, decided here. A CI log must be able to
    show when the gate runs on the fallback (e.g. after a library upgrade
    renames the class).
    """
    stub = _sigstore_stub_modules(
        bundle_error=(_StubInvalidBundle, "validation error: bundle is not valid")
    )
    mocker.patch.dict(sys.modules, stub)
    ok, message = vp.verify_sigstore("{}")
    assert ok is True
    assert "Bundle format not supported" in message
    # RED: the message must name the deciding classifier — the name fallback,
    # not the identical text the genuine-class path produces.
    assert _FALLBACK_MARKER in message


def test_bundleformat_skip_messages_differ_between_environments(
    vp: Any, mocker
) -> None:
    """The two deciding environments produce distinguishable skip messages.

    Composition check: run both environments through the same stub pattern
    and assert the resulting skip messages differ between them (class-match
    vs fallback), and each names its own classifier — so a CI log needs no
    extra context to show which decided.
    """
    published = type("InvalidBundle", (Exception,), {})
    stub_class = _sigstore_stub_modules(
        bundle_error=(published, "validation error: bundle is not valid"),
        publish_invalid_bundle=published,
    )
    stub_fallback = _sigstore_stub_modules(
        bundle_error=(published, "validation error: bundle is not valid")
    )
    mocker.patch.dict(sys.modules, stub_class)
    ok_pub, msg_pub = vp.verify_sigstore("{}")
    mocker.patch.dict(sys.modules, stub_fallback)
    ok_fall, msg_fall = vp.verify_sigstore("{}")
    assert ok_pub is True
    assert ok_fall is True
    assert "Bundle format not supported" in msg_pub
    assert "Bundle format not supported" in msg_fall
    assert _CLASS_MATCH_MARKER in msg_pub
    assert _FALLBACK_MARKER in msg_fall
    # RED: the two messages must be distinguishable.
    assert msg_pub != msg_fall


# ---------------------------------------------------------------------------
# Aggregate sweep: an all-skip sweep must not report success.
# ---------------------------------------------------------------------------


def test_sweep_with_no_verifications_fails_loudly(
    vp: Any, tmp_path: Path, mocker, capsys
) -> None:
    """A sweep that parsed packages but verified none must exit 1.

    RED: with every parsed package hitting the well-formed no-attestation gap
    (get_provenance_bundle returning None for each), main() prints "Verified
    via provenance: 0" and returns 0 — reporting success over a run that
    verified nothing. Per the maintainer decision (2026-09-23), when at least
    one package was parsed and zero packages verified, main() must return 1
    and print an explicit aggregate failure saying nothing was verified. The
    aggregate message's stable marker substring is 'nothing was verified'
    (asserted case-insensitively); the per-package no-attestation counting
    itself stays unchanged.
    """
    body = (
        "packages:\n\n"
        "  'a@1.0.0':\n    resolution: {integrity: sha512-A==}\n\n"
        "  'b@1.0.0':\n    resolution: {integrity: sha512-B==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    mocker.patch.object(vp, "LOCKFILE", path)
    mocker.patch.object(vp, "get_provenance_bundle", return_value=None)
    exit_code = vp.main()
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Verified via provenance: 0" in output
    assert "No attestation (expected gap): 2" in output
    assert "nothing was verified" in output.lower()


def test_sweep_with_at_least_one_verified_package_still_succeeds(
    vp: Any, tmp_path: Path, mocker, capsys
) -> None:
    """A sweep with at least one verified package still exits 0.

    GREEN companion: the aggregate-failure contract fires only when nothing
    was verified. With one parsed package attested and verified (hash matches,
    Sigstore verifies) and one hitting the no-attestation gap, main() still
    returns 0.
    """
    integrity = "sha512-" + base64.b64encode(bytes.fromhex("cd" * 32)).decode()
    payload = base64.b64encode(
        json.dumps({"subject": [{"digest": {"sha512": "cd" * 32}}]}).encode()
    ).decode()
    bundle = {"dsseEnvelope": {"payload": payload}}

    def sweep(name: str, ver: str) -> tuple[str, dict[str, Any]] | None:
        if name == "attested":
            return ("https://slsa.dev/provenance/v1", bundle)
        return None

    path = _write_lockfile(
        tmp_path,
        "packages:\n\n"
        "  'attested@1.0.0':\n"
        f"    resolution: {{integrity: {integrity}}}\n\n"
        "  'gap@1.0.0':\n    resolution: {integrity: sha512-GAPGAP==}\n",
    )
    mocker.patch.object(vp, "LOCKFILE", path)
    mocker.patch.object(vp, "get_provenance_bundle", side_effect=sweep)
    mocker.patch.object(
        vp,
        "check_subject_hash",
        return_value=(True, "subject hash matches lockfile integrity"),
    )
    mocker.patch.object(
        vp,
        "verify_sigstore",
        return_value=(True, "Sigstore signature verified (Rekor + Fulcio)"),
    )
    exit_code = vp.main()
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Verified via provenance: 1" in output
    assert "No attestation (expected gap): 1" in output


def test_unattributed_resolution_block_fails_loudly(vp: Any, tmp_path: Path) -> None:
    """A resolution-carrying block behind no package entry must fail loudly.

    `ledger:` is a well-formed 2-space key the block splitter tokenizes, but
    it is not package-shaped: the scan skips it (no `@`), and the ownership
    walk counts its resolution as owned because any key line starts a block.
    So `sha512-ORPHAN==` — verification material — is compared against no
    attestation, reported as no gap, and the run exits 0. The gate's
    invariant elsewhere is that verification material it cannot attribute
    fails loudly: parse_lockfile must refuse to proceed, naming the
    offending key.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD==}\n\n"
        "  ledger:\n"
        "    resolution: {integrity: sha512-ORPHAN==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    with pytest.raises(RuntimeError, match="ledger"):
        vp.parse_lockfile(path)


def test_unattributed_resolution_block_with_no_other_entries_fails_loudly(
    vp: Any, tmp_path: Path
) -> None:
    """The same hole stands alone: an unattributed resolution alone is loud.

    With no legitimate entry alongside, the parsed map is empty and the
    zero-parse guard does not fire (the resolution *was* reached — by the
    unattributed block). The unattributed-resolution refusal must fire on
    its own, still naming the offending key, not lean on the zero-parse
    or unreached-resolution guards.
    """
    body = "packages:\n\n  ledger:\n    resolution: {integrity: sha512-ORPHAN==}\n"
    path = _write_lockfile(tmp_path, body)
    with pytest.raises(RuntimeError, match="ledger"):
        vp.parse_lockfile(path)


def test_all_zkochan_entries_still_parse_to_an_empty_map(
    vp: Any, tmp_path: Path
) -> None:
    """Re-check of a pinned contract: all-@zkochan lockfile still parses to {}.

    Every entry here is package-shaped and deliberately excluded — each
    resolution is attributed to a (named, excluded) package entry, so the
    unattributed-resolution refusal must not over-fire on this shape.
    """
    body = (
        "packages:\n\n"
        "  '@zkochan/internal@1.0.0':\n"
        "    resolution: {integrity: sha512-ZKOCHAN==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.parse_lockfile(path) == {}


def test_empty_quoted_key_with_resolution_still_parses_to_an_empty_map(
    vp: Any, tmp_path: Path
) -> None:
    """Re-check of a pinned contract: `'':` with a resolution parses to {}.

    TENSION (flagged, not resolved here): this pinned shape is itself a
    resolution-carrying block attributed to no package entry — the same
    class of hole as an unattributed `ledger:` block, since the integrity
    behind `'':` is verified against nothing. If the unattributed-resolution
    refusal is written generally, it fires here too and this re-check goes
    red post-fix; that outcome is the tension surfacing, and it must go
    back to a human decision (carve the empty key out deliberately, or
    revise the pinned contract), never be silenced as a drive-by.
    """
    body = "packages:\n\n  '':\n    resolution: {integrity: sha512-AAAA==}\n"
    path = _write_lockfile(tmp_path, body)
    assert vp.parse_lockfile(path) == {}


def test_sweep_all_excluded_lockfile_still_succeeds(
    vp: Any, tmp_path: Path, mocker, capsys
) -> None:
    """A sweep whose every entry is legitimately excluded still exits 0.

    GREEN companion pinning the aggregate guard's documented interaction
    with the zero-parsed sweep (main()'s docstring: returns 0 "when nothing
    was parsed at all (a lockfile whose every entry is legitimately
    excluded, e.g. @zkochan)"). The guard fires only when packages were
    parsed but none verified; an empty parsed map must skip it entirely —
    if the guard ever dropped its `packages and` condition, an all-excluded
    lockfile would start failing with 'nothing was verified' and this test
    would catch the regression at the sweep level, not just the parser
    level.
    """
    path = _write_lockfile(
        tmp_path,
        "packages:\n\n"
        "  '@zkochan/internal@1.0.0':\n"
        "    resolution: {integrity: sha512-ZKOCHAN==}\n",
    )
    mocker.patch.object(vp, "LOCKFILE", path)
    exit_code = vp.main()
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Parsed 0 packages" in output
    assert "nothing was verified" not in output.lower()
    assert "All attested packages passed provenance verification." in output


# ---------------------------------------------------------------------------
# Observation 4 — two conditional tests that can never meaningfully fail.
# ---------------------------------------------------------------------------
#
# test_non_sha512_integrity_entry_is_parsed_or_reported (:307) and
# test_quoted_key_with_colon_is_parsed_or_reported (:359) branch on the
# parser's own behavior (`if unmatched: ... else: ...`), so each passes
# whichever way the parser decides — they pin nothing. Their replacements
# below pin the deterministic behavior observed on HEAD by running the
# script directly:
#
#   - 'legacy-pkg@1.0.0' with integrity `sha1-AAAABBBB==` PARSES into the
#     map: {('legacy-pkg', '1.0.0'): 'sha1-AAAABBBB=='}
#   - 'weird:name@1.0.0' (quoted scalar carrying a colon) with integrity
#     `sha512-AAAABBBB==` PARSES into the map: {('weird:name', '1.0.0'):
#     'sha512-AAAABBBB=='}
#
# Neither shape is reported as a parser gap today (find_unmatched_package_keys
# returns [] for both; find_untokenized_package_keys too). The replacements
# pin exactly those outcomes; the originals are left in place for the
# maintainer to compare and retire.
#
# Observed outcomes (python importing scripts/verify_provenance.py by path):
#   obs4-sha1:  unmatched=[], untokenized=[],
#               parsed={('legacy-pkg','1.0.0'): 'sha1-AAAABBBB=='}
#   obs4-colon: unmatched=[], untokenized=[],
#               parsed={('weird:name','1.0.0'): 'sha512-AAAABBBB=='}}


def test_non_sha512_integrity_entry_parses_into_the_map(
    vp: Any, tmp_path: Path
) -> None:
    """Pinned observation: a sha1-integrity entry parses, not reported.

    Replacement for the conditional test_non_sha512_integrity_entry_is_
    parsed_or_reported (:307, left in place above). Observed on HEAD: the
    sha1 entry parses into the map with its sha1 value; nothing is reported
    as an unmatched/untokenized shape. Downstream, check_subject_hash
    rejects the value naming the algorithm (pinned at
    :test_non_sha512_integrity_is_rejected_naming_the_algorithm). This test
    fails if that parse outcome changes.
    """
    body = (
        "packages:\n\n"
        "  'legacy-pkg@1.0.0':\n"
        "    resolution: {integrity: sha1-AAAABBBB==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.find_untokenized_package_keys(body) == []
    assert vp.parse_lockfile(path) == {("legacy-pkg", "1.0.0"): "sha1-AAAABBBB=="}


def test_quoted_key_with_colon_parses_into_the_map(vp: Any, tmp_path: Path) -> None:
    """Pinned observation: a quoted scalar carrying `:` parses, not reported.

    Replacement for the conditional test_quoted_key_with_colon_is_parsed_or_
    reported (:359, left in place above). Observed on HEAD: the quoted key
    'weird:name@1.0.0' parses into the map under the name `weird:name`
    (colon included); nothing is reported as an unmatched/untokenized shape.
    This test fails if that parse outcome changes.
    """
    body = (
        "packages:\n\n"
        "  'weird:name@1.0.0':\n"
        "    resolution: {integrity: sha512-AAAABBBB==}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.find_unmatched_package_keys(body) == []
    assert vp.find_untokenized_package_keys(body) == []
    assert vp.parse_lockfile(path) == {("weird:name", "1.0.0"): "sha512-AAAABBBB=="}


def test_short_value_orphan_below_the_material_floor_stays_quiet(
    vp: Any, tmp_path: Path
) -> None:
    """An orphan with a sub-4-char hash value is below the material floor.

    Pins the `_INTEGRITY_VALUE_RE` boundary the advisor flagged: the value
    class requires 4+ base64ish chars after the algorithm dash, so an
    inline orphan carrying `sha512-O==` (3 chars) is not counted as
    material and the parse stays quiet. This is a DOCUMENTED residual
    (see the regex comment), not an endorsement — if the quantifier is
    ever retuned, this test is the conscious-decision marker.
    """
    body = (
        "packages:\n\n"
        "  'good@1.0.0':\n"
        "    resolution: {integrity: sha512-GOOD==}\n"
        "  extra: {resolution: {integrity: sha512-O==}}\n"
    )
    path = _write_lockfile(tmp_path, body)
    assert vp.parse_lockfile(path) == {("good", "1.0.0"): "sha512-GOOD=="}


def test_orphan_inside_a_snapshots_block_is_refused(vp: Any, tmp_path: Path) -> None:
    """Orphan material inside a snapshots: block is refused, not silent.

    Pins the composition the advisor's matrix flagged as untested: an
    inline non-package mapping inside a `snapshots:` entry's body is
    invisible to the line-anchored ownership walk AND carries hash-shaped
    material, so the unaccounted classification must catch it — the parse
    refuses naming the offending key, whatever section the block sits in.
    """
    body = (
        "snapshots:\n\n"
        "  'keyv@5.6.0':\n"
        "    dependencies:\n"
        "      meta: 1.0.0\n"
        "  extra: {resolution: {integrity: sha512-ORPHAN==}}\n"
    )
    path = _write_lockfile(tmp_path, body)
    with pytest.raises(RuntimeError) as excinfo:
        vp.parse_lockfile(path)
    assert "extra" in str(excinfo.value)
