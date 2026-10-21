#!/usr/bin/env python3
"""Validate Diagrammer component SVGs against the application's own parser.

Catches the failures that are invisible in a picture of the symbol but break it
on the canvas: a port that misses the viewBox edge (so wires approach from the
wrong side), a lead endpoint that does not coincide with its port (so lead
shortening silently skips it), a lead weight that disagrees with its library (so
the wire steps in thickness at the join), or an Illustrator wrapper <g> that
hides every named layer from the parser.

Usage:
    python validate_components.py <file-or-dir> [<file-or-dir> ...]
    python validate_components.py --quiet src/diagrammer/components/PAPER_2pt

Exits non-zero if any check fails, so it can gate a commit.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Lead weight each shipped library draws at; see the spec's House Conventions.
LIBRARY_LEAD_WEIGHT = {
    "PAPER_2pt": 2.0,
    "SLIDES_3pt": 3.0,
    "CRYO_WIRING_6pt": 6.0,
}
EDGE_TOL = 2.0      # the app's own port/edge detection tolerance
ENDPOINT_TOL = 2.0  # _find_port_at matches within distance^2 < 4.0


def find_repo_root(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        if (p / "src" / "diagrammer" / "models" / "component_def.py").is_file():
            return p
    return None


def css_widths(svg_text: str) -> dict[str, float]:
    """Map CSS class name -> stroke-width, the way the app resolves it."""
    out: dict[str, float] = {}
    for block in re.finditer(r"<style[^>]*>(.*?)</style>", svg_text, re.S):
        for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", block.group(1)):
            m = re.search(r"stroke-width\s*:\s*([\d.]+)", rule.group(2))
            if not m:
                continue
            for cls in re.finditer(r"\.([\w-]+)", rule.group(1)):
                out[cls.group(1)] = float(m.group(1))
    return out


def lead_width(svg_text: str) -> float | None:
    """Stroke width of the first element in a lead layer, as the app reads it."""
    widths = css_widths(svg_text)
    g = re.search(r'<g id="leads(?:-left|-right|-top|-bottom)?"[^>]*>(.*?)</g>',
                  svg_text, re.S)
    if not g:
        return None
    for m in re.finditer(r"<(?:line|path|polyline)\b([^>]*)>", g.group(1)):
        attrs = m.group(1)
        inline = re.search(r"stroke-width\s*:\s*([\d.]+)", attrs)
        if inline:
            return float(inline.group(1))
        direct = re.search(r'stroke-width="([\d.]+)"', attrs)
        if direct:
            return float(direct.group(1))
        cls = re.search(r'class="([\w-]+)"', attrs)
        if cls and cls.group(1) in widths:
            return widths[cls.group(1)]
    return None


def _simple_path_ends(d: str) -> tuple[float, float, float, float] | None:
    """Start and end of a path built only from absolute M/L/H/V.

    Anything with curves, arcs or relative commands is returned as None rather
    than guessed at: a wrong endpoint here would be a confident false alarm,
    which is worse than staying quiet.
    """
    tokens = re.findall(r"([MLHV])([^MLHVmlhvCcSsQqTtAaZz]*)", d)
    if not tokens or len(re.findall(r"[A-Za-z]", d)) != len(tokens):
        return None
    x = y = None
    start = None
    for cmd, args in tokens:
        nums = [float(v) for v in re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", args)]
        if cmd == "M":
            if len(nums) < 2:
                return None
            x, y = nums[0], nums[1]
            start = (x, y)
            nums = nums[2:]
            for i in range(0, len(nums) - 1, 2):
                x, y = nums[i], nums[i + 1]
        elif cmd == "L":
            for i in range(0, len(nums) - 1, 2):
                x, y = nums[i], nums[i + 1]
        elif cmd == "H":
            if nums:
                x = nums[-1]
        elif cmd == "V":
            if nums:
                y = nums[-1]
    if start is None or x is None or y is None:
        return None
    return (start[0], start[1], x, y)


def lead_endpoints(svg_text: str) -> list[tuple[float, float, float, float]]:
    """(x1, y1, x2, y2) for every lead segment whose ends we can read exactly."""
    pts = []
    for g in re.finditer(r'<g id="leads(?:-left|-right|-top|-bottom)?"[^>]*>(.*?)</g>',
                         svg_text, re.S):
        for m in re.finditer(r"<line\b([^>]*)>", g.group(1)):
            a = m.group(1)
            got = {k: re.search(rf'\b{k}="([-\d.]+)"', a) for k in ("x1", "y1", "x2", "y2")}
            # omitted coordinates default to 0, which is how the library writes
            # edge leads (e.g. x2 absent means x2=0)
            pts.append(tuple(float(got[k].group(1)) if got[k] else 0.0
                             for k in ("x1", "y1", "x2", "y2")))
        for m in re.finditer(r'<path\b[^>]*\bd="([^"]+)"', g.group(1)):
            ends = _simple_path_ends(m.group(1))
            if ends:
                pts.append(ends)
    return pts


def check(path: Path, repo: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors misbehave on the canvas; warnings may
    be deliberate (a centred port on a terminal, a component drawn without an
    explicit artwork layer) and only need a human glance."""
    problems: list[str] = []
    warnings: list[str] = []
    text = path.read_text(errors="ignore")

    sys.path.insert(0, str(repo / "src"))
    from diagrammer.models.component_def import ComponentDef  # noqa: E402

    try:
        cdef = ComponentDef.from_svg(path)
    except Exception as exc:  # the app would log and skip this file
        return [f"does not parse: {exc}"], []

    if re.search(r'<g\s+id="Layer_1"', text):
        problems.append('wrapped in <g id="Layer_1"> — named layers must be '
                        'direct children of <svg> (Illustrator export)')

    if not re.search(r'<g id="artwork">', text):
        warnings.append('no <g id="artwork"> layer — the whole SVG is treated '
                        'as artwork, which is legal but usually unintended')

    if not cdef.ports and not cdef.decorative:
        problems.append("no ports, and not marked decorative")

    w, h = cdef.width, cdef.height
    for p in cdef.ports:
        on_edge = (abs(p.x) <= EDGE_TOL or abs(p.x - w) <= EDGE_TOL
                   or abs(p.y) <= EDGE_TOL or abs(p.y - h) <= EDGE_TOL)
        if not on_edge:
            warnings.append(
                f"port '{p.name}' at ({p.x:g},{p.y:g}) is not on a viewBox edge "
                f"(0..{w:g} x 0..{h:g}) — approach direction is inferred from "
                f"position, so check wires attach from the side you expect")

    # Lead shortening only fires when an endpoint coincides with a port.
    if re.search(r'<g id="leads', text):
        ports = [(p.x, p.y) for p in cdef.ports]
        for x1, y1, x2, y2 in lead_endpoints(text):
            hit = any(
                min((x1 - px) ** 2 + (y1 - py) ** 2,
                    (x2 - px) ** 2 + (y2 - py) ** 2) < ENDPOINT_TOL ** 2
                for px, py in ports)
            if not hit:
                problems.append(
                    f"lead ({x1:g},{y1:g})-({x2:g},{y2:g}) has no endpoint on a "
                    f"port — it will never be shortened")

        lw = lead_width(text)
        if lw is None:
            problems.append("lead layer present but its stroke width is "
                            "unresolvable — the attached wire cannot match it")
        else:
            lib = next((part for part in path.parts if part in LIBRARY_LEAD_WEIGHT), None)
            if lib and abs(lw - LIBRARY_LEAD_WEIGHT[lib]) > 0.01:
                problems.append(
                    f"lead width {lw:g}px but {lib} draws leads at "
                    f"{LIBRARY_LEAD_WEIGHT[lib]:g}px — wires will step in thickness")

    return problems, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--quiet", action="store_true",
                    help="only print components that have problems")
    args = ap.parse_args()

    repo = find_repo_root(Path.cwd()) or find_repo_root(args.paths[0].resolve())
    if repo is None:
        print("error: run this from inside the Diagrammer repo", file=sys.stderr)
        return 2

    files: list[Path] = []
    for p in args.paths:
        files.extend(sorted(p.rglob("*.svg")) if p.is_dir() else [p])
    files = [f for f in files if not any(s.startswith(".") for s in f.parts)]

    def rel(f: Path) -> str:
        full = f.resolve()
        try:
            return str(full.relative_to(repo))
        except ValueError:
            return str(f)

    bad = warned = 0
    for f in files:
        problems, warnings = check(f, repo)
        if problems:
            bad += 1
            print(f"FAIL {rel(f)}")
        elif warnings:
            warned += 1
            if not args.quiet:
                print(f"warn {rel(f)}")
        elif not args.quiet:
            print(f"ok   {rel(f)}")
        for pr in problems:
            print(f"       error: {pr}")
        if problems or not args.quiet:
            for wn in warnings:
                print(f"       warn:  {wn}")

    ok = len(files) - bad - warned
    print(f"\n{ok} sound, {warned} with warnings, {bad} failing "
          f"(of {len(files)} components)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
