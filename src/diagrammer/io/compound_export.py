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

    # Compute bounding rect — and build the render list in true scene
    # stacking order so the exported compound matches the on-canvas Z order.
    # scene.items() returns items top-first; reverse to get bottom-up so the
    # topmost item is rendered last (and its CSS rules are appended last).
    all_items = components + connections + junctions + annotations + shapes
    selected_ids_local = {id(i): i for i in all_items}
    scene_order_top_first = [it for it in scene.items() if id(it) in selected_ids_local]
    render_items = list(reversed(scene_order_top_first))
    # Defensive: if scene.items() omits anything, append it at the end so
    # it isn't silently dropped.
    rendered_ids = {id(it) for it in render_items}
    for it in all_items:
        if id(it) not in rendered_ids:
            render_items.append(it)
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

    # Render every selected item in true z-order so the exported compound
    # matches the on-canvas stacking (e.g. an elbow placed on top of a
    # straight coax stays on top after export+reuse).
    comp_idx = 0
    for item in render_items:
        if isinstance(item, ComponentItem):
            _render_component(artwork, item, ox, oy, instance_index=comp_idx)
            comp_idx += 1
        elif isinstance(item, ConnectionItem):
            _render_connection(artwork, item, ox, oy)
        elif isinstance(item, JunctionItem):
            if item.isVisible():
                sc = item.mapToScene(QPointF(0, 0))
                circle = ET.SubElement(artwork, "circle")
                circle.set("cx", f"{sc.x() - ox:.1f}")
                circle.set("cy", f"{sc.y() - oy:.1f}")
                circle.set("r", "3")
                circle.set("fill", "#323232")
        elif isinstance(item, AnnotationItem):
            _render_annotation(artwork, item, ox, oy)
        elif isinstance(item, (ShapeItem, LineItem)):
            _render_shape(artwork, item, ox, oy)

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


def _render_component(parent: ET.Element, comp, ox: float, oy: float,
                      instance_index: int = 0) -> None:
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

    # Find <defs> (if any) so we can copy *all* its children — clipPaths,
    # gradients, markers, etc. — not just <style>. Stretchable components
    # without an explicit <g id="tile"> rely on auto-generated clipPaths in
    # <defs> to clip the tiled clones; if we drop those, the tiled artwork
    # references missing clipPaths and renders as nothing.
    defs_elem = None
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "defs":
            defs_elem = elem
            break

    # Namespace any ids AND CSS class names found inside this component's
    # defs/artwork/leads so that multiple components inlined into the same
    # SVG can't collide. The bundled SVGs all use Adobe-generated class
    # names like ".st0", ".st1", ... so without prefixing, e.g. the elbow's
    # ".st1 { fill: gray }" is overridden by the coax's ".st1 { stroke: red;
    # fill: none }" (whichever <style> block appears later wins), making
    # the elbow body lose its fill and gain a red stroke.
    prefix = f"c{instance_index}_"
    rename_map: dict[str, str] = {}
    for subtree in (defs_elem, art_elem, leads_elem):
        if subtree is None:
            continue
        for el in subtree.iter():
            eid = el.get("id")
            if eid:
                new_id = prefix + eid
                el.set("id", new_id)
                rename_map[eid] = new_id

    if rename_map:
        import re
        url_re = re.compile(r"url\(#([^)]+)\)")

        def _rewrite_urls(value: str) -> str:
            return url_re.sub(
                lambda m: f"url(#{rename_map.get(m.group(1), m.group(1))})",
                value,
            )

        for subtree in (defs_elem, art_elem, leads_elem):
            if subtree is None:
                continue
            for el in subtree.iter():
                for attr, val in list(el.attrib.items()):
                    if "url(#" in val:
                        el.set(attr, _rewrite_urls(val))
                    elif attr == "href" or attr.endswith("}href"):
                        if val.startswith("#") and val[1:] in rename_map:
                            el.set(attr, "#" + rename_map[val[1:]])

    # Collect every CSS class name actually used in this component's
    # artwork/leads, then prefix all `class="…"` references and rewrite
    # the `<style>` block selectors to match. We restrict to classes that
    # are actually referenced so we don't mangle unrelated rules.
    import re
    used_classes: set[str] = set()
    for subtree in (art_elem, leads_elem):
        if subtree is None:
            continue
        for el in subtree.iter():
            cls = el.get("class")
            if cls:
                for token in cls.split():
                    used_classes.add(token)

    if used_classes:
        # Rewrite class attributes in artwork/leads.
        for subtree in (art_elem, leads_elem):
            if subtree is None:
                continue
            for el in subtree.iter():
                cls = el.get("class")
                if not cls:
                    continue
                new_tokens = [
                    (prefix + t) if t in used_classes else t
                    for t in cls.split()
                ]
                el.set("class", " ".join(new_tokens))

        # Rewrite class selectors in any <style> blocks inside <defs>.
        # Only rewrites in the *selector* portion (text before each '{')
        # so we don't accidentally touch property values.
        if defs_elem is not None:
            class_token_re = re.compile(
                r"\.(" + "|".join(re.escape(c) for c in used_classes) + r")\b"
            )

            def _prefix_selectors(css: str) -> str:
                out = []
                pos = 0
                while pos < len(css):
                    brace = css.find("{", pos)
                    if brace == -1:
                        out.append(css[pos:])
                        break
                    selectors = css[pos:brace]
                    out.append(class_token_re.sub(lambda m: "." + prefix + m.group(1), selectors))
                    end = css.find("}", brace)
                    if end == -1:
                        out.append(css[brace:])
                        break
                    out.append(css[brace:end + 1])
                    pos = end + 1
                return "".join(out)

            for el in defs_elem.iter():
                tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
                if tag == "style" and el.text:
                    el.text = _prefix_selectors(el.text)

    # Copy all defs children into the per-instance group so refs resolve.
    if defs_elem is not None and len(defs_elem) > 0:
        defs_copy = ET2.SubElement(g, "defs")
        for child in defs_elem:
            defs_copy.append(child)

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
    """Render an AnnotationItem as SVG.

    For plain-text annotations we emit a regular ``<text>`` element.
    For annotations whose source contains LaTeX math (``$...$`` or
    ``$$...$$``), we re-run the math renderer to get the rendered SVG
    bytes and inline the resulting paths into the compound — otherwise
    a flat-SVG compound would just show the raw markdown source after
    placement.
    """
    from diagrammer.items.annotation_item import _has_any_math, _render_latex_svg

    sc = annot.mapToScene(QPointF(0, 0))
    x = sc.x() - ox
    y = sc.y() - oy
    rotation = annot.rotation()

    if _has_any_math(annot.source_text):
        if _inline_math_annotation(parent, annot, x, y, rotation,
                                   _render_latex_svg):
            return
        # Fall through to plain-text export if math rendering failed.

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


