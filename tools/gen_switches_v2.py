#!/usr/bin/env python3
"""Generate the "v2" multi-throw switch symbols.

The shipped `SPDT` / `SP6T` put their throws on a 20 pt pitch with the outer
ports a few tenths off any grid line (`cy=30.3`, `29.9`, ...), so the part only
lands cleanly on a 10 pt snap grid.  The v2 set is a strict 20 pt-grid family:

* throws on a **40 pt** pitch, every port coordinate a multiple of 20
* the wiring *inside* the box drawn at 4 pt with a small gap where it meets the
  box outline, and the outline dropped to 2 pt so the signal path reads first
  — the `90degHYBRID1` treatment rather than the heavy-outline switch one
* leads span the 10 pt body inset only, so the lead/interior weight change
  lands exactly on the outline

Everything tuned is a constant below.  Usage:

    python tools/gen_switches_v2.py --out src/diagrammer/components
    python tools/gen_switches_v2.py --out /tmp/mock --gap 4 --suffix _gap4

Run with `--list-libraries` to see where each symbol is written.
"""

from __future__ import annotations

import argparse
from pathlib import Path

# --- footprint -------------------------------------------------------------
# circuits/ parts are 80 pt wide, common port at x=0, throws at x=WIDTH, body
# inset 10 pt all round.  v2 widens that: at 40 pt throw pitch the interior has
# to hold a readable 4 pt stub on each side *and* a wiper arm at a switch-like
# angle, and 80 pt leaves them fighting over ~21 pt.  Any multiple of 20 keeps
# both port columns on the 20 pt grid.
WIDTH = 100.0
INSET = 10.0          # body rect inset from the viewBox on all four sides
CORNER = 4.0          # body rx/ry

PITCH = 40.0          # vertical pitch of the throw ports (v1 used 20)
MARGIN = 20.0         # viewBox margin beyond the outermost throw port
# => height = PITCH*(N-1) + 2*MARGIN, so every port cy and the common cy
#    (= height/2 = 20*N) is a multiple of 20.

# --- interior --------------------------------------------------------------
# The pivot and the contact column are placed *from the stub outwards*, so the
# two sides stay symmetric about the body centre and the stub length is the one
# number to tune:
#     pivot_x    = INSET + GAP + STUB + CONTACT_R
#     contact_x  = WIDTH - INSET - GAP - STUB - CONTACT_R
# A longer stub buys interior wiring you can see at the cost of a shorter arm.
STUB = 16.0           # visible length of the 4 pt interior wiring.  With
                      # WIDTH=100 this puts the wiper at 31 degrees, within
                      # half a degree of v1 SPDT's 31.7.
CONTACT_R = 5.0       # contact / pivot circle radius (v1 used 4.5 at 20 pt pitch)

GAP = 2.5             # clearance between the box outline and the 4 pt interior
                      # wiring.  90degHYBRID1 uses 2.3 (outline 17.7, trace 20).

WIRE_W = 4.0          # interior wiring weight -- fixed, as in 90degHYBRID1
ARM_W = 2.0           # wiper arm; v1's weight, so the pole reads as a blade
BOX_W = 2.0           # body outline; 90degHYBRID1's box, not v1's 2.7
CONTACT_W = 2.0       # contact circle outline

INK = "#231f20"       # the switch family's near-black
PORT_INK = "#c66"     # port marker fill (hidden at render time)

# --- libraries -------------------------------------------------------------
# Lead weight is a property of the library; the interior is not.
LIBRARIES = {
    "PAPER_2pt": (2.0, "circuits/µwave/switches"),
    "SLIDES_3pt": (3.0, "circuits/switches"),
    "CRYO_WIRING_6pt": (6.0, "switches"),
}

# Which N ships where -- mirrors the v1 set exactly.
SYMBOLS = {
    "SPDTv2": (2, ("PAPER_2pt", "SLIDES_3pt", "CRYO_WIRING_6pt")),
    "SP6Tv2": (6, ("PAPER_2pt", "SLIDES_3pt")),
}


def throw_ys(n: int) -> list[float]:
    return [MARGIN + i * PITCH for i in range(n)]


