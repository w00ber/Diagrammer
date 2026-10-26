#!/usr/bin/env python3
"""Undo the rewrites some SVG editors apply to Diagrammer component files.

Editors that round-trip a component can silently destroy the parts of it that
make it a *component* rather than a picture. VectorCraft (and some Illustrator
builds) do three things that each, on their own, make ``ComponentDef.from_svg``
see nothing:

1. **Colons sanitized out of ``id``.** ``id="port:left"`` becomes
   ``id="port_left"`` with the real name demoted to ``data-name="port:left"``.
   ``_parse_ports`` / ``_parse_stretch`` only read ``id`` and require the
   ``port:`` / ``stretch:`` prefix, so every port and stretch axis vanishes.
2. **Primitives converted to ``<path>``.** ``<circle>`` and ``<line>`` become
   path approximations. ``_parse_ports`` accepts only ``circle`` and ``rect``;
   stretch parsing accepts only ``line``. Renaming the ids back is not enough.
3. **Content re-wrapped in ``<g id="Layer_1">``**, which hides the named layers
   from the parser entirely.

All three are mechanical and reversible, so this script fixes them in place.
Those three repairs are lossless, so the default run applies them blind and is
safe in a pre-commit hook.

A fourth default repair is lossy in one direction only. Illustrator's "Preserve
Illustrator Editing Capabilities" export option embeds a ``<metadata>`` block
holding ``<i:aipgf>`` -- the whole document again as a zstd/base64 private
stream, 45-90 KB against a median component of 1.5 KB. Nothing but Illustrator
reads it, so removing it cannot change the rendered symbol or how the parser
sees it; what it costs is Illustrator's own editing state on re-open. The
component tree is shipped artwork rather than a working file, so that is the
right trade -- keep the .ai beside it if the state matters.

Grid alignment is **opt-in**, and the reason is worth stating. ``--snap`` can
re-align ports, but only as a rigid translation of the whole document: when
every port misses the grid by the same offset, moving everything back by it
keeps the part internally consistent, because leads, ports and artwork move
together. (Snapping ports *individually* is never offered — that would pull a
port off the lead feeding it.) The transform is sound; the *trigger* is not. A
part that centres its port in a frame which is not a whole number of grid cells
is off-grid by design, and most of the library is like that: across
``components/``, ``--check-grid`` warns on 97 of 208 files, and ``--snap`` would
rewrite 14 the validator already calls sound — one by a full 5 pt. So reach for
it on a file you know an exporter moved, after reading ``--check-grid
--dry-run``, not as routine hygiene.

A cropped viewBox is a different fault and is only ever reported, since the
original frame is not recoverable from the file. Restore it with ``--viewbox``
or from git.

Usage:
    python scripts/normalize_editor_svgs.py <file-or-dir> [...] [--dry-run]
    python scripts/normalize_editor_svgs.py <dir> --check-grid --dry-run
    python scripts/normalize_editor_svgs.py <file> --snap --grid 10
    python scripts/normalize_editor_svgs.py <file> --viewbox "0 0 60 80"

Run ``.claude/skills/svg-component/scripts/validate_components.py`` afterwards —
this script fixes the mangling, the validator confirms the part actually works.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Deliberately stdlib-only and importing nothing from ``diagrammer`` — this has
# to run in a git hook, where the interpreter may not have PySide6.

# Grid the shipped libraries align ports to; --grid relaxes it (e.g. to 10).
DEFAULT_GRID = 20.0
# Tolerance for calling a coordinate "on the grid", in pt.
GRID_TOL = 0.05
# Tolerance for accepting a path as a circle approximation, in pt.
CIRCLE_TOL = 0.15
# Largest uniform shift applied automatically, in pt. Beyond this the nearest
# grid line may not be the one the author meant, so we report instead of move.
DEFAULT_MAX_SHIFT = 5.0
# Named prefixes the parser keys on; these are the ids worth restoring.
NAMED_PREFIXES = ("port:", "stretch:", "label:")

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _fmt(v: float) -> str:
    """Format a coordinate the way the component files do — no trailing zeros."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# --------------------------------------------------------------------------
# 1. id restoration
# --------------------------------------------------------------------------

