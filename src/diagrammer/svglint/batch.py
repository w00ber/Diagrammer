"""Sweep a tree of components and report every file's findings."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from diagrammer.svglint.checks import run_checks
from diagrammer.svglint.model import Finding, LibraryProfile
from diagrammer.svglint.profiles import profile_for


@dataclass(frozen=True)
class FileReport:
    path: Path
    profile: LibraryProfile
    findings: tuple[Finding, ...] = ()
    #: set when the file could not even be read off disk, which is distinct
    #: from a ``parse-failure`` finding (bad XML in a file we did read)
    read_error: str | None = None

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def verdict(self) -> str:
        """``"fail"`` | ``"warn"`` | ``"ok"`` — what the CLI prints per file."""
        if self.read_error or self.errors:
            return "fail"
        return "warn" if self.warnings else "ok"


def iter_svgs(targets: Iterable[Path]) -> list[Path]:
    """Expand files and directories to a sorted list of component SVGs.

    Skips dot-prefixed path parts, which is what ``ComponentLibrary.scan``
    does — so the sweep sees exactly the files the app would load, and never
    the override index or the backup folder inside a user's overlay.
    """
    out: list[Path] = []
    for target in targets:
        if target.is_dir():
            out.extend(sorted(target.rglob("*.svg")))
        elif target.is_file():
            out.append(target)
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        if any(part.startswith(".") for part in p.parts):
            continue
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


def scan_paths(targets: Iterable[Path], *,
               overrides: Mapping[str, Mapping] | None = None
               ) -> Iterator[FileReport]:
    """Check every component under ``targets``, yielding one report per file.

    A generator so the GUI can show progress and so one unreadable file never
    aborts the sweep — the report carries the error instead of raising.
    """
    for path in iter_svgs(targets):
        profile = profile_for(path, overrides=overrides)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            yield FileReport(path=path, profile=profile, read_error=str(exc))
            continue
        yield FileReport(path=path, profile=profile,
                         findings=tuple(run_checks(text, path=path,
                                                   profile=profile)))
