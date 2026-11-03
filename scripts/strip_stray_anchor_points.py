#!/usr/bin/env python3
"""Remove Illustrator's stray anchor points from component SVGs.

Illustrator's SVG export leaves a path behind wherever an anchor point
outlived the shape it belonged to::

    <path class="st0" d="M40.1,24.1"/>

A ``d`` with a single moveto and no drawing command has no geometry. Qt
renders it as nothing (butt linecap) or a sub-pixel speck (round linecap),
so it goes unnoticed on the canvas — but it survives into every SVG export
and clipboard paste, where a vector editor turns it into a visible stray
anchor point.

The app strips these at render time as well
(``ComponentItem._strip_degenerate_paths``), which covers user library
folders this repo cannot reach. This script cleans the shipped files so the
data itself is right.

Usage::

    python scripts/strip_stray_anchor_points.py --dry-run
    python scripts/strip_stray_anchor_points.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "src" / "diagrammer" / "components"

DRAWING_COMMANDS = set("LlHhVvCcSsQqTtAa")

# A whole <path .../> element, captured with the leading whitespace so the
# line it sat on goes with it.
PATH_ELEMENT = re.compile(r'[ \t]*<path\b[^>]*/>\n?')
D_ATTR = re.compile(r'\bd="([^"]*)"')


def is_stray(tag: str) -> bool:
    """True when *tag* is a <path> whose ``d`` draws nothing."""
    m = D_ATTR.search(tag)
    if m is None:
        return False
    if "id=" in tag:
        # Ports, leads and stretch markers are addressed by id; never touch
        # one even if its geometry looks degenerate.
        return False
    return not any(c in DRAWING_COMMANDS for c in m.group(1))


def strip(text: str) -> tuple[str, int]:
    """Return *text* without stray anchor paths, and how many were removed."""
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        if is_stray(m.group(0)):
            count += 1
            return ""
        return m.group(0)

    return PATH_ELEMENT.sub(_sub, text), count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT,
                    help="directory to sweep (default: the shipped component tree)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args(argv)

    files = sorted(args.root.rglob("*.svg"))
    if not files:
        print(f"no SVGs under {args.root}", file=sys.stderr)
        return 1

    total = 0
    touched = 0
    for path in files:
        text = path.read_text()
        cleaned, n = strip(text)
        if not n:
            continue
        total += n
        touched += 1
        print(f"{n:3d}  {path.relative_to(args.root)}")
        if not args.dry_run:
            path.write_text(cleaned)

    verb = "would remove" if args.dry_run else "removed"
    print(f"\n{verb} {total} stray anchor point(s) in {touched} of {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
