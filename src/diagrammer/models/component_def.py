"""ComponentDef — parse an SVG file to extract artwork, ports, labels, and metadata."""

from __future__ import annotations

import tempfile
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
    # Repeating stretch: two break lines define a tile region
    stretch_h_repeat: tuple[float, float] | None = None  # (y1, y2) repeat region
    stretch_v_repeat: tuple[float, float] | None = None  # (x1, x2) repeat region
    min_width: float = 0.0
    min_height: float = 0.0
    decorative: bool = False  # freely resizable, no ports/leads
    snap_point: tuple[float, float] | None = None  # optional snap anchor (from <g id="snap">)
    category: str = ""
    # If this def came from a user-saved compound, the path to its sibling
    # ``.dgmcomp`` manifest. When set, the canvas drop handler instantiates
    # the manifest as a group instead of placing the SVG as a single item,
    # which preserves stretchability and lets the user ungroup and edit.
    compound_manifest_path: Path | None = None
    # Embedded support: raw SVG bytes cached for serialization, and a flag
    # indicating this def was reconstructed from embedded .dgm data rather
    # than loaded from the on-disk component library.
    svg_bytes: bytes | None = None
    is_embedded: bool = False

    @classmethod
    def from_embedded(cls, metadata: dict, svg_bytes: bytes) -> ComponentDef:
        """Reconstruct a ComponentDef from embedded SVG data and metadata.

        Writes *svg_bytes* to a temporary file so that all existing code
        paths that call ``ET.parse(str(svg_path))`` continue to work.
        """
        tmp = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
        tmp.write(svg_bytes)
        tmp.flush()
        tmp.close()
        comp = cls.from_svg(Path(tmp.name), category=metadata.get("category", ""))
        comp.name = metadata.get("name", comp.name)
        comp.svg_bytes = svg_bytes
        comp.is_embedded = True
        return comp

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
        stretch_h_pos, stretch_v_pos, stretch_h_repeat, stretch_v_repeat = _parse_stretch_layer(root)

        # Fall back to convention-based inference when the stretch layer
        # is absent or only partially specified. <g id="tile"> defines a
        # repeat region; direction-tagged leads (leads-left/-right/-top/
        # -bottom) define an anchored gap stretch.
        if (stretch_v_pos is None and stretch_v_repeat is None) or \
           (stretch_h_pos is None and stretch_h_repeat is None):
            (stretch_h_pos, stretch_v_pos,
             stretch_h_repeat, stretch_v_repeat) = _infer_stretch_from_layers(
                path, root,
                stretch_h_pos, stretch_v_pos,
                stretch_h_repeat, stretch_v_repeat,
            )

        # If the stretch layer defines axes, ensure the booleans are set
        if stretch_h_pos is not None or stretch_h_repeat is not None:
            stretch_h = True
        if stretch_v_pos is not None or stretch_v_repeat is not None:
            stretch_v = True

        # Parse snap point layer (for decorative components)
        snap_point = _parse_snap_point(root)

        # Decorative: has a <g id="decorative"> marker, or has no ports
        # and has a snap point defined
        decorative = _find_group_by_id(root, "decorative") is not None
        if not decorative and not ports and snap_point is not None:
            decorative = True

        manifest_path = path.with_suffix(".dgmcomp")
        if not manifest_path.is_file():
            manifest_path = None

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
            stretch_h_repeat=stretch_h_repeat,
            stretch_v_repeat=stretch_v_repeat,
            min_width=min_width,
            min_height=min_height,
            decorative=decorative,
            snap_point=snap_point,
            category=category,
            compound_manifest_path=manifest_path,
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


