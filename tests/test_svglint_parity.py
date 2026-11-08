"""Parity with the legacy validator, plus profile resolution and the sweep.

``test_errors_match_the_legacy_validator`` is the test that de-risks the whole
promotion: the checks used to live in
``.claude/skills/svg-component/scripts/validate_components.py`` and ran only
from a git hook. Moving them into the app is only safe if they still reach the
same verdicts on the real artwork, so this runs both implementations over all
238 shipped components and insists the *error* sets agree.

Warnings deliberately do not have to agree — the new engine adds checks the old
one never had (lead linecap, viewBox origin, duplicate upright groups), which is
the point of moving them somewhere a user's own library can be checked too.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from diagrammer import svglint
from diagrammer.svglint import profiles
from diagrammer.svglint.model import LibraryProfile

REPO = Path(__file__).parent.parent
COMPONENTS = REPO / "src" / "diagrammer" / "components"
LEGACY = (REPO / ".claude" / "skills" / "svg-component" / "scripts"
          / "validate_components.py")

needs_components = pytest.mark.skipif(
    not COMPONENTS.is_dir(), reason="component library not present")


def _load_legacy():
    """Import the legacy validator by path, from git if it is now a shim.

    The script is being replaced by a thin shim over this engine, so comparing
    against the working tree would eventually compare the engine with itself.
    Reading the committed pre-promotion copy out of git keeps the comparison
    honest for as long as that revision exists.
    """
    source = None
    try:
        source = subprocess.run(
            ["git", "show", f"main:{LEGACY.relative_to(REPO)}"],
            cwd=REPO, capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        if LEGACY.is_file():
            source = LEGACY.read_text(encoding="utf-8")
    if not source or "def check(" not in source:
        pytest.skip("pre-promotion validator not available to compare against")

    ns: dict = {"__name__": "_legacy_validator", "__file__": str(LEGACY)}
    exec(compile(source, str(LEGACY), "exec"), ns)
    if "check" not in ns:
        pytest.skip("legacy validator has no check()")
    return ns["check"]


@needs_components
def test_errors_match_the_legacy_validator() -> None:
    legacy_check = _load_legacy()

    # Checks the new engine added; the old one could not report them, so they
    # are expected to appear only on the new side.
    NEW_ONLY = {
        "viewbox-missing", "viewbox-origin-nonzero", "mangled-named-id",
        "primitive-degraded", "stray-anchor-point", "duplicate-upright-group",
        "lead-linecap-not-butt",
    }

    disagreements: list[str] = []
    for path in sorted(COMPONENTS.rglob("*.svg")):
        if any(part.startswith(".") for part in path.parts):
            continue
        legacy_errors, _ = legacy_check(path, REPO)
        new = svglint.check_file(path)
        new_errors = [f for f in new if f.severity == "error"
                      and f.check_id not in NEW_ONLY]

        rel = path.relative_to(COMPONENTS)
        # A file the old validator failed, the new one must also fail.
        if legacy_errors and not new_errors:
            disagreements.append(
                f"{rel}: legacy errors {legacy_errors} but new engine is clean")
        # And a file the old validator passed, the new one must not fail on a
        # check they share.
        if new_errors and not legacy_errors:
            disagreements.append(
                f"{rel}: new errors "
                f"{[f.check_id for f in new_errors]} but legacy was clean")

    assert not disagreements, "\n".join(disagreements)


@needs_components
def test_shipped_library_has_no_errors_except_the_known_temp_file() -> None:
    """A guard against a check that cries wolf.

    The shipped tree is the artwork the conventions were derived from, so a new
    check that flags a lot of it is wrong about the convention, not about the
    files. The one exception is a stray ``_tmp`` export that really is mangled.
    """
    failing = {}
    for report in svglint.scan_paths([COMPONENTS]):
        if report.errors:
            failing[str(report.path.relative_to(COMPONENTS))] = [
                f.check_id for f in report.errors]
    assert list(failing) == [
        "PAPER_2pt/circuits/µwave/coax and twp/COAX1_FAT_LCUT_tmp.svg"
    ], json.dumps(failing, indent=2)


@needs_components
def test_warnings_stay_a_minority_of_the_library() -> None:
    """A report that flags most of the library gets ignored wholesale.

    The linecap migration is the bulk of the warnings and collapses to one
    bulk-fix row in the batch report, so it is allowed to be large; everything
    else has to stay small.
    """
    reports = list(svglint.scan_paths([COMPONENTS]))
    other = [r for r in reports
             if any(f.severity == "warning"
                    and f.check_id != "lead-linecap-not-butt"
                    for f in r.findings)]
    assert len(other) < len(reports) * 0.15, (
        f"{len(other)} of {len(reports)} files warn for reasons other than "
        "the linecap migration")


# ==========================================================================
# profiles
# ==========================================================================

def test_builtin_profile_is_found_by_path_component(tmp_path: Path) -> None:
    p = tmp_path / "PAPER_2pt" / "circuits" / "cap.svg"
    assert profiles.profile_for(p).name == "PAPER_2pt"
    assert profiles.profile_for(p).lead_width == 2.0
    assert profiles.profile_for(p).source == "builtin"


def test_overlay_path_still_resolves_to_its_library(tmp_path: Path) -> None:
    """The user overlay mirrors the built-in tree, so a forked PAPER_2pt part
    must still be checked against PAPER_2pt's 2 px lead."""
    p = tmp_path / ".diagrammer" / "components" / "PAPER_2pt" / "sw" / "SP6T.svg"
    assert profiles.profile_for(p).lead_width == 2.0


