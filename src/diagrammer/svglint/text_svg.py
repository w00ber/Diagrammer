"""Text-level SVG primitives shared by the checks and the fixes.

Everything here works on the document *text*, not on a parsed tree, and that is
deliberate. A component file is hand-tuned artwork: Illustrator's CSS classes,
the comment banner, the attribute order, the indentation. Parsing it and writing
it back reformats all of that even where nothing changed, which makes a diff
unreadable and a review impossible. Surgical substitution over text leaves every
untouched byte exactly as it was.

Lifted from ``scripts/normalize_editor_svgs.py`` and the validator in
``.claude/skills/svg-component/scripts/`` so the app, that skill script and the
pre-commit hook share one implementation instead of three.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tolerance for calling a coordinate "on the grid", in pt.
GRID_TOL = 0.05
# How far off a grid line still reads as drift rather than design, in pt.
# Editor drift is 0.1-0.4 pt (a port at 59.7 for 60); deliberate offsets in
# the shipped tree are 2 pt or more (GND's port at 23, the flowchart insets).
NEAR_GRID_TOL = 1.0
# Tolerance for accepting a path as a circle approximation, in pt.
CIRCLE_TOL = 0.15
# The app's own port/edge detection tolerance (ComponentDef.EDGE_TOLERANCE).
EDGE_TOL = 2.0
# ``ComponentItem._find_port_at`` matches within distance**2 < 4.0.
ENDPOINT_TOL = 2.0
# Named prefixes the parser keys on; these are the ids worth restoring.
NAMED_PREFIXES = ("port:", "stretch:", "label:")

# Layer ids the app assigns a meaning to. Anything else in the top level is
# the author's own and gets rendered as artwork.
KNOWN_LAYER_IDS = (
    "artwork", "leads", "leads-left", "leads-right", "leads-top",
    "leads-bottom", "ports", "labels", "stretch", "tile", "upright",
    "decorative", "snap",
)
LEAD_LAYER_RE = r'leads(?:-left|-right|-top|-bottom)?'

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def fmt(v: float) -> str:
    """Format a coordinate the way the component files do — no trailing zeros."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def grid_delta(v: float, grid: float) -> float:
    """Signed distance to the nearest grid line."""
    return v - round(v / grid) * grid


def snap_lattice(grid: float) -> float:
    """The spacing ports are snapped to: a quarter of the library grid.

    Not the grid itself, because whole-grid conformance is deliberately not
    checked — circuits/ parts centre ports on half-grid lines by design, and
    parts like the 30-wide filters sit on 5 pt lines. A coordinate *near* one
    of these lines but not on it is drift, whatever the design.
    """
    return grid / 4.0


def near_grid_target(v: float, grid: float) -> float | None:
    """The lattice line ``v`` has drifted off, or None if it has not.

    None both when ``v`` is on a line (within ``GRID_TOL``) and when it is so
    far from one (over ``NEAR_GRID_TOL``) that the offset is likely meant.
    """
    lat = snap_lattice(grid)
    d = grid_delta(v, lat)
    if GRID_TOL < abs(d) <= NEAR_GRID_TOL:
        return v - d
    return None


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------

def group_body(text: str, group_id: str) -> tuple[int, int] | None:
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


