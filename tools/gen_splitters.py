#!/usr/bin/env python3
"""Generate the 1:N power-splitter symbols for the PAPER_2pt and SLIDES_3pt
libraries (issue #37).

Three styles share one footprint, port map and set of layers, so any of them can
replace a switch on the canvas without moving a wire:

    SPLITTER1x<N>          smooth fanout  — every output off a single junction
    SPLITTER1x<N>_TREE     smooth binary tree — rounded corporate feed
    SPLITTER1x<N>_TREESQ   the same cascade with square corners

Drawing rules follow the rest of the library:
    <g id="artwork">  body and arms      body 2.7px (matching SP6T), arms 2px
    <g id="leads">    stubs to the ports 2px (PAPER_2pt) / 3px (SLIDES_3pt)
    <g id="ports">    <circle id="port:NAME"> fill #c66, r=3, on the viewBox edge
Outputs sit on a 20pt grid (40pt for 1:2, matching SPDT); the common port is at
x=0 and the viewBox is 80pt wide. Lead endpoints land exactly on the port
coordinates so ComponentItem's lead shortening still finds them.

Run from the repo root:  python3 tools/gen_splitters.py
"""
from __future__ import annotations

import math
from pathlib import Path

# --- fixed geometry -------------------------------------------------------
W = 80.0
BOX_X, BOX_W = 10.0, 60.0
LEAD_IN_X = 10.0     # input lead runs x=0 -> 10, meeting the body outline
LEAD_OUT_X = 70.0    # output leads run x=80 -> 70, meeting the body outline
NODE_X = 20.0        # where the trunk ends and the arms begin

# --- style parameters (settled over rounds one to four) -------------------
FAN_DEPART = 9.0     # control offset leaving the node
FAN_FLAT = 46.0      # arms are horizontal from here out
FAN_LAND = 14.0      # landing control offset; larger is a softer corner
TREE_R = 8.0         # corner radius of the smooth cascade
TREE_SHIFT = 5.0     # points each riser is pulled toward the feed
SQ_RISER = 0.15      # square cascade: riser position within each level's run

FAN_WAYS = (2, 3, 4, 6, 8)
TREE_WAYS = (2, 4, 8)   # a binary cascade only lands on powers of two

LIBRARIES = {
    "PAPER_2pt": ("circuits/µwave/splitters", 2.0),
    "SLIDES_3pt": ("circuits/splitters", 3.0),
}

STYLE = """    <style>
      .st0 {{
        fill: #c66;
      }}

      .st1, .st2, .st3 {{
        fill: none;
        stroke: #000;
        stroke-linecap: butt;
        stroke-linejoin: round;
      }}

      .st1 {{
        stroke-width: 2px;
      }}

      .st2 {{
        stroke-width: {lead}px;
      }}

      .st3 {{
        stroke-width: 2.7px;
      }}
    </style>"""


def f(v: float) -> str:
    return f"{v:g}"


def geom(n: int):
    """Port grid for an n-way splitter. Returns (height, port_ys, centre_y)."""
    pitch = 40.0 if n == 2 else 20.0
    h = pitch * (n - 1) + 60.0
    return h, [30.0 + pitch * i for i in range(n)], h / 2.0


def body(h: float) -> str:
    return (f'<rect class="st3" x="{f(BOX_X)}" y="10" '
            f'width="{f(BOX_W)}" height="{f(h - 20)}" rx="4" ry="4"/>')


def trunk(cy: float) -> str:
    return f'<line class="st1" x1="{f(LEAD_IN_X)}" y1="{f(cy)}" x2="{f(NODE_X)}" y2="{f(cy)}"/>'


def straight(x0: float, y: float, x1: float) -> str:
    return f'<line class="st1" x1="{f(x0)}" y1="{f(y)}" x2="{f(x1)}" y2="{f(y)}"/>'


def rounded(x0: float, y0: float, xr: float, x1: float, y1: float, r: float) -> str:
    """Trunk -> rounded corner -> riser -> rounded corner -> flat run to x1.

    The path may start left of x0 when the corner needs the room; that overlap
    is collinear with the incoming horizontal, so it is invisible, and it is
    what lets a riser move toward the feed without shrinking its radius.
    """
    if abs(y1 - y0) < 0.5:
        return straight(x0, y0, x1)
    s = 1.0 if y1 > y0 else -1.0
    rr = min(r, abs(y1 - y0) / 2.0, xr - LEAD_IN_X, x1 - xr)
    start = max(LEAD_IN_X, min(x0, xr - rr))
    sw1, sw2 = (1, 0) if s > 0 else (0, 1)
    return (f'<path class="st1" d="M{f(start)},{f(y0)} H{f(xr - rr)} '
            f'A{f(rr)},{f(rr)} 0 0 {sw1} {f(xr)},{f(y0 + s * rr)} '
            f'V{f(y1 - s * rr)} '
            f'A{f(rr)},{f(rr)} 0 0 {sw2} {f(xr + rr)},{f(y1)} H{f(x1)}"/>')


