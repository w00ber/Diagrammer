#!/usr/bin/env python3
"""Generate the "v2" 1:N power-splitter symbols.

The companion to `tools/gen_switches_v2.py`, and the same three changes:

* outputs on a **40 pt** pitch, every port coordinate a multiple of 20, so the
  part lands on a 20 pt snap grid.  (v1 puts them at y=30, 50, 70, ... -- a
  20 pt pitch offset by 10, which only works on a 10 pt grid.)
* the wiring *inside* the box drawn at 4 pt with a small gap at **both** ends,
  where it meets the box outline -- the `90degHYBRID1` treatment, whose 4 pt
  traces run x=20 to x=120 inside a box spanning 17.7 to 122.4
* the outline dropped to 2 pt so the signal path reads above the box, and the
  viewBox widened to 140 pt so the interior has room for it

Note that 140 does NOT match `SPDTv2`/`SP6Tv2` at 100, so v1's "any of these can
replace a switch without moving a wire" no longer holds across the two families.
See the WIDTH comment for why the fanouts need the extra run.

The three styles, port map and layer set are unchanged from v1:

    SPLITTER1x<N>v2          smooth fanout -- every output off a single junction
    SPLITTER1x<N>_TREEv2     smooth binary tree -- rounded corporate feed
    SPLITTER1x<N>_TREESQv2   the same cascade with square corners

Everything tuned is a constant below.  Usage:

    python tools/gen_splitters_v2.py --out src/diagrammer/components
    python tools/gen_splitters_v2.py --out /tmp/mock --flat --width 180
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

# --- footprint -------------------------------------------------------------
WIDTH = 140.0         # multiple of 20, so both port columns sit on the grid.
                      # Wider than SPDTv2/SP6Tv2 (100) on purpose: a fanout
                      # spreads vertically, so at 40 pt pitch the 1x6 and 1x8
                      # outer arms go near-vertical and merge at 4 pt unless
                      # the interior run grows with them.  The two v2 families
                      # therefore do NOT share a footprint, unlike v1.
# ...but only the tall ones need that run.  A 1x2 spans 40 pt vertically and a
# 1x3 spans 80, so at 140 they read as empty boxes with a short kink in the
# middle.  These overrides keep each in proportion to its own height; anything
# not listed uses WIDTH.  Multiples of 20, like WIDTH, so the ports stay on grid.
WIDTH_BY_WAYS = {
    2: 80.0,          # 80x80 square; also v1's footprint, and the narrowest
                      # the fanout's S-curve fits in without pinching
    3: 100.0,         # matches SPDTv2/SP6Tv2 exactly
}
INSET = 10.0          # body rect inset from the viewBox on all four sides
CORNER = 4.0          # body rx/ry

PITCH = 40.0          # vertical pitch of the output ports (v1 used 20, or 40
                      # for 1:2 only)
MARGIN = 20.0         # viewBox margin beyond the outermost output port
# => height = PITCH*(N-1) + 2*MARGIN, so every port cy and the common cy
#    (= height/2 = 20*N) is a multiple of 20.

# --- interior --------------------------------------------------------------
GAP = 2.5             # clearance between the box outline and the 4 pt interior
                      # wiring, at both ends.  90degHYBRID1 uses 2.3.
NODE_IN = 12.0        # how far past the interior's left end the trunk runs
                      # before the arms begin.  v1's node sat 10 pt inside the
                      # body outline; this is the same idea measured from the
                      # gap so it does not drift when GAP changes.

WIRE_W = 4.0          # interior wiring weight -- fixed, as in 90degHYBRID1
BOX_W = 2.0           # body outline; 90degHYBRID1's box, not v1's 2.7

INK = "#000"
PORT_INK = "#c66"     # port marker fill (hidden at render time)

# --- style parameters ------------------------------------------------------
# Carried over from gen_splitters.py and re-tuned for the 40 pt pitch, which
# doubles every arm's vertical travel over a horizontal run that only grew by
# 20 pt.  FAN_FLAT is expressed as a fraction of the interior run rather than
# an absolute x, so it tracks --width.
FAN_DEPART = 9.0      # control offset leaving the node
FAN_FLAT_FRAC = 0.62  # arms are horizontal from this fraction of the interior
FAN_LAND = 18.0       # landing control offset; larger is a softer corner
TREE_R = 10.0         # corner radius of the smooth cascade
TREE_SHIFT = 5.0      # points each riser is pulled toward the feed
SQ_RISER = 0.15       # square cascade: riser position within each level's run

FAN_WAYS = (2, 3, 4, 6, 8)
TREE_WAYS = (2, 4, 8)    # a binary cascade only lands on powers of two

LIBRARIES = {
    "PAPER_2pt": (2.0, "circuits/µwave/splitters"),
    "SLIDES_3pt": (3.0, "circuits/splitters"),
}


def f(v: float) -> str:
    # 3 decimals: the precision the spec asks of Illustrator exports.
    return f"{round(v, 3):g}"


def width_for(n: int, default: float) -> float:
    return WIDTH_BY_WAYS.get(n, default)


def geom(n: int, width: float):
    """Return the measurements every style needs."""
    height = PITCH * (n - 1) + 2 * MARGIN
    ys = [MARGIN + PITCH * i for i in range(n)]
    return height, ys, height / 2.0


def build(art_fn, n: int, lead_w: float, gap: float = GAP, box_w: float = BOX_W,
          width: float = WIDTH) -> str:
    height, ys, cy = geom(n, width)
    x_in = INSET + gap                  # interior wiring starts here
    x_out = width - INSET - gap         # ...and ends here
    node_x = x_in + NODE_IN
    flat_x = x_in + (x_out - x_in) * FAN_FLAT_FRAC

    art = art_fn(n, height, ys, cy, x_in, x_out, node_x, flat_x, width)

    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'viewBox="0 0 {f(width)} {f(height)}">'
    )
    # "--" is illegal inside an XML comment; keep the separator single-dash.
    out.append(f"  <!-- Generated by tools/gen_splitters_v2.py : 1x{n} v2 -->")
    out.append("  <defs>")
    out.append("    <style>")
    # Class names follow the library's Illustrator-exported convention
    # (.st0 ports, .st1 box, .st3 interior wiring, .st5 leads); the numbering
    # is kept aligned with gen_switches_v2.py so the two families read alike.
    out.append(f"      .st0 {{ fill: {PORT_INK}; }}")
    out.append(f"      .st1, .st3, .st5 {{ fill: none; stroke: {INK};"
               " stroke-linecap: butt; stroke-linejoin: round; }")
    out.append(f"      .st1 {{ stroke-width: {f(box_w)}px; }}")
    out.append(f"      .st3 {{ stroke-width: {f(WIRE_W)}px; }}")
    out.append(f"      .st5 {{ stroke-width: {f(lead_w)}px; }}")
    out.append("    </style>")
    out.append("  </defs>")

    out.append('  <g id="artwork">')
    out.append(
        f'    <rect class="st1" x="{f(INSET)}" y="{f(INSET)}" '
        f'width="{f(width - 2 * INSET)}" height="{f(height - 2 * INSET)}" '
        f'rx="{f(CORNER)}" ry="{f(CORNER)}"/>'
    )
    for el in art:
        out.append(f"    {el}")
    out.append("  </g>")

    out.append('  <g id="leads">')
    out.append(f'    <path id="lead:com" class="st5" d="M0,{f(cy)} H{f(INSET)}"/>')
    for i, y in enumerate(ys, start=1):
        out.append(
            f'    <path id="lead:{i}" class="st5" '
            f'd="M{f(width)},{f(y)} H{f(width - INSET)}"/>'
        )
    out.append("  </g>")

    out.append('  <g id="ports">')
    out.append(f'    <circle id="port:com" class="st0" cx="0" cy="{f(cy)}" r="3"/>')
    for i, y in enumerate(ys, start=1):
        out.append(
            f'    <circle id="port:{i}" class="st0" cx="{f(width)}" '
            f'cy="{f(y)}" r="3"/>'
        )
    out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


# --- drawing helpers -------------------------------------------------------

def trunk(x_in: float, node_x: float, cy: float) -> str:
    return f'<path class="st3" d="M{f(x_in)},{f(cy)} H{f(node_x)}"/>'


def straight(x0: float, y: float, x1: float) -> str:
    return f'<path class="st3" d="M{f(x0)},{f(y)} H{f(x1)}"/>'


def rounded(x0: float, y0: float, xr: float, x1: float, y1: float, r: float,
            x_in: float) -> str:
    """Trunk -> rounded corner -> riser -> rounded corner -> flat run to x1.

    The path may start left of x0 when the corner needs the room; that overlap
    is collinear with the incoming horizontal, so it is invisible, and it is
    what lets a riser move toward the feed without shrinking its radius.
    """
    if abs(y1 - y0) < 0.5:
        return straight(x0, y0, x1)
    s = 1.0 if y1 > y0 else -1.0
    rr = min(r, abs(y1 - y0) / 2.0, xr - x_in, x1 - xr)
    start = max(x_in, min(x0, xr - rr))
    sw1, sw2 = (1, 0) if s > 0 else (0, 1)
    return (f'<path class="st3" d="M{f(start)},{f(y0)} H{f(xr - rr)} '
            f'A{f(rr)},{f(rr)} 0 0 {sw1} {f(xr)},{f(y0 + s * rr)} '
            f'V{f(y1 - s * rr)} '
            f'A{f(rr)},{f(rr)} 0 0 {sw2} {f(xr + rr)},{f(y1)} H{f(x1)}"/>')


def levels(n: int, ys: list[float], node_x: float, x_out: float):
    """Yield (x_parent, y_parent, x_child, y_low, y_high) for a binary cascade."""
    depth = int(round(math.log2(n)))
    xs = [node_x + (x_out - node_x) * i / depth for i in range(depth + 1)]
    for lv in range(depth):
        group = n >> lv
        for j in range(1 << lv):
            seg = ys[j * group:(j + 1) * group]
            lo, hi = seg[:group // 2], seg[group // 2:]
            yield (xs[lv], sum(seg) / len(seg), xs[lv + 1],
                   sum(lo) / len(lo), sum(hi) / len(hi))


# --- the three styles ------------------------------------------------------

def art_fan(n, h, ys, cy, x_in, x_out, node_x, flat_x, width):
    """Each arm sweeps off the node, flattens at flat_x, runs straight out."""
    out = [trunk(x_in, node_x, cy)]
    for y in ys:
        if abs(y - cy) < 0.5:
            out.append(straight(node_x, cy, x_out))
            continue
        out.append(f'<path class="st3" d="M{f(node_x)},{f(cy)} '
                   f'C{f(node_x + FAN_DEPART)},{f(cy)} '
                   f'{f(flat_x - FAN_LAND)},{f(y)} {f(flat_x)},{f(y)} '
                   f'H{f(x_out)}"/>')
    return out


def art_tree(n, h, ys, cy, x_in, x_out, node_x, flat_x, width):
    out = [trunk(x_in, node_x, cy)]
    for xa, ya, xb, yl, yh in levels(n, ys, node_x, x_out):
        xr = xa + TREE_R + 1.0 - TREE_SHIFT
        out += [rounded(xa, ya, xr, xb, yc, TREE_R, x_in) for yc in (yl, yh)]
    return out


def art_treesq(n, h, ys, cy, x_in, x_out, node_x, flat_x, width):
    """Square cascade. Each branch is a single path (trunk, riser, run) so the
    corners are line joins; drawn as separate lines they would notch at every
    turn now that the caps are butt."""
    out = [trunk(x_in, node_x, cy)]
    for xa, ya, xb, yl, yh in levels(n, ys, node_x, x_out):
        xm = xa + (xb - xa) * SQ_RISER
        for yc in (yl, yh):
            out.append(f'<path class="st3" d="M{f(xa)},{f(ya)} H{f(xm)} '
                       f'V{f(yc)} H{f(xb)}"/>')
    return out


STYLES = {
    "": (art_fan, FAN_WAYS),
    "_TREE": (art_tree, TREE_WAYS),
    "_TREESQ": (art_treesq, TREE_WAYS),
}


def main() -> None:
    global FAN_FLAT_FRAC, FAN_LAND
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True,
                    help="component root to write under (library dirs are created)")
    ap.add_argument("--width", type=float, default=WIDTH,
                    help=f"viewBox width in pt for any N without an entry in "
                         f"WIDTH_BY_WAYS, multiple of 20 (default {WIDTH})")
    ap.add_argument("--gap", type=float, default=GAP,
                    help=f"outline/interior clearance in pt (default {GAP})")
    ap.add_argument("--box-weight", type=float, default=BOX_W,
                    help=f"body outline weight in pt (default {BOX_W})")
    ap.add_argument("--fan-flat", type=float, default=FAN_FLAT_FRAC,
                    help=f"fanout flattens at this fraction of the interior "
                         f"run (default {FAN_FLAT_FRAC})")
    ap.add_argument("--fan-land", type=float, default=FAN_LAND,
                    help=f"fanout landing control offset (default {FAN_LAND})")
    ap.add_argument("--suffix", default="",
                    help="appended to each filename stem (for mockup variants)")
    ap.add_argument("--flat", action="store_true",
                    help="write everything into --out directly, no library tree")
    args = ap.parse_args()

    # These two are read by art_fan and build(); assigning the module globals
    # keeps the drawing functions' signatures from growing a tail of knobs.
    FAN_FLAT_FRAC, FAN_LAND = args.fan_flat, args.fan_land

    written = 0
    for lib, (lead_w, sub) in LIBRARIES.items():
        for style, (art_fn, ways) in STYLES.items():
            for n in ways:
                svg = build(art_fn, n, lead_w, gap=args.gap,
                            box_w=args.box_weight,
                            width=width_for(n, args.width))
                stem = f"SPLITTER1x{n}{style}v2{args.suffix}"
                if args.flat:
                    path = args.out / f"{lib}_{stem}.svg"
                else:
                    path = args.out / lib / sub / f"{stem}.svg"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(svg)
                written += 1
    print(f"{written} files written to {args.out}")


if __name__ == "__main__":
    main()
