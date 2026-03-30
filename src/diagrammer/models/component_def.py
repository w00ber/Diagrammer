"""ComponentDef — parse an SVG file to extract artwork, ports, labels, and metadata."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# SVG namespace
SVG_NS = "http://www.w3.org/2000/svg"
# Diagrammer custom namespace for metadata
DGM_NS = "https://diagrammer.dev/ns"

NS_MAP = {"svg": SVG_NS, "dgm": DGM_NS}


@dataclass
class PortDef:
    """A connection port defined in the component SVG.

    approach_dx/dy: unit vector indicating the direction a wire should
    approach this port from (i.e. the direction of the component's lead).
    Auto-detected from port position relative to viewBox if not specified.
    """
    name: str
    x: float
    y: float
    approach_dx: float = 0.0  # set after viewBox is known
    approach_dy: float = 0.0


@dataclass
class LabelDef:
    """A label placeholder defined in the component SVG."""
    name: str
    x: float
    y: float


@dataclass
class ComponentDef:
    """Parsed definition of a reusable SVG component.

    SVG convention:
        <g id="artwork">     — the visible artwork (rendered)
        <g id="ports">       — hidden port markers as <circle id="port:<name>" cx=... cy=.../>
        <g id="labels">      — label placeholders as <text id="label:<name>" x=... y=.../>
        <g id="stretch">     — optional stretch axis lines:
                               <line id="stretch:h" .../> horizontal stretch (break along Y)
                               <line id="stretch:v" .../> vertical stretch (break along X)
        <metadata>           — optional <dgm:component stretch-h="true" .../>

    The viewBox of the SVG defines the component's native coordinate space.
    """
    name: str
    svg_path: Path
    viewbox: tuple[float, float, float, float]  # x, y, width, height
    artwork_svg: str  # raw SVG string of the artwork group
    ports: list[PortDef] = field(default_factory=list)
    labels: list[LabelDef] = field(default_factory=list)
    stretch_h: bool = False
    stretch_v: bool = False
    stretch_h_pos: float | None = None  # Y position of the horizontal break line
    stretch_v_pos: float | None = None  # X position of the vertical break line
    min_width: float = 0.0
    min_height: float = 0.0
    decorative: bool = False  # freely resizable, no ports/leads
    snap_point: tuple[float, float] | None = None  # optional snap anchor (from <g id="snap">)
    category: str = ""

    @property
    def width(self) -> float:
        return self.viewbox[2]

    @property
    def height(self) -> float:
        return self.viewbox[3]

    @classmethod
    def from_svg(cls, path: Path, category: str = "") -> ComponentDef:
        """Parse a component definition from an SVG file."""
        tree = ET.parse(path)
        root = tree.getroot()

        # Strip namespace prefix for easier attribute access
        tag = _strip_ns(root.tag)
        if tag != "svg":
            raise ValueError(f"Expected <svg> root element, got <{tag}> in {path}")

        # Parse viewBox
        viewbox = _parse_viewbox(root.get("viewBox", "0 0 100 100"))

        # Parse artwork group
        artwork_elem = _find_group_by_id(root, "artwork")
        if artwork_elem is not None:
            artwork_svg = ET.tostring(artwork_elem, encoding="unicode")
        else:
            # If no artwork group, use the entire SVG content as artwork
            artwork_svg = ET.tostring(root, encoding="unicode")

        # Parse ports
        ports = _parse_ports(root)

        # Auto-detect approach directions from port positions vs viewBox
        _assign_approach_directions(ports, viewbox)

        # Parse labels
        labels = _parse_labels(root)

        # Parse metadata
        stretch_h, stretch_v, min_width, min_height = _parse_metadata(root)

        # Parse stretch layer for break-line positions
        stretch_h_pos, stretch_v_pos = _parse_stretch_layer(root)

        # If the stretch layer defines axes, ensure the booleans are set
        if stretch_h_pos is not None:
            stretch_h = True
        if stretch_v_pos is not None:
            stretch_v = True

        # Parse snap point layer (for decorative components)
        snap_point = _parse_snap_point(root)

        # Decorative: has a <g id="decorative"> marker, or has no ports
        # and has a snap point defined
        decorative = _find_group_by_id(root, "decorative") is not None
        if not decorative and not ports and snap_point is not None:
            decorative = True

        return cls(
            name=path.stem,
            svg_path=path,
            viewbox=viewbox,
            artwork_svg=artwork_svg,
            ports=ports,
            labels=labels,
            stretch_h=stretch_h,
            stretch_v=stretch_v,
            stretch_h_pos=stretch_h_pos,
            stretch_v_pos=stretch_v_pos,
            min_width=min_width,
            min_height=min_height,
            decorative=decorative,
            snap_point=snap_point,
            category=category,
        )


def _strip_ns(tag: str) -> str:
    """Remove namespace URI from an element tag."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _find_group_by_id(root: ET.Element, group_id: str) -> ET.Element | None:
    """Find a <g> element with the given id attribute, searching recursively."""
    # Try with namespace
    for elem in root.iter():
        if _strip_ns(elem.tag) == "g" and elem.get("id") == group_id:
            return elem
    return None


def _parse_viewbox(vb_str: str) -> tuple[float, float, float, float]:
    """Parse an SVG viewBox string into (x, y, w, h)."""
    parts = vb_str.replace(",", " ").split()
    if len(parts) != 4:
        return (0.0, 0.0, 100.0, 100.0)
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def _parse_ports(root: ET.Element) -> list[PortDef]:
    """Extract port definitions from the 'ports' group."""
    ports_group = _find_group_by_id(root, "ports")
    if ports_group is None:
        return []

    ports: list[PortDef] = []
    # Search recursively — Illustrator may nest ports inside sub-groups
    for elem in ports_group.iter():
        tag = _strip_ns(elem.tag)
        elem_id = elem.get("id", "")

        if not elem_id.startswith("port:"):
            continue

        port_name = elem_id[5:]  # strip "port:" prefix

        if tag == "circle":
            x = float(elem.get("cx", "0"))
            y = float(elem.get("cy", "0"))
        elif tag == "rect":
            x = float(elem.get("x", "0")) + float(elem.get("width", "0")) / 2
            y = float(elem.get("y", "0")) + float(elem.get("height", "0")) / 2
        else:
            continue

        ports.append(PortDef(name=port_name, x=x, y=y))

    return ports


def _parse_labels(root: ET.Element) -> list[LabelDef]:
    """Extract label definitions from the 'labels' group."""
    labels_group = _find_group_by_id(root, "labels")
    if labels_group is None:
        return []

    labels: list[LabelDef] = []
    for elem in labels_group:
        elem_id = elem.get("id", "")
        if not elem_id.startswith("label:"):
            continue

        label_name = elem_id[6:]  # strip "label:" prefix
        x = float(elem.get("x", "0"))
        y = float(elem.get("y", "0"))
        labels.append(LabelDef(name=label_name, x=x, y=y))

    return labels


def _parse_metadata(root: ET.Element) -> tuple[bool, bool, float, float]:
    """Parse <metadata> for stretch and sizing attributes.

    Returns (stretch_h, stretch_v, min_width, min_height).
    """
    stretch_h = False
    stretch_v = False
    min_width = 0.0
    min_height = 0.0

    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag == "metadata":
            # Look for dgm:component child or attributes on metadata children
            for child in elem:
                child_tag = _strip_ns(child.tag)
                if child_tag == "component":
                    stretch_h = child.get("stretch-h", "false").lower() == "true"
                    stretch_v = child.get("stretch-v", "false").lower() == "true"
                    min_width = float(child.get("min-width", "0"))
                    min_height = float(child.get("min-height", "0"))
                    return stretch_h, stretch_v, min_width, min_height

    return stretch_h, stretch_v, min_width, min_height


def _parse_stretch_layer(root: ET.Element) -> tuple[float | None, float | None]:
    """Parse the ``<g id="stretch">`` layer for stretch axis break lines.

    Looks for child ``<line>`` elements with specific ids:
    - ``stretch:h`` — a horizontal line whose Y position defines the horizontal
      stretch break point (content above/below the line stays put; the gap grows).
    - ``stretch:v`` — a vertical line whose X position defines the vertical
      stretch break point.

    Returns (stretch_h_pos, stretch_v_pos).  Either value is ``None`` if the
    corresponding line is not present.
    """
    stretch_group = _find_group_by_id(root, "stretch")
    if stretch_group is None:
        return None, None

    stretch_h_pos: float | None = None
    stretch_v_pos: float | None = None

    for elem in stretch_group:
        tag = _strip_ns(elem.tag)
        elem_id = elem.get("id", "")

        if tag == "line":
            if elem_id == "stretch:h":
                # Horizontal stretch — the break is at the Y position of the line.
                # For a horizontal line y1 == y2; use y1.
                stretch_h_pos = float(elem.get("y1", "0"))
            elif elem_id == "stretch:v":
                # Vertical stretch — the break is at the X position of the line.
                # For a vertical line x1 == x2; use x1.
                stretch_v_pos = float(elem.get("x1", "0"))

    return stretch_h_pos, stretch_v_pos


def _parse_snap_point(root: ET.Element) -> tuple[float, float] | None:
    """Parse the ``<g id="snap">`` layer for a single snap anchor point.

    Looks for a ``<circle>`` element inside the snap group whose center
    (cx, cy) defines the snap point. Used by decorative components to
    define where the component snaps to grid.

    Returns ``(x, y)`` or ``None`` if no snap layer is present.
    """
    snap_group = _find_group_by_id(root, "snap")
    if snap_group is None:
        return None

    for elem in snap_group.iter():
        tag = _strip_ns(elem.tag)
        if tag == "circle":
            cx = float(elem.get("cx", "0"))
            cy = float(elem.get("cy", "0"))
            return (cx, cy)

    return None


def _assign_approach_directions(
    ports: list[PortDef],
    viewbox: tuple[float, float, float, float],
) -> None:
    """Auto-detect approach directions for ports based on their position.

    A port at the left edge of the viewBox gets approach_dx=−1 (wire arrives
    from the left, lead extends right).  Similarly for right/top/bottom edges.
    Ports in the interior get no preferred direction (0, 0).
    """
    _, _, vb_w, vb_h = viewbox
    EDGE_TOLERANCE = 2.0  # pts — how close to the edge counts as "on the edge"

    for port in ports:
        dx, dy = 0.0, 0.0

        # Horizontal edge detection
        if port.x <= EDGE_TOLERANCE:
            dx = -1.0  # left edge — wire approaches from left
        elif port.x >= vb_w - EDGE_TOLERANCE:
            dx = 1.0   # right edge — wire approaches from right

        # Vertical edge detection
        if port.y <= EDGE_TOLERANCE:
            dy = -1.0  # top edge — wire approaches from top
        elif port.y >= vb_h - EDGE_TOLERANCE:
            dy = 1.0   # bottom edge — wire approaches from bottom

        port.approach_dx = dx
        port.approach_dy = dy