def levels(n: int, ys: list[float]):
    """Yield (x_parent, y_parent, x_child, y_low, y_high) for a binary cascade."""
    depth = int(round(math.log2(n)))
    xs = [NODE_X + (LEAD_OUT_X - NODE_X) * i / depth for i in range(depth + 1)]
    for lv in range(depth):
        group = n >> lv
        for j in range(1 << lv):
            seg = ys[j * group:(j + 1) * group]
            lo, hi = seg[:group // 2], seg[group // 2:]
            yield (xs[lv], sum(seg) / len(seg), xs[lv + 1],
                   sum(lo) / len(lo), sum(hi) / len(hi))


# --- the three styles -----------------------------------------------------

def art_fan(n, h, ys, cy):
    """Each arm sweeps off the node, flattens at FAN_FLAT, runs straight out."""
    out = [body(h), trunk(cy)]
    for y in ys:
        if abs(y - cy) < 0.5:
            out.append(straight(NODE_X, cy, LEAD_OUT_X))
            continue
        out.append(f'<path class="st1" d="M{f(NODE_X)},{f(cy)} '
                   f'C{f(NODE_X + FAN_DEPART)},{f(cy)} '
                   f'{f(FAN_FLAT - FAN_LAND)},{f(y)} {f(FAN_FLAT)},{f(y)} '
                   f'H{f(LEAD_OUT_X)}"/>')
    return out


def art_tree(n, h, ys, cy):
    out = [body(h), trunk(cy)]
    for xa, ya, xb, yl, yh in levels(n, ys):
        xr = xa + TREE_R + 1.0 - TREE_SHIFT
        out += [rounded(xa, ya, xr, xb, yc, TREE_R) for yc in (yl, yh)]
    return out


def art_treesq(n, h, ys, cy):
    """Square cascade. Each branch is a single path (trunk, riser, run) so the
    corners are line joins; drawn as separate lines they would notch at every
    turn now that the caps are butt."""
    out = [body(h), trunk(cy)]
    for xa, ya, xb, yl, yh in levels(n, ys):
        xm = xa + (xb - xa) * SQ_RISER
        for yc in (yl, yh):
            out.append(f'<path class="st1" d="M{f(xa)},{f(ya)} H{f(xm)} '
                       f'V{f(yc)} H{f(xb)}"/>')
    return out


STYLES = {
    "": (art_fan, FAN_WAYS),
    "_TREE": (art_tree, TREE_WAYS),
    "_TREESQ": (art_treesq, TREE_WAYS),
}


def build(art_fn, n: int, lead_w: float) -> str:
    h, ys, cy = geom(n)
    art = "\n    ".join(art_fn(n, h, ys, cy))

    leads = [f'<line id="lead:com" class="st2" x1="0" y1="{f(cy)}" '
             f'x2="{f(LEAD_IN_X)}" y2="{f(cy)}"/>']
    ports = [f'<circle id="port:com" class="st0" cx="0" cy="{f(cy)}" r="3"/>']
    for i, y in enumerate(ys, start=1):
        leads.append(f'<line id="lead:{i}" class="st2" x1="{f(W)}" y1="{f(y)}" '
                     f'x2="{f(LEAD_OUT_X)}" y2="{f(y)}"/>')
        ports.append(f'<circle id="port:{i}" class="st0" cx="{f(W)}" cy="{f(y)}" r="3"/>')

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'viewBox="0 0 {f(W)} {f(h)}">\n'
        '  <defs>\n' + STYLE.format(lead=f(lead_w)) + '\n  </defs>\n'
        f'  <g id="artwork">\n    {art}\n  </g>\n'
        '  <g id="leads">\n    ' + "\n    ".join(leads) + '\n  </g>\n'
        '  <g id="ports">\n    ' + "\n    ".join(ports) + '\n  </g>\n'
        '</svg>\n')


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "src/diagrammer/components"
    written = 0
    for lib, (subdir, lead_w) in LIBRARIES.items():
        out = root / lib / subdir
        out.mkdir(parents=True, exist_ok=True)
        for suffix, (art_fn, ways) in STYLES.items():
            for n in ways:
                path = out / f"SPLITTER1x{n}{suffix}.svg"
                path.write_text(build(art_fn, n, lead_w))
                written += 1
        print(f"{lib}: {len(FAN_WAYS) + 2 * len(TREE_WAYS)} symbols -> {subdir}")
    print(f"{written} files written")


if __name__ == "__main__":
    main()
