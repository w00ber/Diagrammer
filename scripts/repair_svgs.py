#!/usr/bin/env python3
"""Repair malformed SVGs that carry duplicate attributes (e.g. a double
``xmlns="http://www.w3.org/2000/svg"`` on the root ``<svg>``).

Such files are rejected by Python's strict ``xml.etree`` parser even though
Inkscape/Illustrator open them fine. This script de-duplicates the offending
attributes in place, preserving everything else.

Usage:
    python scripts/repair_svgs.py <file-or-dir> [<file-or-dir> ...] [--dry-run]

Reuses ``diagrammer.io.svg_parse`` so the repair logic stays in one place.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running straight from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from diagrammer.io.svg_parse import normalize_svg_text, repair_svg_file  # noqa: E402


def _iter_svgs(target: Path):
    if target.is_dir():
        yield from sorted(target.rglob("*.svg"))
    elif target.is_file():
        yield target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path, help="SVG files or directories to repair")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args(argv)

    changed = 0
    scanned = 0
    for target in args.paths:
        for svg in _iter_svgs(target):
            scanned += 1
            if args.dry_run:
                _, would_change = normalize_svg_text(svg.read_text(encoding="utf-8"))
                if would_change:
                    changed += 1
                    print(f"would repair: {svg}")
            else:
                if repair_svg_file(svg):
                    changed += 1
                    print(f"repaired: {svg}")

    verb = "would be repaired" if args.dry_run else "repaired"
    print(f"\n{scanned} file(s) scanned, {changed} {verb}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