def restore_named_ids(text: str) -> tuple[str, list[str]]:
    """Move a ``data-name="port:x"`` back into ``id``, dropping the data-name.

    Restores **only** the sanitization pattern — an ``id`` that is exactly the
    ``data-name`` with its colons turned into underscores. That narrowness is
    the whole point, because a second editor behaviour looks superficially
    identical and must not be touched: Illustrator resolves two elements that
    share an ``id`` by appending a digit to one and recording the original in
    ``data-name``. So::

        <circle id="port:2"  .../>                      <!-- port 2 -->
        <circle id="port:21" data-name="port:2" .../>   <!-- a DIFFERENT port -->

    Rewriting the second back to ``port:2`` would duplicate the id and lose a
    port. Here ``"port:2".replace(":", "_")`` is ``port_2``, which is not
    ``port:21``, so the pattern does not match and the element is left alone.
    As a second guard, a name already used as an ``id`` anywhere in the file is
    never assigned again.
    """
    notes: list[str] = []
    existing = set(re.findall(r'\sid="([^"]*)"', text))

    def fix(m: re.Match[str]) -> str:
        tag = m.group(0)
        dn = re.search(r'\sdata-name="([^"]*)"', tag)
        if dn is None:
            return tag
        name = dn.group(1)
        if not name.startswith(NAMED_PREFIXES):
            return tag
        cur = re.search(r'\sid="([^"]*)"', tag)
        if cur is None or cur.group(1) != name.replace(":", "_"):
            return tag
        if name in existing:
            notes.append(f'left id="{cur.group(1)}" alone — "{name}" is '
                         "already used; two elements would collide")
            return tag
        existing.discard(cur.group(1))
        existing.add(name)
        out = tag[:dn.start()] + tag[dn.end():]
        out = re.sub(r'\sid="[^"]*"', f' id="{name}"', out, count=1)
        notes.append(f'restored id="{name}"')
        return out

    return re.sub(r"<\w+\b[^>]*>", fix, text), notes


# --------------------------------------------------------------------------
# 2. path -> circle / line
# --------------------------------------------------------------------------

def _path_endpoints(d: str) -> list[tuple[float, float]]:
    """On-curve endpoints of a path, ignoring Bezier control points."""
    tokens = re.findall(rf"[A-Za-z]|{_NUM}", d)
    nargs = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4,
             "Q": 4, "T": 2, "A": 7, "Z": 0}
    x = y = sx = sy = 0.0
    pts: list[tuple[float, float]] = []
    cmd = ""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd.upper() == "Z":
                x, y = sx, sy
                pts.append((x, y))
            continue
        if not cmd:
            return []
        n = nargs.get(cmd.upper())
        if n is None:
            return []
        args = [float(a) for a in tokens[i:i + n]]
        if len(args) < n:
            return []
        i += n
        rel = cmd.islower()
        up = cmd.upper()
        if up in ("M", "L", "T"):
            x, y = (x + args[0], y + args[1]) if rel else (args[0], args[1])
        elif up == "H":
            x = x + args[0] if rel else args[0]
        elif up == "V":
            y = y + args[0] if rel else args[0]
        elif up == "C":
            x, y = (x + args[4], y + args[5]) if rel else (args[4], args[5])
        elif up in ("S", "Q"):
            x, y = (x + args[2], y + args[3]) if rel else (args[2], args[3])
        elif up == "A":
            x, y = (x + args[5], y + args[6]) if rel else (args[5], args[6])
        if up == "M":
            sx, sy = x, y
        pts.append((x, y))
        if cmd == "M":
            cmd = "L"
        elif cmd == "m":
            cmd = "l"
    return pts


def path_as_circle(d: str) -> tuple[float, float, float] | None:
    """``(cx, cy, r)`` if the path is a circle approximation, else ``None``."""
    pts = _path_endpoints(d)
    if len(pts) < 4 or "C" not in d.upper():
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    rx = (max(xs) - min(xs)) / 2
    ry = (max(ys) - min(ys)) / 2
    if rx <= 0 or abs(rx - ry) > CIRCLE_TOL:
        return None
    for px, py in pts:
        d2 = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        if abs(d2 - rx) > CIRCLE_TOL:
            return None
    return cx, cy, rx


