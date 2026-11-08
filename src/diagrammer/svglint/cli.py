"""Command-line front end: ``python -m diagrammer.svglint <paths>``.

The output format is fixed by ``scripts/hooks/pre-commit``, which greps nothing
but does rely on the exit code and shows this text to whoever's commit was
blocked. It is the same format the skill script printed before the checks moved
in here, so a hook, a CI log and a developer's terminal all read as they did.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from diagrammer.svglint.batch import scan_paths


def find_repo_root(start: Path) -> Path | None:
    """Walk up for the Diagrammer checkout, to print short relative paths."""
    for p in [start, *start.parents]:
        if (p / "src" / "diagrammer" / "models" / "component_def.py").is_file():
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m diagrammer.svglint",
        description=("Validate Diagrammer component SVGs against the "
                     "application's own parser and the host library's "
                     "conventions."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--quiet", action="store_true",
                    help="only print components that have problems")
    ap.add_argument("--errors-only", action="store_true",
                    help="exit non-zero on errors but say nothing about warnings")
    args = ap.parse_args(argv)

    repo = find_repo_root(Path.cwd())
    if repo is None and args.paths:
        repo = find_repo_root(args.paths[0].resolve())

    def rel(f: Path) -> str:
        if repo is None:
            return str(f)
        try:
            return str(f.resolve().relative_to(repo))
        except ValueError:
            return str(f)

    bad = warned = total = 0
    for report in scan_paths(args.paths):
        total += 1
        verdict = report.verdict
        if verdict == "fail":
            bad += 1
            print(f"FAIL {rel(report.path)}")
        elif verdict == "warn":
            warned += 1
            if not (args.quiet or args.errors_only):
                print(f"warn {rel(report.path)}")
        elif not args.quiet:
            print(f"ok   {rel(report.path)}")

        if report.read_error:
            print(f"       error: cannot read: {report.read_error}")
        for f in report.errors:
            print(f"       error: {f.message}")
        if verdict == "fail" or not (args.quiet or args.errors_only):
            for f in report.warnings:
                print(f"       warn:  {f.message}")

    ok = total - bad - warned
    print(f"\n{ok} sound, {warned} with warnings, {bad} failing "
          f"(of {total} components)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