def _parse_stretch_layer(root: ET.Element) -> tuple[
    float | None, float | None,
    tuple[float, float] | None, tuple[float, float] | None,
]:
    """Parse the ``<g id="stretch">`` layer for stretch axis break lines.

    Supports two modes per axis:

    **Gap stretch (single break line):**
    - ``stretch:h`` — Y position; content below shifts down
    - ``stretch:v`` — X position; content to the right shifts right

    **Repeating stretch (two break lines):**
    - ``stretch:h1`` + ``stretch:h2`` — Y range; content between is tiled
    - ``stretch:v1`` + ``stretch:v2`` — X range; content between is tiled

    Returns ``(stretch_h_pos, stretch_v_pos, stretch_h_repeat, stretch_v_repeat)``.
    """
    stretch_group = _find_group_by_id(root, "stretch")
    if stretch_group is None:
        return None, None, None, None

    stretch_h_pos: float | None = None
    stretch_v_pos: float | None = None
    h1: float | None = None
    h2: float | None = None
    v1: float | None = None
    v2: float | None = None

    for elem in stretch_group:
        tag = _strip_ns(elem.tag)
        elem_id = elem.get("id", "")

        if tag == "line":
            if elem_id == "stretch:h":
                stretch_h_pos = float(elem.get("y1", "0"))
            elif elem_id == "stretch:v":
                stretch_v_pos = float(elem.get("x1", "0"))
            elif elem_id == "stretch:h1":
                h1 = float(elem.get("y1", "0"))
            elif elem_id == "stretch:h2":
                h2 = float(elem.get("y1", "0"))
            elif elem_id == "stretch:v1":
                v1 = float(elem.get("x1", "0"))
            elif elem_id == "stretch:v2":
                v2 = float(elem.get("x1", "0"))

    stretch_h_repeat = (min(h1, h2), max(h1, h2)) if h1 is not None and h2 is not None else None
    stretch_v_repeat = (min(v1, v2), max(v1, v2)) if v1 is not None and v2 is not None else None

    return stretch_h_pos, stretch_v_pos, stretch_h_repeat, stretch_v_repeat