def path_as_line(d: str) -> tuple[float, float, float, float] | None:
    """``(x1, y1, x2, y2)`` if the path is a single straight segment."""
    if re.search(r"[CcSsQqTtAa]", d):
        return None
    pts = _path_endpoints(d)
    if len(pts) != 2:
        return None
    (x1, y1), (x2, y2) = pts
    return x1, y1, x2, y2


def _group_body(text: str, group_id: str) -> tuple[int, int] | None:
    """Span of the body of ``<g id="group_id">``, or None if absent.

    Matches the closing tag by depth, because Illustrator nests sub-groups
    inside ``ports`` and the parser finds those ports anyway (``_parse_ports``
    walks the group with ``.iter()``). A flat search for the first ``</g>``
    would end the group early and silently ignore everything after it.
    """
    m = re.search(rf'<g\s+id="{re.escape(group_id)}"[^>]*?(/?)>', text)
    if m is None or m.group(1):
        return None
    depth = 1
    for tok in re.finditer(r"<g\b[^>]*?(/?)>|</g>", text[m.end():]):
        if tok.group(0) == "</g>":
            depth -= 1
            if depth == 0:
                return m.end(), m.end() + tok.start()
        elif not tok.group(1):
            depth += 1
    return None


def restore_primitives(text: str) -> tuple[str, list[str], list[str]]:
    """Turn path-ified ports back into ``<circle>`` and stretch axes into
    ``<line>``. Returns ``(text, notes, warnings)``."""
    notes: list[str] = []
    warnings: list[str] = []

    for group_id, kind in (("ports", "circle"), ("stretch", "line")):
        span = _group_body(text, group_id)
        if span is None:
            if re.search(rf'<g\s+id="{group_id}"[^>]*[^/]>', text):
                warnings.append(
                    f'<g id="{group_id}"> is never closed — malformed SVG, '
                    "left alone")
            continue
        start, end = span
        body = text[start:end]

        def convert(m: re.Match[str], kind: str = kind) -> str:
            tag = m.group(0)
            dm = re.search(r'\sd="([^"]*)"', tag)
            if dm is None:
                return tag
            attrs = tag[:dm.start()] + tag[dm.end():]
            attrs = re.sub(r"^<path\b", "", attrs)
            attrs = re.sub(r"\s*/?>$", "", attrs).strip()
            attrs = (" " + attrs) if attrs else ""
            if kind == "circle":
                c = path_as_circle(dm.group(1))
                if c is None:
                    return tag
                cx, cy, r = c
                notes.append(
                    f"path -> <circle> at ({_fmt(cx)},{_fmt(cy)}) r={_fmt(r)}")
                return (f'<circle{attrs} cx="{_fmt(cx)}" cy="{_fmt(cy)}" '
                        f'r="{_fmt(r)}"/>')
            ln = path_as_line(dm.group(1))
            if ln is None:
                return tag
            x1, y1, x2, y2 = ln
            notes.append(f"path -> <line> ({_fmt(x1)},{_fmt(y1)})-"
                         f"({_fmt(x2)},{_fmt(y2)})")
            return (f'<line{attrs} x1="{_fmt(x1)}" y1="{_fmt(y1)}" '
                    f'x2="{_fmt(x2)}" y2="{_fmt(y2)}"/>')

        new_body = re.sub(r"<path\b[^>]*/>", convert, body)
        if new_body != body:
            text = text[:start] + new_body + text[end:]

    return text, notes, warnings


# --------------------------------------------------------------------------
# 3. Layer_1 unwrap
# --------------------------------------------------------------------------

def unwrap_layer_group(text: str) -> tuple[str, list[str]]:
    """Hoist the children of a wrapper ``<g id="Layer_1">`` to the root.

    Only unwraps when the wrapper actually contains named layers — a layer
    group that is genuine artwork is left in place.
    """
    m = re.search(r'([ \t]*)<g\s+id="Layer_1"[^>]*>\n?', text)
    if m is None:
        return text, []
    # Match the wrapper's closing tag by depth, so nested <g> inside artwork
    # does not end it early.
    depth = 1
    pos = m.end()
    close = None
    for tok in re.finditer(r"<g\b[^>]*?(/?)>|</g>", text[pos:]):
        if tok.group(0) == "</g>":
            depth -= 1
            if depth == 0:
                close = (pos + tok.start(), pos + tok.end())
                break
        elif not tok.group(1):
            depth += 1
    if close is None:
        return text, []

    body = text[m.end():close[0]]
    if not re.search(r'<g\s+id="(artwork|ports|leads|labels|stretch)"', body):
        return text, []

    # Pull the hoisted lines back by one indent level so the file still reads
    # like the hand-authored components.
    # rstrip at the end, not strip at the start: the body's first line keeps the
    # indent that dedenting is about to reduce, while its last "line" is the
    # bare indent that sat before the wrapper's </g> and must go entirely —
    # leaving it behind puts a blank line before </svg>.
    dedented = "\n".join(
        ln[2:] if ln.startswith("  ") else ln
        for ln in body.strip("\n").split("\n")).rstrip()
    tail = text[close[1]:].lstrip("\n")
    return (text[:m.start()] + dedented + "\n" + tail,
            ['unwrapped <g id="Layer_1">'])