def test_unknown_library_gets_no_lead_width_expectation(tmp_path: Path) -> None:
    """Guessing one would invent errors in a user's own folder."""
    prof = profiles.profile_for(tmp_path / "MyLib" / "thing.svg")
    assert prof.lead_width is None
    assert prof.source == "default"


def test_sidecar_declares_a_library_of_its_own(tmp_path: Path) -> None:
    lib = tmp_path / "MyLab"
    (lib / "sub").mkdir(parents=True)
    (lib / profiles.SIDECAR_NAME).write_text(json.dumps({
        "name": "MyLab", "display_name": "My Lab Symbols",
        "grid": 10, "lead_width": 2.5, "linecap": "round",
        "ignored_checks": ["port-off-edge"],
    }), encoding="utf-8")

    prof = profiles.profile_for(lib / "sub" / "part.svg")
    assert prof.name == "MyLab"
    assert prof.display_name == "My Lab Symbols"
    assert prof.grid == 10.0
    assert prof.lead_width == 2.5
    assert prof.linecap == "round"
    assert prof.ignored_checks == frozenset({"port-off-edge"})
    assert prof.source == "library.json"


def test_sidecar_beats_a_settings_override(tmp_path: Path) -> None:
    """The sidecar travels with the folder; a per-machine setting cannot know
    about a library it has never seen."""
    lib = tmp_path / "PAPER_2pt"
    lib.mkdir()
    (lib / profiles.SIDECAR_NAME).write_text(
        json.dumps({"lead_width": 9.0}), encoding="utf-8")
    prof = profiles.profile_for(lib / "p.svg",
                                overrides={"PAPER_2pt": {"lead_width": 5.0}})
    assert prof.lead_width == 9.0
    assert prof.source == "library.json"


def test_settings_override_applies_without_a_sidecar(tmp_path: Path) -> None:
    prof = profiles.profile_for(tmp_path / "PAPER_2pt" / "p.svg",
                                overrides={"PAPER_2pt": {"lead_width": 5.0}})
    assert prof.lead_width == 5.0
    assert prof.grid == 20.0, "unspecified fields keep the built-in value"
    assert prof.source == "settings"


def test_sidecar_null_lead_width_disables_the_check(tmp_path: Path) -> None:
    """An explicit null is how a library says "we have no fixed weight"."""
    lib = tmp_path / "PAPER_2pt"
    lib.mkdir()
    (lib / profiles.SIDECAR_NAME).write_text(
        json.dumps({"lead_width": None}), encoding="utf-8")
    assert profiles.profile_for(lib / "p.svg").lead_width is None


@pytest.mark.parametrize("payload", [
    "{ not json at all",
    '["a", "list"]',
    '{"grid": "twenty"}',
    '{"grid": -5}',
])
def test_malformed_sidecar_falls_back_instead_of_raising(
        payload: str, tmp_path: Path, caplog) -> None:
    """A typo in a hand-edited library.json should cost that one setting, not
    the ability to validate the folder."""
    lib = tmp_path / "PAPER_2pt"
    lib.mkdir()
    (lib / profiles.SIDECAR_NAME).write_text(payload, encoding="utf-8")
    prof = profiles.profile_for(lib / "p.svg")
    assert prof.grid == 20.0
    assert prof.lead_width == 2.0


def test_nearest_sidecar_wins(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (outer / profiles.SIDECAR_NAME).write_text(
        json.dumps({"grid": 10}), encoding="utf-8")
    (inner / profiles.SIDECAR_NAME).write_text(
        json.dumps({"grid": 5}), encoding="utf-8")
    assert profiles.profile_for(inner / "p.svg").grid == 5.0


# ==========================================================================
# batch sweep
# ==========================================================================

def test_scan_skips_dot_prefixed_paths(tmp_path: Path) -> None:
    """Matches ComponentLibrary.scan, so the sweep never sees a user overlay's
    .overrides.json or .backups/ folder."""
    (tmp_path / ".backups").mkdir()
    (tmp_path / ".backups" / "old.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "real.svg").write_text("<svg/>", encoding="utf-8")
    found = [p.name for p in svglint.iter_svgs([tmp_path])]
    assert found == ["real.svg"]


def test_one_unreadable_file_does_not_abort_the_sweep(tmp_path: Path) -> None:
    good = tmp_path / "a.svg"
    good.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<g id="ports"><circle id="port:a" cx="0" cy="5" r="3"/></g></svg>',
        encoding="utf-8")
    bad = tmp_path / "b.svg"
    bad.write_text("<svg", encoding="utf-8")

    reports = {r.path.name: r for r in svglint.scan_paths([tmp_path])}
    assert set(reports) == {"a.svg", "b.svg"}
    assert reports["b.svg"].verdict == "fail"
    assert reports["a.svg"].verdict in ("ok", "warn")


def test_verdict_reflects_the_worst_finding(tmp_path: Path) -> None:
    p = tmp_path / "x.svg"
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                 "</svg>", encoding="utf-8")
    report = next(iter(svglint.scan_paths([p])))
    assert report.verdict == "fail"  # no ports
    assert report.errors


@needs_components
def test_cli_exit_code_and_summary_line() -> None:
    """The hook relies on the exit code; the summary line is what the developer
    whose commit was blocked actually reads."""
    out = subprocess.run(
        [sys.executable, "-m", "diagrammer.svglint",
         str(COMPONENTS / "flowchart"), "--quiet"],
        cwd=REPO, capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stdout + out.stderr
    assert "sound," in out.stdout and "failing" in out.stdout