def _svg_path_geometric_bbox(d: str) -> tuple[float, float, float, float] | None:
    """Compute (x_min, y_min, x_max, y_max) of an SVG path's geometry.

    Walks absolute and relative path commands, tracking current position,
    and accumulates extents from every endpoint and Bézier control point.
    Stroke padding is **not** included — this is bare path geometry,
    suitable for tile-region inference where ``QSvgRenderer.boundsOnElement``
    would over-estimate by half the stroke width on each side.
    """
    import re
    tokens = re.findall(
        r'[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)
    cmd_args = {
        'M': 2, 'L': 2, 'H': 1, 'V': 1,
        'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0,
    }
    cx = cy = sx = sy = 0.0
    pts: list[tuple[float, float]] = []
    cmd = ""
    nums: list[float] = []

    def consume() -> None:
        nonlocal cx, cy, sx, sy, cmd
        if not cmd or cmd.upper() == 'Z':
            return
        u = cmd.upper()
        is_rel = cmd.islower()
        n = cmd_args.get(u, 0)
        if n == 0:
            return
        i = 0
        # After the first M, additional coordinate pairs are implicit L's.
        first_m = (u == 'M')
        while i + n <= len(nums):
            args = nums[i:i + n]
            i += n
            if u == 'M' or u == 'L':
                if is_rel:
                    cx += args[0]; cy += args[1]
                else:
                    cx, cy = args[0], args[1]
                if u == 'M' and first_m:
                    sx, sy = cx, cy
                    u = 'L'
                    first_m = False
                pts.append((cx, cy))
            elif u == 'H':
                if is_rel: cx += args[0]
                else: cx = args[0]
                pts.append((cx, cy))
            elif u == 'V':
                if is_rel: cy += args[0]
                else: cy = args[0]
                pts.append((cx, cy))
            elif u == 'C':
                if is_rel:
                    pts.append((cx + args[0], cy + args[1]))
                    pts.append((cx + args[2], cy + args[3]))
                    cx += args[4]; cy += args[5]
                else:
                    pts.append((args[0], args[1]))
                    pts.append((args[2], args[3]))
                    cx, cy = args[4], args[5]
                pts.append((cx, cy))
            elif u == 'S':
                if is_rel:
                    pts.append((cx + args[0], cy + args[1]))
                    cx += args[2]; cy += args[3]
                else:
                    pts.append((args[0], args[1]))
                    cx, cy = args[2], args[3]
                pts.append((cx, cy))
            elif u == 'Q':
                if is_rel:
                    pts.append((cx + args[0], cy + args[1]))
                    cx += args[2]; cy += args[3]
                else:
                    pts.append((args[0], args[1]))
                    cx, cy = args[2], args[3]
                pts.append((cx, cy))
            elif u == 'T':
                if is_rel:
                    cx += args[0]; cy += args[1]
                else:
                    cx, cy = args[0], args[1]
                pts.append((cx, cy))
            elif u == 'A':
                # Last 2 args are the endpoint; arc shape detail not needed
                # for axis-aligned bbox approximation.
                if is_rel:
                    cx += args[5]; cy += args[6]
                else:
                    cx, cy = args[5], args[6]
                pts.append((cx, cy))

    for t in tokens:
        if t.isalpha():
            consume()
            cmd = t
            nums = []
            if t.upper() == 'Z':
                cx, cy = sx, sy
                cmd = ""
        else:
            try:
                nums.append(float(t))
            except ValueError:
                continue
    consume()

    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _layer_geometric_bbox(group: ET.Element) -> tuple[float, float, float, float] | None:
    """Union of geometric (stroke-less) bboxes of all leaf elements in a group."""
    bboxes: list[tuple[float, float, float, float]] = []
    for elem in group.iter():
        tag = _strip_ns(elem.tag)
        try:
            if tag == "path":
                b = _svg_path_geometric_bbox(elem.get("d", ""))
                if b: bboxes.append(b)
            elif tag in ("polyline", "polygon"):
                pts_attr = elem.get("points", "")
                import re
                nums = [float(n) for n in re.findall(
                    r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', pts_attr)]
                pairs = [(nums[i], nums[i+1]) for i in range(0, len(nums) - 1, 2)]
                if pairs:
                    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
                    bboxes.append((min(xs), min(ys), max(xs), max(ys)))
            elif tag == "line":
                x1 = float(elem.get("x1", "0")); y1 = float(elem.get("y1", "0"))
                x2 = float(elem.get("x2", "0")); y2 = float(elem.get("y2", "0"))
                bboxes.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
            elif tag == "rect":
                x = float(elem.get("x", "0")); y = float(elem.get("y", "0"))
                w = float(elem.get("width", "0")); h = float(elem.get("height", "0"))
                bboxes.append((x, y, x + w, y + h))
            elif tag == "circle":
                cx = float(elem.get("cx", "0")); cy = float(elem.get("cy", "0"))
                r = float(elem.get("r", "0"))
                bboxes.append((cx - r, cy - r, cx + r, cy + r))
            elif tag == "ellipse":
                cx = float(elem.get("cx", "0")); cy = float(elem.get("cy", "0"))
                rx = float(elem.get("rx", "0")); ry = float(elem.get("ry", "0"))
                bboxes.append((cx - rx, cy - ry, cx + rx, cy + ry))
        except (ValueError, TypeError):
            continue
    if not bboxes:
        return None
    return (
        min(b[0] for b in bboxes), min(b[1] for b in bboxes),
        max(b[2] for b in bboxes), max(b[3] for b in bboxes),
    )


def _infer_stretch_from_layers(
    svg_path: Path,
    root: ET.Element,
    stretch_h_pos: float | None,
    stretch_v_pos: float | None,
    stretch_h_repeat: tuple[float, float] | None,
    stretch_v_repeat: tuple[float, float] | None,
) -> tuple[
    float | None, float | None,
    tuple[float, float] | None, tuple[float, float] | None,
]:
    """Infer missing stretch break-line positions from the new layer convention.

    Per axis (horizontal first, then vertical), and only if no explicit
    break line was already declared via ``<g id="stretch">``:

    1. If a ``<g id="tile">`` element is present, derive a *repeat stretch*
       from its **geometric** bbox (path centerlines, no stroke padding) so
       cloned tiles abut cleanly without gaps.
    2. Otherwise, if direction-tagged lead groups (``leads-left`` /
       ``leads-right`` / ``leads-top`` / ``leads-bottom``) are present,
       derive an *anchored gap stretch*: the break sits between the
       inner edges of the two groups so that the moving lead group
       translates on stretch while the rest stays put.

    For lead-group bboxes we use ``QSvgRenderer.boundsOnElement()``
    (stroke-aware is fine for choosing a midpoint break line). For tile
    bboxes we walk the path data ourselves — ``boundsOnElement`` would
    add half the stroke width on each side, leaving visible gaps between
    cloned tiles when the renderer spaces clones by that wider extent.
    """
    # If both axes are already declared, nothing to infer.
    if (stretch_h_pos is not None or stretch_h_repeat is not None) and \
       (stretch_v_pos is not None or stretch_v_repeat is not None):
        return stretch_h_pos, stretch_v_pos, stretch_h_repeat, stretch_v_repeat

    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return stretch_h_pos, stretch_v_pos, stretch_h_repeat, stretch_v_repeat

    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        return stretch_h_pos, stretch_v_pos, stretch_h_repeat, stretch_v_repeat

    def _bbox(elem_id: str):
        if not renderer.elementExists(elem_id):
            return None
        b = renderer.boundsOnElement(elem_id)
        if b.isNull() or b.isEmpty():
            return None
        return b

    # Tile bbox: path-data geometric extent (no stroke padding).
    tile_g = _find_group_by_id(root, "tile")
    tile_geom = _layer_geometric_bbox(tile_g) if tile_g is not None else None

    # Lead-group bboxes: stroke-aware is fine for break-line midpoints.
    ll_b = _bbox("leads-left")
    lr_b = _bbox("leads-right")
    lt_b = _bbox("leads-top")
    lb_b = _bbox("leads-bottom")

    # If the author already explicitly declared one axis (via
    # ``<g id="stretch">``), the ``<g id="tile">`` is presumed to
    # belong to that declared axis. Don't claim it for the other axis
    # — many legacy components have a tile alongside an explicit
    # ``stretch:h1``/``h2`` declaration; auto-claiming the tile for
    # the horizontal axis would silently change their behavior.
    explicit_h_declared = stretch_h_pos is not None or stretch_h_repeat is not None
    explicit_v_declared = stretch_v_pos is not None or stretch_v_repeat is not None

    # ---------------- Horizontal axis ----------------
    if not explicit_v_declared:
        wants_horiz = ll_b is not None or lr_b is not None
        wants_vert = lt_b is not None or lb_b is not None
        # Use the tile for horizontal stretch only if direction-tagged
        # leads point that way, OR if there's no other intent at all
        # (no directional leads, no explicit declaration on the other
        # axis either).
        if tile_geom is not None and (
            wants_horiz
            or (not wants_vert and not explicit_h_declared)
        ):
            stretch_v_repeat = (tile_geom[0], tile_geom[2])
        elif ll_b is not None and lr_b is not None:
            stretch_v_pos = (ll_b.right() + lr_b.x()) / 2
        elif lr_b is not None:
            stretch_v_pos = lr_b.x()
        elif ll_b is not None:
            stretch_v_pos = ll_b.right()

    # ---------------- Vertical axis ----------------
    if not explicit_h_declared:
        wants_vert = lt_b is not None or lb_b is not None
        used_tile_for_h = stretch_v_repeat is not None and tile_geom is not None
        if tile_geom is not None and wants_vert and not used_tile_for_h:
            stretch_h_repeat = (tile_geom[1], tile_geom[3])
        elif lt_b is not None and lb_b is not None:
            stretch_h_pos = (lt_b.bottom() + lb_b.y()) / 2
        elif lb_b is not None:
            stretch_h_pos = lb_b.y()
        elif lt_b is not None:
            stretch_h_pos = lt_b.bottom()

    return stretch_h_pos, stretch_v_pos, stretch_h_repeat, stretch_v_repeat


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
