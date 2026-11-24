"""Which lead ends at each port, and how it is drawn.

A wire should look like the lead it continues: same stroke width, same
colour. Wire creation used to read the *first* element of the lead layer for
the whole part, re-parsing the SVG on every connection, which is wrong for a
part whose leads differ (and slow). This reads every lead once, at parse
time, matches each lead's endpoints to ports, and stores the result on the
port (``PortDef.lead_width`` / ``lead_color``).

It walks the parsed tree rather than regex-matching the text, because
Illustrator nests leads inside its own sub-groups (``<g id="leads"><g
id="leads1"><g id="leads2"><path/>``) — 14 shipped parts do — and a
non-greedy ``<g …>(.*?)</g>`` stops at the first inner ``</g>``.

No Qt here: the wiring checks and svglint-style tooling read it headless.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from diagrammer.svglint.text_svg import ENDPOINT_TOL, NUM, path_endpoints

#: The layers whose strokes continue into wires. Same list as
#: ``component_item.LEAD_LAYER_IDS`` (which imports Qt, so is not reused).
LEAD_LAYER_IDS = ("leads", "leads-left", "leads-right", "leads-top", "leads-bottom")


@dataclass(frozen=True)
class LeadStyle:
    """How a lead is drawn. ``None`` means the SVG does not say."""

    width: float | None
    color: str | None     # "#rrggbb", lower case, when it could be read


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _css_rules(root: ET.Element) -> list[tuple[list[str], dict[str, str]]]:
    """``(class names, declarations)`` for every ``.class`` rule, in order."""
    rules: list[tuple[list[str], dict[str, str]]] = []
    for elem in root.iter():
        if _strip_ns(elem.tag) != "style" or not elem.text:
            continue
        for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", elem.text):
            classes = re.findall(r"\.([\w-]+)", rule.group(1))
            if not classes:
                continue
            decls = {d.group(1).strip(): d.group(2).strip()
                     for d in re.finditer(r"([\w-]+)\s*:\s*([^;]+)", rule.group(2))}
            rules.append((classes, decls))
    return rules


def _declared(elem: ET.Element, prop: str,
              rules: list[tuple[list[str], dict[str, str]]]) -> str | None:
    """``prop`` as set on *elem* itself: inline style, then class rules
    (later rules win, as in the renderer), then the presentation attribute
    (which CSS overrides)."""
    style = elem.get("style", "")
    m = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", style)
    if m:
        return m.group(1).strip()
    found = None
    classes = elem.get("class", "").split()
    for names, decls in rules:
        if prop in decls and any(c in names for c in classes):
            found = decls[prop]
    if found is not None:
        return found
    return elem.get(prop)


def normalize_color(value: str | None) -> str | None:
    """``#rrggbb`` for a readable colour; ``None`` for none/unknown."""
    if value is None:
        return None
    v = value.strip().lower().replace("!important", "").strip()
    if not v or v in ("none", "transparent", "inherit", "currentcolor") \
            or v.startswith("url("):
        return None
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        return "#" + ("".join(c * 2 for c in h) if len(h) == 3 else h)
    m = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", v)
    if m:
        return "#{:02x}{:02x}{:02x}".format(*(min(255, int(c)) for c in m.groups()))
    return {"black": "#000000", "white": "#ffffff"}.get(v)


def _width(value: str | None) -> float | None:
    if value is None:
        return None
    m = re.match(rf"\s*({NUM})", value)
    return float(m.group(1)) if m else None


def _ends(elem: ET.Element) -> tuple[tuple[float, float], tuple[float, float]] | None:
    tag = _strip_ns(elem.tag)
    try:
        if tag == "line":
            return ((float(elem.get("x1", "0")), float(elem.get("y1", "0"))),
                    (float(elem.get("x2", "0")), float(elem.get("y2", "0"))))
        if tag == "path":
            pts = path_endpoints(elem.get("d", ""))
            return (pts[0], pts[-1]) if len(pts) >= 2 else None
        if tag in ("polyline", "polygon"):
            nums = [float(n) for n in re.findall(NUM, elem.get("points", ""))]
            if len(nums) >= 4:
                return (nums[0], nums[1]), (nums[-2], nums[-1])
    except ValueError:
        return None
    return None


