"""Display polarity: black-on-white, or the same diagram white-on-black.

Polarity is a *view* setting, not document state. Every colour stored on an
item, in a .dgm, on the clipboard or in a component SVG stays canonical (as
drawn for a white page); the mapping happens only where colour is handed to
Qt for painting. Because every export except compound export renders through
``QGraphicsScene.render`` — i.e. through the same ``paint()`` calls — the
exported SVG, PDF, PNG and clipboard image come out in whatever polarity the
canvas shows. Compound export deliberately stays canonical: its output becomes
a library component, which the canvas would otherwise invert a second time.

**The rule.** Every colour has its HLS lightness inverted (``L -> 1 - L``),
hue and saturation kept. Black and white swap, ``#333`` becomes ``#ccc``,
mid-grey stays put, a pale tint becomes a dark tint of the same hue, and a
saturated mid-lightness colour (a red photon, L ≈ 0.5) barely moves.

Why *every* colour: what reads as an edge is the lightness difference ΔL
between two touching colours. One decreasing map applied to all of them flips
every ΔL together, so every edge keeps its contrast. Inverting only some
colours (an earlier draft flipped greys alone) breaks the pairs where one side
moves and the other does not — dark text on a pale flowchart fill became light
text on the same pale fill. The mapping is its own inverse up to 8-bit
rounding, and the stored colours are never touched, so toggling back is exact.

Two entry points, matching the two ways colour reaches the screen:

* :func:`map_svg_text` / :func:`svg_renderer` for SVG bytes fed to a
  ``QSvgRenderer`` (components, pasted SVGs, rendered math);
* :func:`display_color` for ``QColor`` values items paint with directly
  (wires, junctions, shapes, text).

The colour arithmetic is pure Python; Qt is imported only by the two helpers
that need it, so the rule itself is testable without a QApplication.
"""

from __future__ import annotations

import colorsys
import re

_inverted = False


def is_inverted() -> bool:
    return _inverted


def set_inverted(on: bool) -> None:
    """Set the process-wide polarity. Callers must refresh what is cached
    (see ``DiagramScene.refresh_polarity``); this only changes the rule."""
    global _inverted
    _inverted = bool(on)


# ---------------------------------------------------------------------------
# The colour rule (pure)
# ---------------------------------------------------------------------------

