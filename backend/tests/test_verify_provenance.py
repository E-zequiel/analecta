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