def _style(elem: ET.Element, chain: list[ET.Element],
           rules: list[tuple[list[str], dict[str, str]]]) -> LeadStyle:
    """The element's stroke style, inheriting from enclosing groups."""
    width = color = None
    for node in [elem, *reversed(chain)]:       # nearest first
        if width is None:
            width = _width(_declared(node, "stroke-width", rules))
        if color is None:
            raw = _declared(node, "stroke", rules)
            if raw is not None:
                color = normalize_color(raw)
                if color is None:      # "none" stops inheritance too
                    color = ""
    return LeadStyle(width, color or None)


def lead_elements(root: ET.Element) -> list[tuple[ET.Element, LeadStyle]]:
    """Every stroked element in a lead layer, with its resolved style."""
    rules = _css_rules(root)
    out: list[tuple[ET.Element, LeadStyle]] = []
    seen: set[int] = set()

    def walk(node: ET.Element, chain: list[ET.Element], in_lead: bool) -> None:
        tag = _strip_ns(node.tag)
        here = in_lead or (tag == "g" and node.get("id") in LEAD_LAYER_IDS)
        if here and tag in ("line", "path", "polyline", "polygon") \
                and id(node) not in seen:
            seen.add(id(node))
            out.append((node, _style(node, chain, rules)))
        for child in node:
            walk(child, [*chain, node], here)

    walk(root, [], False)
    return out


#: Leaf tags that carry an element path. Same set as
#: ``svg_element_info._LEAF_TAGS`` (which imports Qt, so is not reused).
_LEAF_TAGS = {"line", "path", "rect", "circle", "ellipse", "polyline",
              "polygon", "text", "use"}


def lead_element_paths(root: ET.Element) -> dict[int, str]:
    """``{id(element): path}`` for the leaves of the lead layers, in the
    scheme per-instance style overrides use (``svg_element_info``,
    ``ComponentItem._apply_style_overrides``): the layer id, then the child
    index at each level. The first lead layer to reach an element names it.
    """
    out: dict[int, str] = {}

    def walk(node: ET.Element, prefix: str) -> None:
        for i, child in enumerate(node):
            tag = _strip_ns(child.tag)
            path = f"{prefix}/{i}"
            if tag == "g":
                walk(child, path)
            elif tag in _LEAF_TAGS:
                out.setdefault(id(child), path)

    for layer_id in LEAD_LAYER_IDS:
        group = next((e for e in root.iter()
                      if _strip_ns(e.tag) == "g" and e.get("id") == layer_id),
                     None)
        if group is not None:
            walk(group, layer_id)
    return out


def assign_lead_styles(root: ET.Element, ports) -> LeadStyle | None:
    """Set ``lead_width``/``lead_color`` on each port whose lead ends on it,
    and ``lead_paths`` to the element paths of every lead ending there.

    Returns the part-wide fallback: the first lead's style, which is what
    wire creation used for every port before this existed. A port with no
    lead of its own (a terminal, a part drawn without leads) keeps ``None``
    and callers fall back to that.
    """
    leads = lead_elements(root)
    paths = lead_element_paths(root)
    for elem, _style in leads:
        ends = _ends(elem)
        path = paths.get(id(elem))
        if ends is None or path is None:
            continue
        for port in ports:
            if any((ex - port.x) ** 2 + (ey - port.y) ** 2 <= ENDPOINT_TOL ** 2
                   for ex, ey in ends) and path not in port.lead_paths:
                port.lead_paths = (*port.lead_paths, path)
    for elem, style in leads:
        ends = _ends(elem)
        if ends is None:
            continue
        for port in ports:
            if port.lead_width is not None or port.lead_color is not None:
                continue
            if any((ex - port.x) ** 2 + (ey - port.y) ** 2 <= ENDPOINT_TOL ** 2
                   for ex, ey in ends):
                port.lead_width = style.width
                port.lead_color = style.color
    for _elem, style in leads:
        if style.width is not None:
            return style
    return None