# --------------------------------------------------------------------------
# 4. Illustrator PGF private data
# --------------------------------------------------------------------------

def strip_editor_metadata(text: str) -> tuple[str, list[str]]:
    """Drop the Adobe PGF blob that "Preserve Illustrator Editing
    Capabilities" bakes into an export.

    The ``<metadata>`` block holds ``<i:aipgf>``: the whole document a second
    time, as a zstd/base64 private stream Illustrator reads back so a re-open
    recovers live text, effects and layer state. Nothing outside Illustrator
    can read it -- Qt's renderer, ``ComponentDef.from_svg`` and every browser
    skip it -- and it dwarfs the drawing it accompanies, 45-90 KB against a
    median component of 1.5 KB.

    It is **not** reversible: stripping it costs the Illustrator editing
    state, which is why this is a separate step rather than part of the
    de-mangling above. That is the right trade here because the component
    tree is the shipped artwork, not the working file -- keep the .ai beside
    it if that state matters. The visible drawing is untouched.

    Removes the ``xmlns:i`` declaration with it, since the ``i:`` prefix has
    no other user once the block is gone.
    """
    notes: list[str] = []
    if "i:aipgf" not in text:
        return text, notes

    before = len(text)
    # Non-greedy, DOTALL: the blob contains a CDATA section, so match to the
    # first </metadata> after it rather than trying to parse the contents.
    text, n = re.subn(r"[ \t]*<metadata>.*?</metadata>\n?", "", text,
                      flags=re.DOTALL)
    if not n:
        return text, notes

    # The prefix is only ever used by the block just removed; leaving the
    # declaration behind would be a dangling namespace.
    if not re.search(r"<i:|\si:[\w-]+=", text):
        text = re.sub(r'\s+xmlns:i="[^"]*"', "", text)

    notes.append(f"stripped Illustrator PGF metadata "
                 f"({(before - len(text)) / 1024:.0f} KB)")
    return text, notes


# --------------------------------------------------------------------------
# 5. grid reporting (never automatic)
# --------------------------------------------------------------------------

def _viewbox(text: str) -> tuple[float, float, float, float] | None:
    m = re.search(r'viewBox="([^"]*)"', text)
    if m is None:
        return None
    parts = m.group(1).replace(",", " ").split()
    if len(parts) != 4:
        return None
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def _ports_from_text(text: str) -> list[tuple[str, float, float]]:
    span = _group_body(text, "ports")
    if span is None:
        return []
    out: list[tuple[str, float, float]] = []
    for tag in re.finditer(r"<(circle|rect)\b[^>]*/>", text[span[0]:span[1]]):
        t = tag.group(0)
        pid = re.search(r'\sid="(port:[^"]*)"', t)
        if pid is None:
            continue
        if tag.group(1) == "circle":
            x = float((re.search(r'\scx="([^"]*)"', t) or ["", "0"])[1])
            y = float((re.search(r'\scy="([^"]*)"', t) or ["", "0"])[1])
        else:
            x = (float((re.search(r'\sx="([^"]*)"', t) or ["", "0"])[1])
                 + float((re.search(r'\swidth="([^"]*)"', t) or ["", "0"])[1]) / 2)
            y = (float((re.search(r'\sy="([^"]*)"', t) or ["", "0"])[1])
                 + float((re.search(r'\sheight="([^"]*)"', t) or ["", "0"])[1]) / 2)
        out.append((pid.group(1), x, y))
    return out


