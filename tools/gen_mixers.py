#!/usr/bin/env python3
"""Generate the IQ mixer symbol for the PAPER_2pt and SLIDES_3pt libraries.

The symbol is the standard mixer mark — a circle with an X — drawn bare, with
no enclosing body. `CIRC1` is the precedent for that in this library: a plain
2 px circle with leads and no body rect.

All four leads meet the circle at a cardinal point, which is how the symbol is
conventionally drawn and which keeps the X's diagonals clear of the leads:

        I
        |
  RF ---O--- LO          (X inside the circle, at 45 deg)
        |
        Q

Port roles are lettered on the symbol itself, because nothing else distinguishes
the RF port from the LO port once the part is on a canvas. The lettering is
baked as outlined glyphs rather than live <text>: no other component under
`circuits/` uses <text>, and an outline renders the same on a machine that does
not have the font. `FILTER_*_LBL` does the same thing.

The lettering sits in `<g id="upright">`, which the renderer compensates so the
text stays readable when the part is flipped or rotated. Each glyph carries a
`data-anchor` giving the point it should stay pinned to.

Two symbols: the IQ mixer (four ports) and the plain mixer (RF/LO/IF). In
CRYO_WIRING_6pt they live under `room temp/`, which is where that library keeps
the parts that sit outside the fridge.

Artwork weight scales with the library, not just the leads: CIRC1 — the
bare-circle precedent — is 2 px in PAPER_2pt and 3 px in SLIDES_3pt.

Run from the repo root:  python3 tools/gen_mixers.py
"""
from __future__ import annotations

import math
from pathlib import Path

from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextPath

# --- geometry -------------------------------------------------------------
R = 24.0              # circle radius
LABEL_SIZE = 11.0
HALO_PAD = 1.0        # white knockout box padding around lettering
LEAD_HALF = 3.0       # half the heaviest library's lead (CRYO, 6 px)
MARGIN = 3.0          # breathing room beyond that
FONT = FontProperties(family=["Helvetica", "Arial", "DejaVu Sans"])

# library -> (subdirectory, lead width, artwork width)
LIBRARIES = {
    "PAPER_2pt": ("circuits/µwave/PASSIVE", 2.0, 2.0),
    "SLIDES_3pt": ("circuits/µwave/PASSIVE", 3.0, 3.0),
    "CRYO_WIRING_6pt": ("room temp", 6.0, 3.0),
}


def _glyph_radius(text: str, anchor: str) -> float:
    """Furthest the glyph reaches from its anchor, in any direction."""
    bb = TextPath((0, 0), text, size=LABEL_SIZE, prop=FONT).get_extents()
    w, h = bb.x1 - bb.x0, bb.y1 - bb.y0
    reach = w / 2 if anchor == "middle" else w
    return math.hypot(reach, h)


def _clearance(text: str, anchor: str) -> float:
    """Perpendicular distance from a lead at which the glyph can never touch it.

    The label stays upright while its offset vector rotates with the component,
    so after a quarter turn the glyph presents a different edge to the lead.
    Sizing the offset by the glyph's largest reach makes the clearance hold in
    every orientation, which a flat offset does not — "LO" is nearly twice as
    wide as it is tall. LEAD_HALF uses the heaviest library (6 px) so one
    geometry serves all three.
    """
    return _glyph_radius(text, anchor) + LEAD_HALF + MARGIN


def _ports(w: float, h: float, cx: float, cy: float, spec: dict) -> dict:
    """Expand a compact port spec into (edge point, circle point, label)."""
    out = {}
    for name, (side, text) in spec.items():
        if side in ("left", "right"):
            d = _clearance(text, "middle")
            sx = -1 if side == "left" else 1
            out[name] = ((0.0 if side == "left" else w, cy), (cx + sx * R, cy),
                         (text, (cx + sx * (R + 18), cy - d), "middle"))
        else:
            d = _clearance(text, "start")
            sy = -1 if side == "top" else 1
            out[name] = ((cx, 0.0 if side == "top" else h), (cx, cy + sy * R),
                         (text, (cx + d, cy + sy * (R + 12) + (14 if side == "bottom" else 0)),
                          "start"))
    return out


# Signal straight through, LO injected from the side; I and Q on the vertical
# axis, which is how the quadrature pair is conventionally drawn.
IQ = dict(name="MIXER_IQ1", w=120.0, h=120.0, cx=60.0, cy=60.0,
          spec={"rf": ("left", "RF"), "lo": ("right", "LO"),
                "i": ("top", "I"), "q": ("bottom", "Q")})

# The plain three-port mixer: RF in, IF out, LO up from below.
PLAIN = dict(name="MIXER1", w=120.0, h=100.0, cx=60.0, cy=40.0,
             spec={"rf": ("left", "RF"), "if": ("right", "IF"),
                   "lo": ("bottom", "LO")})

SYMBOLS = (IQ, PLAIN)

STYLE = """    <style>
      .st0 {{
        fill: #c66;
      }}

      .st1, .st2 {{
        fill: none;
        stroke: #000;
        stroke-linecap: butt;
        stroke-linejoin: round;
      }}

      .st1 {{
        stroke-width: {art}px;
      }}

      .st2 {{
        stroke-width: {lead}px;
      }}

      .st3 {{
        fill: #000;
      }}

      .st4 {{
        fill: #fff;
      }}
    </style>"""


def f(v: float) -> str:
    return f"{v:g}"


