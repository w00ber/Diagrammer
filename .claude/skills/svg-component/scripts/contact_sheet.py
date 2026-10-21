#!/usr/bin/env python3
"""Compose component SVGs into one captioned sheet so a whole family can be
looked at side by side.

Drawing decisions (corner radii, how far a bend sits from the feed, whether a
symbol still reads at 1:8) cannot be settled by reading SVG source — they have
to be seen, and seen next to their neighbours and at the same scale. This
renders a sheet you can actually open.

Ports are hidden, matching how the app draws them, and CSS classes are
namespaced per cell so the twelfth component's `.st1` does not restyle the
first's.

Usage:
    python contact_sheet.py OUT.svg A.svg B.svg ...
    python contact_sheet.py sheet.svg --row "Fanout" a.svg b.svg \\
                                      --row "Cascade" c.svg d.svg
    python contact_sheet.py sheet.png --scale 3 comps/*.svg   # needs rsvg-convert

A .png output is rendered via `rsvg-convert` (brew install librsvg). Without it,
write .svg and open that instead.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

PAD = 26
CAP_H = 30
ROW_GAP = 26
TITLE_H = 28


def cell_svg(path: Path, key: int) -> tuple[float, float, str]:
    """Return (viewBox w, viewBox h, inner markup) with classes namespaced."""
    text = path.read_text(errors="ignore")
    vb = re.search(r'viewBox="([-\d.\s]+)"', text)
    if not vb:
        raise SystemExit(f"{path}: no viewBox")
    parts = vb.group(1).split()
    vw, vh = float(parts[2]), float(parts[3])

    body = re.sub(r"<\?xml[^>]*\?>", "", text)
    body = re.sub(r"<svg[^>]*>", "", body, count=1).rsplit("</svg>", 1)[0]
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    # hide the layers the app hides, so the sheet matches the canvas
    for layer in ("ports", "labels", "stretch", "snap", "guides"):
        body = body.replace(f'<g id="{layer}"', f'<g id="{layer}" display="none"')
    body = re.sub(r"\.st([\w-]+)", lambda m: f".c{key}_st{m.group(1)}", body)
    body = re.sub(r'class="st([\w-]+)"',
                  lambda m: f'class="c{key}_st{m.group(1)}"', body)
    return vw, vh, body


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build(rows: list[tuple[str, list[Path]]], scale: float) -> str:
    els: list[str] = []
    y = 16.0
    total_w = 0.0
    key = 0
    for title, paths in rows:
        if title:
            els.append(f'<text x="{PAD}" y="{y + 13}" font-family="Helvetica" '
                       f'font-size="14" font-weight="bold">{esc(title)}</text>')
            y += TITLE_H
        x = float(PAD)
        row_h = 0.0
        placed = []
        for p in paths:
            vw, vh, body = cell_svg(p, key)
            key += 1
            w, h = vw * scale, vh * scale
            row_h = max(row_h, h)
            placed.append((x, w, h, vw, vh, body, p.stem))
            x += w + PAD
        total_w = max(total_w, x)
        for cx, w, h, vw, vh, body, label in placed:
            els.append(f'<svg x="{cx:g}" y="{y + (row_h - h) / 2:g}" '
                       f'width="{w:g}" height="{h:g}" '
                       f'viewBox="0 0 {vw:g} {vh:g}">{body}</svg>')
            els.append(f'<text x="{cx + w / 2:g}" y="{y + row_h + 17:g}" '
                       f'font-family="Helvetica" font-size="11" '
                       f'text-anchor="middle" fill="#444">{esc(label)}</text>')
        y += row_h + CAP_H + ROW_GAP

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w:g}" '
            f'height="{y:g}" viewBox="0 0 {total_w:g} {y:g}">'
            f'<rect width="{total_w:g}" height="{y:g}" fill="#fff"/>'
            + "".join(els) + "</svg>")


def parse_rows(argv: list[str]) -> list[tuple[str, list[Path]]]:
    """Split the positional args on --row markers."""
    rows: list[tuple[str, list[Path]]] = []
    title, bucket = "", []
    i = 0
    while i < len(argv):
        if argv[i] == "--row":
            if bucket:
                rows.append((title, bucket))
            title, bucket = argv[i + 1], []
            i += 2
        else:
            bucket.append(Path(argv[i]))
            i += 1
    if bucket:
        rows.append((title, bucket))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, help="output .svg or .png")
    ap.add_argument("--scale", type=float, default=2.6,
                    help="pixels per viewBox unit (default 2.6)")
    ap.add_argument("--width", type=int, default=1400,
                    help="png render width in pixels (default 1400)")
    args, rest = ap.parse_known_args()

    rows = parse_rows(rest)
    if not rows:
        ap.error("no input SVGs given")

    svg = build(rows, args.scale)
    if args.out.suffix.lower() == ".png":
        tmp = args.out.with_suffix(".sheet.svg")
        tmp.write_text(svg)
        if not shutil.which("rsvg-convert"):
            print("rsvg-convert not found; wrote", tmp, file=sys.stderr)
            return 1
        subprocess.run(["rsvg-convert", "-w", str(args.width),
                        str(tmp), "-o", str(args.out)], check=True)
        tmp.unlink()
    else:
        args.out.write_text(svg)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