def _grid_delta(v: float, grid: float) -> float:
    """Signed distance to the nearest grid line."""
    return v - round(v / grid) * grid


def uniform_shift(text: str, grid: float) -> tuple[float, float] | None:
    """The single translation that puts every port on the grid, if one exists.

    Returns ``None`` when there are no ports, when they are already aligned, or
    when they disagree about the offset — disagreement means the positions were
    authored that way, so no rigid transform can fix them.
    """
    ports = _ports_from_text(text)
    if not ports:
        return None
    deltas = {(round(_grid_delta(x, grid), 3), round(_grid_delta(y, grid), 3))
              for _, x, y in ports}
    if len(deltas) != 1:
        return None
    dx, dy = deltas.pop()
    if abs(dx) <= GRID_TOL and abs(dy) <= GRID_TOL:
        return None
    return -dx, -dy


def grid_report(text: str, grid: float, max_shift: float,
                frame_suspect: bool = False) -> list[str]:
    """Warnings about ports that still miss the grid after normalization.

    ``frame_suspect`` says the viewBox itself is off-grid. Edge ports take their
    position from the frame, so when the frame is wrong the port offsets are a
    consequence of it rather than an independent fault, and saying "fix these in
    the editor" would send the reader after the wrong thing.
    """
    ports = _ports_from_text(text)
    if not ports:
        return []
    off = [(n, x, y) for n, x, y in ports
           if abs(_grid_delta(x, grid)) > GRID_TOL
           or abs(_grid_delta(y, grid)) > GRID_TOL]
    if not off:
        return []

    msgs = [f"{len(off)} of {len(ports)} port(s) off the {_fmt(grid)} pt grid: "
            + ", ".join(f"{n} at ({_fmt(x)},{_fmt(y)})" for n, x, y in off)]
    shift = uniform_shift(text, grid)
    if frame_suspect:
        msgs.append("the viewBox is off-grid too (below) — fix the frame first, "
                    "these port positions likely follow from it")
    elif shift is None:
        msgs.append("the offsets differ between ports, so these positions were "
                    "authored, not shifted by an exporter — no rigid "
                    "translation can fix them; correct them in the editor")
    else:
        dx, dy = shift
        msgs.append(
            f"every port is off by the same ({_fmt(-dx)},{_fmt(-dy)}), but the "
            f"shift exceeds --max-shift {_fmt(max_shift)} pt, so it was not "
            f"applied; if ({_fmt(dx)},{_fmt(dy)}) is right, re-run with "
            f"--max-shift {_fmt(max(abs(dx), abs(dy)))}")
    return msgs


def viewbox_report(text: str, grid: float) -> list[str]:
    """Warn when the viewBox is not a whole number of grid cells.

    A component's frame is its artboard, which is designed on the grid. A frame
    that is a few points short of a grid multiple is the signature of an
    exporter that cropped to the artwork bounding box instead — which no
    translation can undo, because the original size is simply gone.
    """
    vb = _viewbox(text)
    if vb is None:
        return [f"no viewBox — the parser will fall back to {_fmt(100)}x"
                f"{_fmt(100)}"]
    _, _, w, h = vb
    bad = [(axis, v) for axis, v in (("width", w), ("height", h))
           if abs(_grid_delta(v, grid)) > GRID_TOL]
    if not bad:
        return []
    return [f"viewBox {_fmt(w)}x{_fmt(h)} is not a multiple of the "
            f"{_fmt(grid)} pt grid ("
            + ", ".join(f"{axis} {_fmt(v)} -> nearest {_fmt(round(v / grid) * grid)}"
                        for axis, v in bad)
            + ") — the exporter likely cropped the frame to the artwork; "
              "restore it with --viewbox or from git"]


# --------------------------------------------------------------------------
# 5. explicit rigid translation
# --------------------------------------------------------------------------

