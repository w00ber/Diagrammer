#!/usr/bin/env python3
"""Validate Diagrammer component SVGs against the application's own parser.

Catches the failures that are invisible in a picture of the symbol but break it
on the canvas: a port that misses the viewBox edge (so wires approach from the
wrong side), a lead endpoint that does not coincide with its port (so lead
shortening silently skips it), a lead weight that disagrees with its library (so
the wire steps in thickness at the join), or an Illustrator wrapper <g> that
hides every named layer from the parser.

The checks themselves now live in ``src/diagrammer/svglint/`` so that the app,
this script and ``scripts/hooks/pre-commit`` share one implementation — and so
the in-app Component Inspector can check a *user's* library folder, which a
script under ``.claude/`` could never reach. This file is the skill's stable
entry point over that engine; the engine imports no PySide6, so it still runs
under a bare ``python3``.

Usage:
    python validate_components.py <file-or-dir> [<file-or-dir> ...]
    python validate_components.py --quiet src/diagrammer/components/PAPER_2pt

Exits non-zero if any check fails, so it can gate a commit.

For the repairs rather than the report, see:
    python -m diagrammer.svglint --help
    python scripts/normalize_editor_svgs.py --help
"""
from __future__ import annotations

import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        if (p / "src" / "diagrammer" / "models" / "component_def.py").is_file():
            return p
    return None


def main() -> int:
    repo = find_repo_root(Path.cwd()) or find_repo_root(Path(__file__).resolve())
    if repo is None:
        print("error: run this from inside the Diagrammer repo", file=sys.stderr)
        return 2
    sys.path.insert(0, str(repo / "src"))

    from diagrammer.svglint.cli import main as engine_main

    return engine_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