def build(n: int, lead_w: float, gap: float = GAP, box_w: float = BOX_W,
          stub: float = STUB, radius: float = CONTACT_R,
          arm_w: float = ARM_W, width: float = WIDTH) -> str:
    height = PITCH * (n - 1) + 2 * MARGIN
    com_y = height / 2.0
    ys = throw_ys(n)
    pivot_x = INSET + gap + stub + radius
    contact_x = width - INSET - gap - stub - radius

    body_y = INSET
    body_h = height - 2 * INSET
    right_edge = width - INSET          # body outline: where leads stop

    # The wiper points at the throw half a pitch below the common, and stops on
    # that contact's circle edge.  Same angle for every N, since the offset is
    # always +PITCH/2.
    tgt_x, tgt_y = contact_x, com_y + PITCH / 2.0
    dx, dy = tgt_x - pivot_x, tgt_y - com_y
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    arm_x1, arm_y1 = pivot_x + radius * ux, com_y + radius * uy
    arm_x2, arm_y2 = tgt_x - radius * ux, tgt_y - radius * uy

    def f(v: float) -> str:
        # 3 decimals: the precision the spec asks of Illustrator exports.
        return f"{round(v, 3):g}"

    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'viewBox="0 0 {f(width)} {f(height)}">'
    )
    # "--" is illegal inside an XML comment; keep the separator single-dash.
    out.append(f"  <!-- Generated by tools/gen_switches_v2.py : SP{n}T v2 -->")
    out.append("  <defs>")
    out.append("    <style>")
    # Class names follow the library's Illustrator-exported convention
    # (.st0 ports, .st1 box, .st2 contacts, .st3 interior wiring, .st4 arm,
    # .st5 leads) so repo tooling that keys off `.stN` keeps working.
    out.append(f"      .st0 {{ fill: {PORT_INK}; }}")
    out.append(f"      .st1, .st2, .st3, .st4, .st5 {{ stroke: {INK};"
               " stroke-linecap: butt; stroke-linejoin: round; }")
    out.append(f"      .st1 {{ fill: none; stroke-width: {f(box_w)}px; }}")
    out.append(f"      .st2 {{ fill: #fff; stroke-width: {f(CONTACT_W)}px; }}")
    out.append(f"      .st3 {{ fill: none; stroke-width: {f(WIRE_W)}px; }}")
    out.append(f"      .st4 {{ fill: none; stroke-width: {f(arm_w)}px; }}")
    out.append(f"      .st5 {{ fill: none; stroke-width: {f(lead_w)}px; }}")
    out.append("    </style>")
    out.append("  </defs>")

    out.append('  <g id="artwork">')
    out.append(
        f'    <rect class="st1" x="{f(INSET)}" y="{f(body_y)}" '
        f'width="{f(width - 2 * INSET)}" height="{f(body_h)}" '
        f'rx="{f(CORNER)}" ry="{f(CORNER)}"/>'
    )
    # Interior wiring: a stub from just inside the outline to each contact.
    out.append(
        f'    <path class="st3" d="M{f(INSET + gap)},{f(com_y)} '
        f'H{f(pivot_x - radius)}"/>'
    )
    for y in ys:
        out.append(
            f'    <path class="st3" d="M{f(right_edge - gap)},{f(y)} '
            f'H{f(contact_x + radius)}"/>'
        )
    # Wiper arm, then the circles on top so the butt ends are covered.
    out.append(
        f'    <path class="st4" d="M{f(arm_x1)},{f(arm_y1)} '
        f'L{f(arm_x2)},{f(arm_y2)}"/>'
    )
    out.append(
        f'    <circle class="st2" cx="{f(pivot_x)}" cy="{f(com_y)}" '
        f'r="{f(radius)}"/>'
    )
    for y in ys:
        out.append(
            f'    <circle class="st2" cx="{f(contact_x)}" cy="{f(y)}" '
            f'r="{f(radius)}"/>'
        )
    out.append("  </g>")

    out.append('  <g id="leads">')
    out.append(f'    <path id="lead:com" class="st5" d="M0,{f(com_y)} H{f(INSET)}"/>')
    for i, y in enumerate(ys, start=1):
        out.append(
            f'    <path id="lead:{i}" class="st5" '
            f'd="M{f(width)},{f(y)} H{f(right_edge)}"/>'
        )
    out.append("  </g>")

    out.append('  <g id="ports">')
    out.append(f'    <circle id="port:com" class="st0" cx="0" cy="{f(com_y)}" r="3"/>')
    for i, y in enumerate(ys, start=1):
        out.append(
            f'    <circle id="port:{i}" class="st0" cx="{f(width)}" '
            f'cy="{f(y)}" r="3"/>'
        )
    out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True,
                    help="component root to write under (library dirs are created)")
    ap.add_argument("--gap", type=float, default=GAP,
                    help=f"outline/interior clearance in pt (default {GAP})")
    ap.add_argument("--box-weight", type=float, default=BOX_W,
                    help=f"body outline weight in pt (default {BOX_W})")
    ap.add_argument("--width", type=float, default=WIDTH,
                    help=f"viewBox width in pt, multiple of 20 (default {WIDTH})")
    ap.add_argument("--stub", type=float, default=STUB,
                    help=f"visible interior wiring length in pt (default {STUB})")
    ap.add_argument("--radius", type=float, default=CONTACT_R,
                    help=f"contact circle radius in pt (default {CONTACT_R})")
    ap.add_argument("--arm-weight", type=float, default=ARM_W,
                    help=f"wiper arm weight in pt (default {ARM_W})")
    ap.add_argument("--suffix", default="",
                    help="appended to each filename stem (for mockup variants)")
    ap.add_argument("--flat", action="store_true",
                    help="write everything into --out directly, no library tree")
    ap.add_argument("--list-libraries", action="store_true")
    args = ap.parse_args()

    if args.list_libraries:
        for name, (n, libs) in SYMBOLS.items():
            for lib in libs:
                lead_w, sub = LIBRARIES[lib]
                print(f"{lib}/{sub}/{name}.svg  (N={n}, lead {lead_w}px)")
        return

    for name, (n, libs) in SYMBOLS.items():
        for lib in libs:
            lead_w, sub = LIBRARIES[lib]
            svg = build(n, lead_w, gap=args.gap, box_w=args.box_weight,
                        stub=args.stub, radius=args.radius,
                        arm_w=args.arm_weight, width=args.width)
            stem = f"{name}{args.suffix}"
            if args.flat:
                path = args.out / f"{lib}_{stem}.svg"
            else:
                path = args.out / lib / sub / f"{stem}.svg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(svg)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