def translate(text: str, dx: float, dy: float) -> tuple[str, list[str]]:
    """Shift every coordinate in the document by ``(dx, dy)``.

    A rigid transform: ports, leads, artwork, stretch axes and label anchors all
    move together, so every internal relationship the parser cares about — a
    lead ending on its port, a port sitting on the frame edge — is preserved.
    The viewBox is deliberately left alone, because this undoes an exporter that
    re-based the *content* inside the frame, not one that resized the frame.
    """
    notes: list[str] = []

    def shift_path(m: re.Match[str]) -> str:
        return f' d="{_shift_path_data(m.group(1), dx, dy)}"'

    text = re.sub(r'\sd="([^"]*)"', shift_path, text)

    for attr, delta in (("cx", dx), ("cy", dy), ("x1", dx), ("x2", dx),
                        ("y1", dy), ("y2", dy), ("x", dx), ("y", dy)):
        text = re.sub(
            rf'\s{attr}="({_NUM})"',
            lambda m, a=attr, d=delta: f' {a}="{_fmt(float(m.group(1)) + d)}"',
            text)

    text = re.sub(
        r'\sdata-anchor="(' + _NUM + r")\s*,\s*(" + _NUM + r')"',
        lambda m: (f' data-anchor="{_fmt(float(m.group(1)) + dx)},'
                   f'{_fmt(float(m.group(2)) + dy)}"'),
        text)

    notes.append(f"translated every coordinate by ({_fmt(dx)},{_fmt(dy)})")
    return text, notes


def set_viewbox(text: str, vb: tuple[float, float, float, float]
                ) -> tuple[str, list[str]]:
    """Replace the viewBox, and the width/height attributes that mirror it."""
    x, y, w, h = vb
    new = f'viewBox="{_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)}"'
    old = _viewbox(text)
    if old is not None and re.search(r'viewBox="[^"]*"', text):
        text = re.sub(r'viewBox="[^"]*"', new, text, count=1)
    else:
        text = re.sub(r"<svg\b", f"<svg {new}", text, count=1)
    text = re.sub(r'(<svg\b[^>]*?)\swidth="[^"]*"', rf"\1", text, count=1)
    text = re.sub(r'(<svg\b[^>]*?)\sheight="[^"]*"', rf"\1", text, count=1)
    was = f"{_fmt(old[2])}x{_fmt(old[3])}" if old else "none"
    return text, [f"viewBox {was} -> {_fmt(w)}x{_fmt(h)}"]


def _shift_path_data(d: str, dx: float, dy: float) -> str:
    """Shift absolute path coordinates; relative commands need no change."""
    tokens = re.findall(rf"[A-Za-z]|{_NUM}", d)
    nargs = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4,
             "Q": 4, "T": 2, "A": 7, "Z": 0}
    out: list[str] = []
    cmd = ""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            out.append(t)
            i += 1
            continue
        n = nargs.get(cmd.upper())
        if n is None:
            out.append(t)
            i += 1
            continue
        args = [float(a) for a in tokens[i:i + n]]
        if len(args) < n:
            out.extend(tokens[i:])
            break
        i += n
        up = cmd.upper()
        if cmd.isupper():
            if up in ("M", "L", "T"):
                args = [args[0] + dx, args[1] + dy]
            elif up == "H":
                args = [args[0] + dx]
            elif up == "V":
                args = [args[0] + dy]
            elif up == "C":
                args = [args[0] + dx, args[1] + dy, args[2] + dx,
                        args[3] + dy, args[4] + dx, args[5] + dy]
            elif up in ("S", "Q"):
                args = [args[0] + dx, args[1] + dy, args[2] + dx, args[3] + dy]
            elif up == "A":
                args = args[:5] + [args[5] + dx, args[6] + dy]
        out.extend(_fmt(a) for a in args)
        if cmd == "M":
            cmd = "L"
        elif cmd == "m":
            cmd = "l"
    res: list[str] = []
    for tok in out:
        if res and not re.match(r"[A-Za-z]", tok) and not re.match(
                r"[A-Za-z]", res[-1]):
            res.append(" " + tok)
        elif res and re.match(r"[A-Za-z]", tok):
            res.append(" " + tok)
        elif res:
            res.append(tok)
        else:
            res.append(tok)
    return "".join(res).replace("  ", " ").strip()


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def normalize(text: str, grid: float = DEFAULT_GRID,
              max_shift: float = DEFAULT_MAX_SHIFT,
              snap: bool = False,
              check_grid: bool = False,
              viewbox: tuple[float, float, float, float] | None = None,
              ) -> tuple[str, list[str], list[str]]:
    """Apply every repair. Returns ``(text, notes, warnings)``.

    By default only the lossless de-mangling runs, so this is safe to apply
    blind to a whole tree. ``snap`` and ``check_grid`` are off because the
    shipped libraries are full of ports that sit off the grid *on purpose* —
    a port centred in a frame that is not a whole number of grid cells is
    off-grid by definition. Measured across ``components/``, the grid check
    warns on 97 of 208 files and the snap would have rewritten 14 that the
    validator already calls sound, one of them by a full 5 pt. The translation
    itself is sound; what is unreliable is inferring from an off-grid port that
    an exporter moved it.

    Order matters: the ids and primitives have to be restored before the grid
    can be measured, because the port positions are unreadable until then.
    """
    notes: list[str] = []
    warnings: list[str] = []

    text, n = unwrap_layer_group(text)
    notes += n
    text, n = restore_named_ids(text)
    notes += n
    text, n, w = restore_primitives(text)
    notes += n
    warnings += w
    text, n = strip_editor_metadata(text)
    notes += n

    if viewbox is not None:
        text, n = set_viewbox(text, viewbox)
        notes += n

    if snap:
        shift = uniform_shift(text, grid)
        if shift is not None and max(abs(shift[0]), abs(shift[1])) <= max_shift:
            text, n = translate(text, *shift)
            notes += n

    if check_grid:
        frame = viewbox_report(text, grid)
        warnings += grid_report(text, grid, max_shift,
                                frame_suspect=bool(frame))
        warnings += frame
    return text, notes, warnings