def outline(s: str, x: float, y: float, anchor: str = "middle") -> str:
    """Letter `s` as a filled outline path, baselined at (x, y).

    matplotlib hands back y-up coordinates, so the glyph is emitted in its own
    space and flipped by the transform; doing it that way keeps the curve
    commands untouched rather than negating every vertex by hand.
    """
    tp = TextPath((0, 0), s, size=LABEL_SIZE, prop=FONT)
    bb = tp.get_extents()
    width = bb.x1 - bb.x0
    tx = {"middle": x - width / 2 - bb.x0,
          "start": x - bb.x0,
          "end": x - width - bb.x0}[anchor]

    d: list[str] = []
    verts, codes = tp.vertices, tp.codes
    i = 0
    while i < len(verts):
        code = codes[i]
        if code == 1:                                   # MOVETO
            d.append(f"M{verts[i][0]:.2f},{verts[i][1]:.2f}")
            i += 1
        elif code == 2:                                 # LINETO
            d.append(f"L{verts[i][0]:.2f},{verts[i][1]:.2f}")
            i += 1
        elif code == 3:                                 # CURVE3 (quadratic)
            d.append(f"Q{verts[i][0]:.2f},{verts[i][1]:.2f} "
                     f"{verts[i+1][0]:.2f},{verts[i+1][1]:.2f}")
            i += 2
        elif code == 4:                                 # CURVE4 (cubic)
            d.append(f"C{verts[i][0]:.2f},{verts[i][1]:.2f} "
                     f"{verts[i+1][0]:.2f},{verts[i+1][1]:.2f} "
                     f"{verts[i+2][0]:.2f},{verts[i+2][1]:.2f}")
            i += 3
        else:                                           # CLOSEPOLY
            d.append("Z")
            i += 1
    # data-anchor is the point ComponentItem._apply_upright pins the glyph to
    # when the part is flipped or rotated, so the lettering stays readable and
    # still travels with the port it names.
    #
    # A white knockout box goes underneath: placement alone cannot keep a label
    # clear of a wire the user routes past the part.
    #
    # The box is a rectangle, not a stroked copy of the letterforms. A stroke
    # halo follows the glyph outline, so a wire grazing its edge comes away
    # scalloped — it reads as a damaged conductor. A rectangle either cuts the
    # wire cleanly or leaves it alone.
    x0 = tx + bb.x0 - HALO_PAD
    y0 = y - bb.y1 - HALO_PAD
    return (f'<rect class="st4" data-anchor="{x:g},{y:g}" '
            f'x="{x0:.2f}" y="{y0:.2f}" '
            f'width="{width + 2 * HALO_PAD:.2f}" '
            f'height="{bb.y1 - bb.y0 + 2 * HALO_PAD:.2f}"/>\n      '
            f'<path class="st3" data-anchor="{x:g},{y:g}" '
            f'transform="translate({tx:.2f},{y:g}) scale(1,-1)" '
            f'd="{" ".join(d)}"/>')


def artwork(cx: float, cy: float, ports: dict) -> list[str]:
    diag = R * math.cos(math.radians(45))
    out = [
        f'<circle class="st1" cx="{f(cx)}" cy="{f(cy)}" r="{f(R)}"/>',
        f'<line class="st1" x1="{f(cx-diag)}" y1="{f(cy-diag)}" '
        f'x2="{f(cx+diag)}" y2="{f(cy+diag)}"/>',
        f'<line class="st1" x1="{f(cx-diag)}" y1="{f(cy+diag)}" '
        f'x2="{f(cx+diag)}" y2="{f(cy-diag)}"/>',
    ]
    return out


def lettering(ports: dict) -> str:
    """The `upright` layer: a top-level group, so it paints after `leads`.

    Order matters. Inside `artwork` the lettering would be drawn before the
    leads and the white halo would knock out nothing — the lead would simply
    paint over the text.
    """
    letters = [outline(text, lx, ly, anchor)
               for _name, (_edge, _start, (text, (lx, ly), anchor)) in ports.items()]
    return '<g id="upright">\n    ' + "\n    ".join(letters) + '\n  </g>' 


def build(sym: dict, lead_w: float, art_w: float) -> str:
    W, H, cx, cy = sym["w"], sym["h"], sym["cx"], sym["cy"]
    PORTS = _ports(W, H, cx, cy, sym["spec"])
    leads, ports = [], []
    for name, (edge, start, _lbl) in PORTS.items():
        leads.append(f'<line id="lead:{name}" class="st2" '
                     f'x1="{f(edge[0])}" y1="{f(edge[1])}" '
                     f'x2="{f(start[0])}" y2="{f(start[1])}"/>')
        ports.append(f'<circle id="port:{name}" class="st0" '
                     f'cx="{f(edge[0])}" cy="{f(edge[1])}" r="3"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'viewBox="0 0 {f(W)} {f(H)}">\n'
        '  <defs>\n' + STYLE.format(art=f(art_w), lead=f(lead_w)) + '\n  </defs>\n'
        '  <g id="artwork">\n    ' + "\n    ".join(artwork(cx, cy, PORTS)) + '\n  </g>\n'
        '  <g id="leads">\n    ' + "\n    ".join(leads) + '\n  </g>\n'
        '  ' + lettering(PORTS) + '\n'
        '  <g id="ports">\n    ' + "\n    ".join(ports) + '\n  </g>\n'
        '</svg>\n')


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "src/diagrammer/components"
    for lib, (subdir, lead_w, art_w) in LIBRARIES.items():
        out = root / lib / subdir
        out.mkdir(parents=True, exist_ok=True)
        for sym in SYMBOLS:
            path = out / f"{sym['name']}.svg"
            path.write_text(build(sym, lead_w, art_w))
            print(f"{lib}: {path.relative_to(root)}")


if __name__ == "__main__":
    main()