def viewbox(text: str) -> tuple[float, float, float, float] | None:
    m = re.search(r'viewBox="([^"]*)"', text)
    if m is None:
        return None
    parts = m.group(1).replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        return tuple(float(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def top_level_layer_ids(text: str) -> list[str]:
    """The ``id``s of groups that are direct children of ``<svg>``.

    Depth-tracked, because only a direct child counts: the parser's
    ``_find_group_by_id`` searches recursively and so finds a nested ``artwork``
    anyway, but the layer *contract* is about document order and paint order,
    both of which a wrapper breaks.
    """
    svg = re.search(r"<svg\b[^>]*?>", text)
    if svg is None:
        return []
    out: list[str] = []
    depth = 0
    for tok in re.finditer(r'<g\b([^>]*?)(/?)>|</g>', text[svg.end():]):
        if tok.group(0) == "</g>":
            depth = max(0, depth - 1)
            continue
        self_closing = bool(tok.group(2))
        if depth == 0:
            gid = re.search(r'\sid="([^"]*)"', tok.group(1) or "")
            if gid:
                out.append(gid.group(1))
        if not self_closing:
            depth += 1
    return out


# --------------------------------------------------------------------------
# path geometry
# --------------------------------------------------------------------------

_NARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4,
          "Q": 4, "T": 2, "A": 7, "Z": 0}


def path_endpoints(d: str) -> list[tuple[float, float]]:
    """On-curve endpoints of a path, ignoring Bezier control points."""
    tokens = re.findall(rf"[A-Za-z]|{NUM}", d)
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
        n = _NARGS.get(cmd.upper())
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
    pts = path_endpoints(d)
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
        if abs(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 - rx) > CIRCLE_TOL:
            return None
    return cx, cy, rx


def path_as_line(d: str) -> tuple[float, float, float, float] | None:
    """``(x1, y1, x2, y2)`` if the path is a single straight segment."""
    if re.search(r"[CcSsQqTtAa]", d):
        return None
    pts = path_endpoints(d)
    if len(pts) != 2:
        return None
    (x1, y1), (x2, y2) = pts
    return x1, y1, x2, y2


def simple_path_ends(d: str) -> tuple[float, float, float, float] | None:
    """Start and end of a path built only from M/L/H/V, absolute or relative.

    Relative ``m/l/h/v`` are read exactly — Illustrator writes most straight
    leads as ``M59.7,98.5v-21.1`` — and ``ComponentItem._shorten_path_elem``
    shortens them, so skipping them hid real faults. Anything with curves or
    arcs still returns ``None`` rather than a guess: a wrong endpoint here would
    be a confident false alarm, which is worse than staying quiet.
    """
    tokens = re.findall(r"([MLHVmlhv])([^MLHVmlhvCcSsQqTtAaZz]*)", d)
    if not tokens or len(re.findall(r"[A-Za-z]", d)) != len(tokens):
        return None
    x = y = None
    start = None
    for cmd, args in tokens:
        nums = [float(v) for v in re.findall(NUM, args)]
        rel = cmd.islower() and x is not None
        op = cmd.upper()
        if op == "M":
            if len(nums) < 2:
                return None
            # The first pair of a leading ``m`` is absolute; the rest of an
            # ``m`` are implicit ``l`` pairs, relative like the command.
            x, y = (x + nums[0], y + nums[1]) if rel else (nums[0], nums[1])
            start = (x, y)
            nums = nums[2:]
            op = "L"
        if x is None or y is None:
            return None  # drawing before any moveto
        if op == "L":
            for i in range(0, len(nums) - 1, 2):
                x, y = ((x + nums[i], y + nums[i + 1]) if cmd.islower()
                        else (nums[i], nums[i + 1]))
        elif op == "H":
            for v in nums:
                x = x + v if cmd.islower() else v
        elif op == "V":
            for v in nums:
                y = y + v if cmd.islower() else v
    if start is None or x is None or y is None:
        return None
    return (start[0], start[1], x, y)


def simple_path_points(d: str) -> list[tuple[float, float]] | None:
    """Absolute vertices of a single-subpath M/L/H/V path, or None.

    The same grammar as ``simple_path_ends``, kept to one subpath: a fix that
    moves one end of a lead must know it is rewriting the whole of it.
    """
    tokens = re.findall(r"([MLHVmlhv])([^MLHVmlhvCcSsQqTtAaZz]*)", d)
    if not tokens or len(re.findall(r"[A-Za-z]", d)) != len(tokens):
        return None
    pts: list[tuple[float, float]] = []
    for i, (cmd, args) in enumerate(tokens):
        nums = [float(v) for v in re.findall(NUM, args)]
        op, rel = cmd.upper(), cmd.islower()
        if op == "M":
            if i or len(nums) < 2:
                return None  # a second subpath, or a bare M
            pts.append((nums[0], nums[1]))
            nums, op = nums[2:], "L"
        if not pts:
            return None
        x, y = pts[-1]
        if op == "L":
            if len(nums) % 2:
                return None
            for j in range(0, len(nums), 2):
                x, y = (x + nums[j], y + nums[j + 1]) if rel else (nums[j], nums[j + 1])
                pts.append((x, y))
        elif op == "H":
            for v in nums:
                x = x + v if rel else v
                pts.append((x, y))
        elif op == "V":
            for v in nums:
                y = y + v if rel else v
                pts.append((x, y))
    return pts if len(pts) >= 2 else None


def polyline_d(points: list[tuple[float, float]]) -> str:
    """Absolute path data through ``points``, using H/V where they apply."""
    (x, y), rest = points[0], points[1:]
    out = [f"M{fmt(x)},{fmt(y)}"]
    for nx, ny in rest:
        if abs(ny - y) < 1e-9:
            out.append(f"H{fmt(nx)}")
        elif abs(nx - x) < 1e-9:
            out.append(f"V{fmt(ny)}")
        else:
            out.append(f"L{fmt(nx)},{fmt(ny)}")
        x, y = nx, ny
    return "".join(out)


def shift_path_data(d: str, dx: float, dy: float) -> str:
    """Shift absolute path coordinates; relative commands need no change.

    Rewrites only the numbers that move and keeps every other character —
    Illustrator's compact ``-4.9-2.2`` spacing included — so a translated
    path diffs as the coordinates that changed, not as a reformatted line.
    """
    out: list[str] = []
    pos = 0
    cmd = ""
    k = 0  # index of the next argument within the current command's group
    first = True  # a path's opening moveto is absolute even when written "m"
    leading_m = False
    for m in re.finditer(rf"[A-Za-z]|{NUM}", d):
        t = m.group(0)
        out.append(d[pos:m.start()])
        pos = m.end()
        if t.isalpha():
            cmd, k = t, 0
            leading_m, first = first and t == "m", False
            out.append(t)
            continue
        up = cmd.upper()
        n = _NARGS.get(up)
        if not n:
            out.append(t)
            continue
        i = k % n
        k += 1
        delta = 0.0
        absolute = cmd.isupper() or (leading_m and k <= 2)
        if absolute:
            if up in ("M", "L", "T", "C", "S", "Q"):
                delta = dx if i % 2 == 0 else dy
            elif up == "H":
                delta = dx
            elif up == "V":
                delta = dy
            elif up == "A" and i in (5, 6):
                delta = dx if i == 5 else dy
        if up == "M" and k == n:
            # Pairs after a moveto are implicit linetos of the same case.
            cmd, k, leading_m = ("L" if cmd == "M" else "l"), 0, False
        if delta == 0.0:
            out.append(t)
            continue
        new = fmt(float(t) + delta)
        prev = out[-1][-1:] if out[-1] else (out[-2][-1:] if len(out) > 1 else "")
        # "4.9-2" shifted to "4.9" + "0.3" must not fuse into "4.90.3".
        if not new.startswith("-") and (prev.isdigit() or prev == "."):
            new = " " + new
        out.append(new)
    out.append(d[pos:])
    return "".join(out)


# --------------------------------------------------------------------------
# ports and leads
# --------------------------------------------------------------------------

def ports_from_text(text: str) -> list[tuple[str, float, float]]:
    """``(id, x, y)`` for every port marker, read from the text.

    Mirrors ``ComponentDef._parse_ports``: ``<circle>`` by centre, ``<rect>``
    by the centre of its box, id must start ``port:``.
    """
    span = group_body(text, "ports")
    if span is None:
        return []
    out: list[tuple[str, float, float]] = []
    for tag in re.finditer(r"<(circle|rect)\b[^>]*/>", text[span[0]:span[1]]):
        t = tag.group(0)
        pid = re.search(r'\sid="(port:[^"]*)"', t)
        if pid is None:
            continue

        def num(attr: str, tag_text: str = t) -> float:
            m = re.search(rf'\s{attr}="([^"]*)"', tag_text)
            try:
                return float(m.group(1)) if m else 0.0
            except ValueError:
                return 0.0

        if tag.group(1) == "circle":
            x, y = num("cx"), num("cy")
        else:
            x = num("x") + num("width") / 2
            y = num("y") + num("height") / 2
        out.append((pid.group(1), x, y))
    return out


def css_widths(text: str) -> dict[str, float]:
    """Map CSS class name -> stroke-width, the way the app resolves it."""
    out: dict[str, float] = {}
    for block in re.finditer(r"<style[^>]*>(.*?)</style>", text, re.S):
        for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", block.group(1)):
            m = re.search(r"stroke-width\s*:\s*([\d.]+)", rule.group(2))
            if not m:
                continue
            for cls in re.finditer(r"\.([\w-]+)", rule.group(1)):
                out[cls.group(1)] = float(m.group(1))
    return out


def css_property(text: str, prop: str) -> dict[str, str]:
    """Map CSS class name -> value of ``prop``, for the classes that set it."""
    out: dict[str, str] = {}
    for block in re.finditer(r"<style[^>]*>(.*?)</style>", text, re.S):
        for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", block.group(1)):
            m = re.search(rf"\b{re.escape(prop)}\s*:\s*([^;}}]+)", rule.group(2))
            if not m:
                continue
            for cls in re.finditer(r"\.([\w-]+)", rule.group(1)):
                out[cls.group(1)] = m.group(1).strip()
    return out


def class_declarations(text: str, cls: str) -> dict[str, str]:
    """Every CSS property that currently applies to ``cls``, cascade resolved.

    Later rules win, matching how the renderer resolves equal-specificity
    class selectors. Needed to split a class: a new one has to carry
    everything the old one gave the element, or the lead loses its stroke.
    """
    out: dict[str, str] = {}
    for block in re.finditer(r"<style[^>]*>(.*?)</style>", text, re.S):
        for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", block.group(1)):
            if not re.search(rf"\.{re.escape(cls)}\b", rule.group(1)):
                continue
            for decl in re.finditer(r"([\w-]+)\s*:\s*([^;}]+)", rule.group(2)):
                out[decl.group(1).strip()] = decl.group(2).strip()
    return out


def unique_class_name(text: str, base: str, suffix: str = "lead") -> str:
    """A class name derived from ``base`` that the document does not use.

    Deliberately hyphen-free. A hyphen is legal CSS and this module reads it
    fine, but several of the app's own class readers match ``\\.(\\w+)``, which
    stops at the hyphen — so ``.st1-lead`` would be recorded as ``st1``,
    silently overwriting the real class and then failing to resolve for the
    element. Generating a name those readers can also parse keeps the fix
    working regardless of which reader sees the file.
    """
    used = {m.group(1) for m in re.finditer(r"\.([\w-]+)", text)}
    used |= {m.group(1) for m in re.finditer(r'class="([\w-]+)"', text)}
    candidate = f"{base}{suffix}"
    n = 2
    while candidate in used:
        candidate = f"{base}{suffix}{n}"
        n += 1
    return candidate


def _element_width(attrs: str, widths: dict[str, float]) -> float | None:
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


def lead_width(text: str) -> float | None:
    """Stroke width of the first element in a lead layer, as the app reads it."""
    widths = css_widths(text)
    g = re.search(rf'<g id="{LEAD_LAYER_RE}"[^>]*>(.*?)</g>', text, re.S)
    if not g:
        return None
    for m in re.finditer(r"<(?:line|path|polyline)\b([^>]*)>", g.group(1)):
        w = _element_width(m.group(1), widths)
        if w is not None:
            return w
    return None


def lead_classes(text: str) -> list[str]:
    """CSS classes used by elements inside the lead layers, in order."""
    seen: list[str] = []
    for g in re.finditer(rf'<g id="{LEAD_LAYER_RE}"[^>]*>(.*?)</g>', text, re.S):
        for m in re.finditer(r"<(?:line|path|polyline)\b([^>]*)>", g.group(1)):
            cls = re.search(r'class="([\w-]+)"', m.group(1))
            if cls and cls.group(1) not in seen:
                seen.append(cls.group(1))
    return seen


@dataclass(frozen=True)
class LeadElement:
    """One lead whose ends can be read exactly, and where its tag sits.

    ``start``/``end`` index the whole document, so a fix can rewrite this one
    tag and leave every other byte alone.
    """

    kind: str                 # "line" or "path"
    ends: tuple[float, float, float, float]
    start: int
    end: int


def lead_elements(text: str) -> list[LeadElement]:
    """Every ``<line>`` and simple ``<path>`` inside a lead layer."""
    out: list[LeadElement] = []
    for g in re.finditer(rf'<g id="{LEAD_LAYER_RE}"[^>]*>(.*?)</g>', text, re.S):
        base = g.start(1)
        body = g.group(1)
        for m in re.finditer(r"<line\b([^>]*)>", body):
            # Omitted coordinates default to 0, which is how the library
            # writes edge leads (x2 absent means x2=0).
            ends = tuple(line_attr(m.group(1), k)
                         for k in ("x1", "y1", "x2", "y2"))
            out.append(LeadElement("line", ends,  # type: ignore[arg-type]
                                   base + m.start(), base + m.end()))
        for m in re.finditer(r'<path\b[^>]*\bd="([^"]+)"[^>]*>', body):
            ends = simple_path_ends(m.group(1))
            if ends:
                out.append(LeadElement("path", ends,
                                       base + m.start(), base + m.end()))
    return sorted(out, key=lambda e: e.start)


def line_attr(attrs: str, name: str) -> float:
    """A numeric attribute of a tag, 0 when omitted (the SVG default)."""
    m = re.search(rf'\s{name}="({NUM})"', " " + attrs)
    return float(m.group(1)) if m else 0.0


def lead_endpoints(text: str) -> list[tuple[float, float, float, float]]:
    """``(x1, y1, x2, y2)`` for every lead whose ends we can read exactly."""
    return [e.ends for e in lead_elements(text)]


# --------------------------------------------------------------------------
# near-black paint
# --------------------------------------------------------------------------

#: A paint value in a CSS declaration or a presentation attribute:
#: group 1 the property, group 2 the hex colour.
_PAINT = re.compile(
    r"(?<![\w-])(fill|stroke|stop-color|color)(\s*[:=]\s*[\"']?\s*)"
    r"(#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b)")


def is_near_black(hex_color: str) -> bool:
    """Dark, neutral, and not black: Illustrator's #231f20 "rich black".

    Every channel at most 0x2f, spread at most 16. #333 (0x33) is a
    deliberate dark grey in the shipped libraries and stays out.
    """
    h = hex_color.lstrip("#").lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return False
    return (max(rgb) <= 0x2F and max(rgb) - min(rgb) <= 16
            and any(rgb))


def near_black_paints(text: str) -> list[re.Match]:
    """Every fill/stroke/stop-color/color set to a near-black, in order."""
    return [m for m in _PAINT.finditer(text) if is_near_black(m.group(3))]