def _iter_svgs(target: Path):
    if target.is_dir():
        yield from sorted(target.rglob("*.svg"))
    elif target.is_file():
        yield target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path,
                    help="SVG files or directories to normalize")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    ap.add_argument("--grid", type=float, default=DEFAULT_GRID,
                    help=f"port grid to align to, in pt "
                         f"(default {_fmt(DEFAULT_GRID)}; 10 to relax)")
    ap.add_argument("--max-shift", type=float, default=DEFAULT_MAX_SHIFT,
                    metavar="PT",
                    help="largest uniform translation --snap will apply, in pt "
                         f"(default {_fmt(DEFAULT_MAX_SHIFT)})")
    ap.add_argument("--check-grid", action="store_true",
                    help="also report ports and viewBoxes that miss the grid; "
                         "noisy, because many shipped parts centre a port in a "
                         "frame that is not a whole number of grid cells")
    ap.add_argument("--snap", action="store_true",
                    help="when every port misses the grid by the same offset, "
                         "translate the whole document back by it. Off by "
                         "default: an off-grid port is not reliable evidence "
                         "that an exporter moved it, so review --check-grid "
                         "--dry-run output before using this")
    ap.add_argument("--viewbox", metavar='"X Y W H"',
                    help="replace the viewBox, for a file whose frame an "
                         "exporter cropped; applies to one file at a time")
    args = ap.parse_args(argv)

    viewbox: tuple[float, float, float, float] | None = None
    if args.viewbox:
        parts = args.viewbox.replace(",", " ").split()
        if len(parts) != 4:
            ap.error('--viewbox wants four numbers, e.g. --viewbox "0 0 60 80"')
        try:
            viewbox = tuple(float(p) for p in parts)  # type: ignore[assignment]
        except ValueError:
            ap.error('--viewbox wants four numbers, e.g. --viewbox "0 0 60 80"')

    files = [f for t in args.paths for f in _iter_svgs(t)]
    if not files:
        print("no SVG files found", file=sys.stderr)
        return 1
    if viewbox is not None and len(files) > 1:
        ap.error("--viewbox sets one frame, so pass exactly one file")

    changed = warned = 0
    for svg in files:
        original = svg.read_text(encoding="utf-8")
        new, notes, warnings = normalize(
            original, args.grid, args.max_shift, args.snap, args.check_grid,
            viewbox)
        if new == original and not warnings:
            continue
        if new != original:
            changed += 1
            verb = "would normalize" if args.dry_run else "normalized"
            print(f"{verb}: {svg}")
            if not args.dry_run:
                svg.write_text(new, encoding="utf-8")
        else:
            print(f"checked: {svg}")
        for note in notes:
            print(f"       {note}")
        if warnings:
            warned += 1
        for wn in warnings:
            print(f"       warn:  {wn}")

    verb = "would be normalized" if args.dry_run else "normalized"
    print(f"\n{len(files)} file(s) scanned, {changed} {verb}, "
          f"{warned} with warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
