"""Export a selection of diagram items as a reusable SVG component.

Builds an SVG manually from the component and connection data,
avoiding QSvgGenerator (which produces namespace-polluted SVG with
scene-space transforms that Diagrammer can't re-parse).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF


def export_compound_component(
    scene,
    selected_items: list,
    output_path: Path,
    component_name: str,
    margin: float = 10.0,
) -> bool:
    """Export selected items as a reusable SVG component.

    Renders each sub-component's SVG artwork at its current position,
    rotation, flip, and stretch state. Connections are drawn as paths.
    Unterminated ports become the new component's port markers.

    Returns True if export succeeded.
    """
    from diagrammer.items.annotation_item import AnnotationItem
    from diagrammer.items.component_item import ComponentItem
    from diagrammer.items.connection_item import ConnectionItem
    from diagrammer.items.junction_item import JunctionItem
    from diagrammer.items.shape_item import LineItem, ShapeItem

    components = [i for i in selected_items if isinstance(i, ComponentItem)]
    connections = [i for i in selected_items if isinstance(i, ConnectionItem)]
    junctions = [i for i in selected_items if isinstance(i, JunctionItem)]
    annotations = [i for i in selected_items if isinstance(i, AnnotationItem)]
    shapes = [i for i in selected_items if isinstance(i, (ShapeItem, LineItem))]

    if not components and not annotations and not shapes:
        return False

    # Also find internal connections
    selected_ids = set(id(i) for i in selected_items)
    for item in scene.items():
        if isinstance(item, ConnectionItem) and item not in connections:
            if (id(item.source_port.component) in selected_ids and
                    id(item.target_port.component) in selected_ids):
                connections.append(item)

    # Compute bounding rect
    all_items = components + connections + junctions + annotations + shapes
    union = QRectF()
    for item in all_items:
        union = union.united(item.sceneBoundingRect())
    if union.isNull():
        return False

    union.adjust(-margin, -margin, margin, margin)
    ox, oy = union.x(), union.y()
    vb_w, vb_h = union.width(), union.height()

    # Build SVG
    svg = ET.Element("svg")
    svg.set("xmlns", "http://www.w3.org/2000/svg")
    svg.set("viewBox", f"0 0 {vb_w:.1f} {vb_h:.1f}")

    artwork = ET.SubElement(svg, "g")
    artwork.set("id", "artwork")

    # Render each component as a <use> or inline group
    for comp in components:
        _render_component(artwork, comp, ox, oy)

    # Render connections as paths
    for conn in connections:
        _render_connection(artwork, conn, ox, oy)

    # Render junctions as dots
    for junc in junctions:
        if junc.isVisible():
            sc = junc.mapToScene(QPointF(0, 0))
            circle = ET.SubElement(artwork, "circle")
            circle.set("cx", f"{sc.x() - ox:.1f}")
            circle.set("cy", f"{sc.y() - oy:.1f}")
            circle.set("r", "3")
            circle.set("fill", "#323232")

    # Render annotations as text
    for annot in annotations:
        _render_annotation(artwork, annot, ox, oy)

    # Render shapes (rectangles, ellipses, lines)
    for shape in shapes:
        _render_shape(artwork, shape, ox, oy)

    # Collect ALL ports from all components in the selection
    all_ports = []
    for comp in components:
        for port in comp.ports:
            all_ports.append(port)

    # Add ports group
    if all_ports:
        ports_group = ET.SubElement(svg, "g")
        ports_group.set("id", "ports")
        seen_names: set[str] = set()
        for port in all_ports:
            sc = port.scene_center()
            x = sc.x() - ox
            y = sc.y() - oy
            name = port.port_name
            counter = 1
            base = name
            while name in seen_names:
                name = f"{base}_{counter}"
                counter += 1
            seen_names.add(name)
            c = ET.SubElement(ports_group, "circle")
            c.set("id", f"port:{name}")
            c.set("cx", f"{x:.1f}")
            c.set("cy", f"{y:.1f}")
            c.set("r", "3")
            c.set("fill", "#c66")

    # Write
    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), xml_declaration=True, encoding="unicode")
    return True


def _render_component(parent: ET.Element, comp, ox: float, oy: float) -> None:
    """Render a ComponentItem as inline SVG artwork at its scene position.

    For stretched components, rebuilds the SVG with stretched coordinates
    so the exported compound retains the stretched appearance.
    """
    import xml.etree.ElementTree as ET2
    from diagrammer.items.component_item import ComponentItem

    cdef = comp.component_def

    is_stretched = (
        (cdef.stretch_v_pos is not None and comp.stretch_dx != 0.0) or
        (cdef.stretch_h_pos is not None and comp.stretch_dy != 0.0) or
        (cdef.stretch_v_repeat is not None and comp.stretch_dx != 0.0) or
        (cdef.stretch_h_repeat is not None and comp.stretch_dy != 0.0)
    )

    if is_stretched:
        # Get the stretched SVG bytes from the component's renderer pipeline
        # Force a rebuild to get fresh bytes
        comp._get_stretched_renderer()
        # Re-parse the SVG with the same stretch logic
        tree = ET2.parse(str(cdef.svg_path))
        root = tree.getroot()
        # Apply the same viewBox update
        vb = root.get("viewBox", "")
        if vb:
            parts = vb.replace(",", " ").split()
            if len(parts) == 4:
                root.set("viewBox", f"{parts[0]} {parts[1]} {comp._width} {comp._height}")
        # Hide non-rendered layers
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "g" and elem.get("id") in ("ports", "labels", "stretch", "snap", "decorative"):
                elem.set("display", "none")
        # Apply coordinate shifting
        def _find(layer_id):
            for e in root.iter():
                t = e.tag.split("}")[-1] if "}" in e.tag else e.tag
                if t == "g" and e.get("id") == layer_id:
                    return e
            return None
        artwork_l = _find("artwork")
        leads_l = _find("leads")
        # Use the same stretch logic as _get_stretched_renderer
        import copy
        v_repeat = cdef.stretch_v_repeat
        h_repeat = cdef.stretch_h_repeat
        dx = comp.stretch_dx
        dy = comp.stretch_dy
        if v_repeat and dx != 0:
            tile_w = v_repeat[1] - v_repeat[0]
            if tile_w > 0:
                n_extra = max(0, int(round(dx / tile_w)))
                total_growth = n_extra * tile_w
                orig_snaps = {}
                for layer in (artwork_l, leads_l):
                    if layer is not None:
                        orig_snaps[layer] = [copy.deepcopy(c) for c in layer]
                for layer in (artwork_l, leads_l):
                    if layer is not None:
                        ComponentItem._shift_svg_element(layer, v_repeat[1], None, total_growth, 0)
                for layer in (artwork_l, leads_l):
                    if layer is not None:
                        ComponentItem._tile_layer_elements(root, layer, "x",
                                                           v_repeat[0], v_repeat[1],
                                                           n_extra, total_growth,
                                                           orig_snaps.get(layer))
        elif cdef.stretch_v_pos is not None and dx != 0:
            for layer in (artwork_l, leads_l):
                if layer is not None:
                    ComponentItem._shift_svg_element(layer, cdef.stretch_v_pos, None, dx, 0)
        if h_repeat and dy != 0:
            tile_h = h_repeat[1] - h_repeat[0]
            if tile_h > 0:
                n_extra = max(0, int(round(dy / tile_h)))
                total_growth = n_extra * tile_h
                orig_snaps = {}
                for layer in (artwork_l, leads_l):
                    if layer is not None:
                        orig_snaps[layer] = [copy.deepcopy(c) for c in layer]
                for layer in (artwork_l, leads_l):
                    if layer is not None:
                        ComponentItem._shift_svg_element(layer, None, h_repeat[1], 0, total_growth)
                for layer in (artwork_l, leads_l):
                    if layer is not None:
                        ComponentItem._tile_layer_elements(root, layer, "y",
                                                           h_repeat[0], h_repeat[1],
                                                           n_extra, total_growth,
                                                           orig_snaps.get(layer))
        elif cdef.stretch_h_pos is not None and dy != 0:
            for layer in (artwork_l, leads_l):
                if layer is not None:
                    ComponentItem._shift_svg_element(layer, None, cdef.stretch_h_pos, 0, dy)
    else:
        tree = ET2.parse(str(cdef.svg_path))
        root = tree.getroot()
        # Hide non-rendered layers for non-stretched too
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "g" and elem.get("id") in ("ports", "labels", "stretch", "snap", "decorative"):
                elem.set("display", "none")

    # Apply per-instance style overrides (bake into export)
    if hasattr(comp, '_style_overrides') and not comp.style_overrides.is_empty():
        ComponentItem._apply_style_overrides(root, comp.style_overrides)

    # Find artwork group
    art_elem = None
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "g" and elem.get("id") == "artwork":
            art_elem = elem
            break

    # Find leads group
    leads_elem = None
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "g" and elem.get("id") == "leads":
            leads_elem = elem
            break

    if art_elem is None and leads_elem is None:
        return

    # Build transform: translate to scene pos, apply rotation/flip
    # The component's scene transform includes rotation and flip
    t = comp.sceneTransform()
    m11, m12, m21, m22 = t.m11(), t.m12(), t.m21(), t.m22()
    dx_t, dy_t = t.dx() - ox, t.dy() - oy

    g = ET2.SubElement(parent, "g")
    g.set("transform", f"matrix({m11:.4f},{m12:.4f},{m21:.4f},{m22:.4f},{dx_t:.1f},{dy_t:.1f})")

    # Copy defs (styles) if present
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "defs":
            # Inline the style
            for style_elem in elem:
                style_tag = style_elem.tag.split("}")[-1] if "}" in style_elem.tag else style_elem.tag
                if style_tag == "style" and style_elem.text:
                    s = ET2.SubElement(g, "style")
                    s.text = style_elem.text
            break

    # Copy artwork elements
    if art_elem is not None:
        for child in art_elem:
            g.append(child)

    # Copy leads elements
    if leads_elem is not None:
        for child in leads_elem:
            g.append(child)


def _render_connection(parent: ET.Element, conn, ox: float, oy: float) -> None:
    """Render a ConnectionItem as an SVG path."""
    points = conn.all_points()
    if len(points) < 2:
        return

    # Build path data
    parts = [f"M{points[0].x() - ox:.1f},{points[0].y() - oy:.1f}"]
    for pt in points[1:]:
        parts.append(f"L{pt.x() - ox:.1f},{pt.y() - oy:.1f}")

    path = ET.SubElement(parent, "path")
    path.set("d", " ".join(parts))
    path.set("fill", "none")
    path.set("stroke", conn.line_color.name())
    path.set("stroke-width", str(conn.line_width))
    path.set("stroke-linecap", "round")
    path.set("stroke-linejoin", "round")


def _render_annotation(parent: ET.Element, annot, ox: float, oy: float) -> None:
    """Render an AnnotationItem as SVG text."""
    sc = annot.mapToScene(QPointF(0, 0))
    x = sc.x() - ox
    y = sc.y() - oy

    rotation = annot.rotation()

    text = ET.SubElement(parent, "text")
    text.set("x", f"{x:.1f}")
    text.set("y", f"{y + annot.font_size:.1f}")  # SVG text y is baseline
    if rotation:
        # Rotate around the annotation's scene origin
        text.set("transform", f"rotate({rotation:.1f},{x:.1f},{y:.1f})")
    text.set("font-family", annot.font_family)
    text.set("font-size", f"{annot.font_size}")
    text.set("fill", annot.text_color.name())
    if annot.font_bold:
        text.set("font-weight", "bold")
    if annot.font_italic:
        text.set("font-style", "italic")
    text.text = annot.source_text


def _render_shape(parent: ET.Element, shape, ox: float, oy: float) -> None:
    """Render a ShapeItem or LineItem as SVG."""
    from diagrammer.items.shape_item import (
        ARROW_BACKWARD, ARROW_BOTH, ARROW_FORWARD, DASH_PATTERNS,
        EllipseItem, LineItem, RectangleItem, ShapeItem,
    )
    import math

    sc = shape.mapToScene(QPointF(0, 0))
    x = sc.x() - ox
    y = sc.y() - oy

    # Build style string
    style_parts = []
    style_parts.append(f"stroke:{shape.stroke_color.name()}")
    style_parts.append(f"stroke-width:{shape.stroke_width}")

    if isinstance(shape, LineItem):
        cap = "square" if shape.cap_style == "square" else "round"
        style_parts.append(f"stroke-linecap:{cap}")

    dash = getattr(shape, 'dash_style', 'solid')
    if dash != 'solid':
        pattern = DASH_PATTERNS.get(dash, [])
        if pattern:
            style_parts.append(f"stroke-dasharray:{','.join(str(v * shape.stroke_width) for v in pattern)}")

    if isinstance(shape, ShapeItem):
        fc = shape.fill_color
        style_parts.append(f"fill:{fc.name(fc.NameFormat.HexArgb)}")
    else:
        style_parts.append("fill:none")

    style = ";".join(style_parts)

    if isinstance(shape, RectangleItem):
        rect = ET.SubElement(parent, "rect")
        rect.set("x", f"{x:.1f}")
        rect.set("y", f"{y:.1f}")
        rect.set("width", f"{shape.shape_width:.1f}")
        rect.set("height", f"{shape.shape_height:.1f}")
        if shape.corner_radius > 0:
            rect.set("rx", f"{shape.corner_radius:.1f}")
            rect.set("ry", f"{shape.corner_radius:.1f}")
        rect.set("style", style)

    elif isinstance(shape, EllipseItem):
        rx = shape.shape_width / 2
        ry = shape.shape_height / 2
        ellipse = ET.SubElement(parent, "ellipse")
        ellipse.set("cx", f"{x + rx:.1f}")
        ellipse.set("cy", f"{y + ry:.1f}")
        ellipse.set("rx", f"{rx:.1f}")
        ellipse.set("ry", f"{ry:.1f}")
        ellipse.set("style", style)

    elif isinstance(shape, LineItem):
        start = shape.line_start
        end = shape.line_end
        line = ET.SubElement(parent, "line")
        line.set("x1", f"{x + start.x():.1f}")
        line.set("y1", f"{y + start.y():.1f}")
        line.set("x2", f"{x + end.x():.1f}")
        line.set("y2", f"{y + end.y():.1f}")
        line.set("style", style)

        # Arrowheads
        arrow = shape.arrow_style
        if arrow in (ARROW_FORWARD, ARROW_BOTH):
            _render_arrowhead(parent, x + start.x(), y + start.y(),
                              x + end.x(), y + end.y(),
                              shape.stroke_width, shape.stroke_color.name())
        if arrow in (ARROW_BACKWARD, ARROW_BOTH):
            _render_arrowhead(parent, x + end.x(), y + end.y(),
                              x + start.x(), y + start.y(),
                              shape.stroke_width, shape.stroke_color.name())


def _render_arrowhead(parent: ET.Element, tail_x, tail_y, tip_x, tip_y,
                       stroke_width, color) -> None:
    """Render an arrowhead as an SVG polygon."""
    import math
    dx = tip_x - tail_x
    dy = tip_y - tail_y
    length = max(math.hypot(dx, dy), 1e-9)
    ux, uy = dx / length, dy / length
    arrow_size = max(stroke_width * 3, 8.0)
    px, py = -uy, ux
    bx = tip_x - ux * arrow_size
    by = tip_y - uy * arrow_size
    pts = (f"{tip_x:.1f},{tip_y:.1f} "
           f"{bx + px * arrow_size * 0.4:.1f},{by + py * arrow_size * 0.4:.1f} "
           f"{bx - px * arrow_size * 0.4:.1f},{by - py * arrow_size * 0.4:.1f}")
    poly = ET.SubElement(parent, "polygon")
    poly.set("points", pts)
    poly.set("fill", color)
    poly.set("stroke", "none")
