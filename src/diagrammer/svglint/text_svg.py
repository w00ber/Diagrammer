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

# Tolerance for calling a coordinate "on the grid", in pt.
GRID_TOL = 0.05
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
    """Start and end of a path built only from absolute M/L/H/V.

    Anything with curves, arcs or relative commands returns ``None`` rather
    than a guess: a wrong endpoint here would be a confident false alarm, which
    is worse than staying quiet.
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


def shift_path_data(d: str, dx: float, dy: float) -> str:
    """Shift absolute path coordinates; relative commands need no change."""
    tokens = re.findall(rf"[A-Za-z]|{NUM}", d)
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
        n = _NARGS.get(cmd.upper())
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
        out.extend(fmt(a) for a in args)
        if cmd == "M":
            cmd = "L"
        elif cmd == "m":
            cmd = "l"
    res: list[str] = []
    for tok in out:
        if res and (not re.match(r"[A-Za-z]", tok)
                    and not re.match(r"[A-Za-z]", res[-1])):
            res.append(" " + tok)
        elif res and re.match(r"[A-Za-z]", tok):
            res.append(" " + tok)
        else:
            res.append(tok)
    return "".join(res).replace("  ", " ").strip()


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


def lead_endpoints(text: str) -> list[tuple[float, float, float, float]]:
    """``(x1, y1, x2, y2)`` for every lead whose ends we can read exactly."""
    pts: list[tuple[float, float, float, float]] = []
    for g in re.finditer(rf'<g id="{LEAD_LAYER_RE}"[^>]*>(.*?)</g>', text, re.S):
        for m in re.finditer(r"<line\b([^>]*)>", g.group(1)):
            a = m.group(1)
            got = {k: re.search(rf'\b{k}="([-\d.]+)"', a)
                   for k in ("x1", "y1", "x2", "y2")}
            # Omitted coordinates default to 0, which is how the library
            # writes edge leads (x2 absent means x2=0).
            pts.append(tuple(float(got[k].group(1)) if got[k] else 0.0
                             for k in ("x1", "y1", "x2", "y2")))  # type: ignore[arg-type]
        for m in re.finditer(r'<path\b[^>]*\bd="([^"]+)"', g.group(1)):
            ends = simple_path_ends(m.group(1))
            if ends:
                pts.append(ends)
    return pts