def invert_rgb(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Invert HLS lightness, keeping hue and saturation."""
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    nr, ng, nb = colorsys.hls_to_rgb(h, 1.0 - l, s)
    return round(nr * 255), round(ng * 255), round(nb * 255)


#: The CSS basic colours plus the greys. No shipped component uses a colour
#: name (only ``none``); this covers pasted SVGs. A name not listed here
#: passes through unmapped.
_NAMED = {
    "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (255, 0, 0), "lime": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "aqua": (0, 255, 255),
    "magenta": (255, 0, 255), "fuchsia": (255, 0, 255),
    "maroon": (128, 0, 0), "green": (0, 128, 0), "navy": (0, 0, 128),
    "olive": (128, 128, 0), "teal": (0, 128, 128), "purple": (128, 0, 128),
    "orange": (255, 165, 0),
    "gray": (128, 128, 128), "grey": (128, 128, 128),
    "silver": (192, 192, 192), "dimgray": (105, 105, 105),
    "dimgrey": (105, 105, 105), "darkgray": (169, 169, 169),
    "darkgrey": (169, 169, 169), "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211), "gainsboro": (220, 220, 220),
    "whitesmoke": (245, 245, 245), "snow": (255, 250, 250),
}

_HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})(?![0-9a-fA-F])")
_RGB = re.compile(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", re.I)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def map_color_value(value: str) -> str:
    """Map one CSS/SVG colour value (``#000``, ``rgb(…)``, ``black``…).

    Anything that is not a plain colour — ``none``, ``url(#grad)``,
    ``currentColor``, ``inherit`` — is returned as is.
    """
    stripped = value.strip()
    low = stripped.lower()
    if low.startswith("url(") or not stripped:
        return value
    m = _HEX.match(stripped)
    if m:
        digits = m.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        rgb = tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
        out = invert_rgb(*rgb)
        if out == rgb:
            return value
        return value.replace(m.group(0), _hex(out), 1)
    m = _RGB.match(stripped)
    if m:
        rgb = tuple(min(255, int(c)) for c in m.groups())
        out = invert_rgb(*rgb)
        if out == rgb:
            return value
        return value.replace(m.group(0), _hex(out), 1)
    word = re.match(r"[a-zA-Z]+", stripped)
    if word and word.group(0).lower() in _NAMED:
        rgb = _NAMED[word.group(0).lower()]
        out = invert_rgb(*rgb)
        if out == rgb:
            return value
        return value.replace(word.group(0), _hex(out), 1)
    return value


# ---------------------------------------------------------------------------
# SVG text
# ---------------------------------------------------------------------------

_PROP = r"(?<![\w-])(fill|stroke|stop-color|color|flood-color|lighting-color)"
# Presentation attribute:  fill="#000"
_ATTR = re.compile(_PROP + r"""(\s*=\s*)(["'])(.*?)\3""", re.S)
# CSS declaration, in a <style> block or a style="" attribute:  fill:#000
_DECL = re.compile(_PROP + r"(\s*:\s*)([^;}\"'<>]+)")
# ElementTree re-serialises the root as <ns0:svg …>, so allow a prefix.
_ROOT = re.compile(r"<(?:[\w.-]+:)?svg\b[^>]*>", re.S)


def map_svg_text(text: str) -> str:
    """Apply the polarity rule to every colour in an SVG document.

    Covers presentation attributes, ``<style>`` rules and inline ``style=``
    alike. SVG's *implicit* paint — an element with no fill at all is filled
    black, and ``currentColor`` defaults to black — is made explicit on the
    root element, so it inverts too; children still override it as before.
    """
    def attr(m: re.Match) -> str:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{map_color_value(m.group(4))}{m.group(3)}"

    def decl(m: re.Match) -> str:
        return f"{m.group(1)}{m.group(2)}{map_color_value(m.group(3))}"

    out = _DECL.sub(decl, _ATTR.sub(attr, text))

    root = _ROOT.search(out)
    if root:
        tag = root.group(0)
        black = _hex(invert_rgb(0, 0, 0))
        extra = ""
        if not re.search(r"(?<![\w-])fill\s*=", tag):
            extra += f' fill="{black}"'
        if not re.search(r"(?<![\w-])color\s*=", tag):
            extra += f' color="{black}"'
        if extra:
            close = len(tag) - (2 if tag.endswith("/>") else 1)
            out = out[:root.start()] + tag[:close] + extra + tag[close:] + out[root.end():]
    return out


def map_svg_bytes(data: bytes) -> bytes:
    """:func:`map_svg_text` when inverted; the bytes unchanged otherwise."""
    if not _inverted:
        return data
    return map_svg_text(data.decode("utf-8", errors="replace")).encode("utf-8")


# ---------------------------------------------------------------------------
# Qt helpers
# ---------------------------------------------------------------------------

def svg_renderer(data: bytes):
    """A ``QSvgRenderer`` for *data* in the current polarity.

    Every renderer that paints diagram content should come from here, so
    that a polarity toggle only has to rebuild renderers, not find them.
    """
    from PySide6.QtSvg import QSvgRenderer
    return QSvgRenderer(map_svg_bytes(data))


def display_color(color):
    """The ``QColor`` to paint for a canonical *color* (alpha preserved)."""
    if not _inverted:
        return color
    from PySide6.QtGui import QColor
    c = QColor(color)
    r, g, b = invert_rgb(c.red(), c.green(), c.blue())
    if (r, g, b) == (c.red(), c.green(), c.blue()):
        return c
    return QColor(r, g, b, c.alpha())