def _inline_math_annotation(parent: ET.Element, annot, x: float, y: float,
                            rotation: float, render_latex_svg) -> bool:
    """Embed a rendered math annotation into the compound SVG.

    Re-runs the math renderer (ziamath / matplotlib usetex / mathtext —
    same pipeline as on-canvas display) to obtain SVG bytes, parses
    them, lifts the inner content into a wrapping ``<g>`` placed at the
    annotation's scene-relative position, and appends it to ``parent``.

    Returns True on success, False if math rendering failed (in which
    case the caller falls back to plain-text export).
    """
    import xml.etree.ElementTree as ET2

    try:
        svg_bytes = render_latex_svg(
            annot.source_text, annot.font_size, annot.text_color,
            font_family=annot.font_family,
        )
    except Exception:
        return False
    if not svg_bytes:
        return False

    try:
        root = ET2.fromstring(svg_bytes)
    except ET2.ParseError:
        return False

    # Matplotlib's SVG output declares the SVG namespace, which causes
    # ElementTree to tag every element as ``{http://www.w3.org/2000/svg}foo``.
    # When appended into the parent compound SVG (which uses the SVG
    # namespace as the *default* xmlns), ElementTree serializes them with a
    # ``ns0:`` prefix. Most browsers' SVG renderers only draw unprefixed
    # elements via the default xmlns and silently skip prefixed ones, so the
    # math becomes invisible. Strip the namespace from every tag here so
    # they serialize unprefixed and inherit the parent's default xmlns.
    _SVG_NS = "{http://www.w3.org/2000/svg}"
    _XLINK_NS = "{http://www.w3.org/1999/xlink}"
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.startswith(_SVG_NS):
            el.tag = el.tag[len(_SVG_NS):]
        # Rewrite xlink:href -> plain href (SVG2). ElementTree serializes
        # the xlink namespace under an auto-generated prefix like ns4:,
        # which some renderers don't honor for <use> resolution.
        for attr_name in list(el.attrib.keys()):
            if attr_name.startswith(_XLINK_NS):
                local = attr_name[len(_XLINK_NS):]
                if local == "href":
                    el.set("href", el.attrib.pop(attr_name))

    # Avoid id collisions across multiple math annotations in the same
    # compound. Each matplotlib render defines paths like ``id="Cmsy10-a1"``
    # inside its own <defs>. Three annotations -> three duplicate ids,
    # which the SVG spec forbids and many renderers refuse to draw. Prefix
    # every id in this annotation's tree with a per-annotation token, and
    # rewrite local ``href="#..."`` references to match.
    import uuid as _uuid
    prefix = f"a{_uuid.uuid4().hex[:8]}_"
    id_map: dict[str, str] = {}
    for el in root.iter():
        eid = el.get("id")
        if eid:
            new_id = prefix + eid
            el.set("id", new_id)
            id_map[eid] = new_id
    if id_map:
        for el in root.iter():
            href = el.get("href")
            if href and href.startswith("#"):
                target = href[1:]
                if target in id_map:
                    el.set("href", "#" + id_map[target])

    # Parse the rendered SVG's viewBox so we can position correctly.
    # ziamath emits viewBoxes with non-zero (often negative-y) origins;
    # we shift the inlined content so its top-left lands at (x, y).
    vb = root.get("viewBox", "")
    vx = vy = 0.0
    vw = vh = 0.0
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            try:
                vx, vy, vw, vh = (float(p) for p in parts)
            except ValueError:
                vx = vy = vw = vh = 0.0

    # Build the transform: rotate around (x, y) if needed, then
    # translate so the viewBox top-left lands at (x, y).
    transform_parts: list[str] = []
    if rotation:
        transform_parts.append(f"rotate({rotation:.1f},{x:.1f},{y:.1f})")
    transform_parts.append(f"translate({x - vx:.3f},{y - vy:.3f})")
    transform = " ".join(transform_parts)

    g = ET2.SubElement(parent, "g")
    g.set("transform", transform)
    g.set("data-source", "annotation-math")

    # Copy every child of the rendered SVG root into our group. We
    # include <defs> too — clipPaths/symbols/styles referenced by the
    # paths must travel with them.
    for child in list(root):
        g.append(child)

    return True


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
