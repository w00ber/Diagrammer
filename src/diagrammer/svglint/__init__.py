"""Component SVG validation and repair.

One engine, three consumers: the in-app Component Inspector, the
``svg-component`` skill script, and ``scripts/hooks/pre-commit``. Keeping it
here rather than in a script is what lets the app check a *user's* library
folder — the tree an editing feature creates, and the one the old script could
never see.

**This package must not import PySide6.** The pre-commit hook may run under a
bare ``python3``, and ``tests/test_svglint_engine.py`` enforces the invariant.

Typical use::

    from diagrammer import svglint

    profile = svglint.profile_for(path)
    findings = svglint.check_file(path, profile=profile)
    fixes = [f for f in svglint.available_fixes(findings) if f.default_on]
    candidate, notes = svglint.apply_fixes(
        path.read_text(), fixes, findings, profile=profile, path=path)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from diagrammer.svglint.batch import FileReport, iter_svgs, scan_paths
from diagrammer.svglint.checks import ALL_CHECKS, run_checks
from diagrammer.svglint.fixes import (
    ALL_FIXES,
    FIXES_BY_ID,
    apply_fixes,
    available_fixes,
)
from diagrammer.svglint.model import (
    SEVERITY_RANK,
    Finding,
    Fix,
    FixContext,
    FixResult,
    LibraryProfile,
    Marker,
    Severity,
)
from diagrammer.svglint.profiles import (
    BUILTIN_PROFILES,
    GENERIC_PROFILE,
    SIDECAR_NAME,
    builtin_profile_name,
    profile_for,
)

__all__ = [
    "ALL_CHECKS", "ALL_FIXES", "BUILTIN_PROFILES", "FIXES_BY_ID",
    "GENERIC_PROFILE", "SEVERITY_RANK", "SIDECAR_NAME", "FileReport",
    "Finding", "Fix", "FixContext", "FixResult", "LibraryProfile", "Marker",
    "Severity", "apply_fixes", "available_fixes", "builtin_profile_name",
    "check_file", "check_text", "iter_svgs", "profile_for", "run_checks",
    "scan_paths",
]


def check_text(text: str, *, path: Path,
               profile: LibraryProfile | None = None,
               overrides: Mapping[str, Mapping] | None = None) -> list[Finding]:
    """Check SVG *text* that may not be what is saved at ``path``.

    This is the entry point the inspector's preview loop uses on every
    checkbox toggle: the candidate is text, and nothing is written to disk.
    """
    if profile is None:
        profile = profile_for(path, overrides=overrides)
    return run_checks(text, path=path, profile=profile)


def check_file(path: Path, *, profile: LibraryProfile | None = None,
               overrides: Mapping[str, Mapping] | None = None) -> list[Finding]:
    """Check the component saved at ``path``."""
    if profile is None:
        profile = profile_for(path, overrides=overrides)
    return run_checks(path.read_text(encoding="utf-8", errors="replace"),
                      path=path, profile=profile)


def default_fixes(findings: Sequence[Finding]) -> list[Fix]:
    """The fixes to pre-tick: available, and safe enough to default on."""
    return [f for f in available_fixes(findings) if f.default_on]
