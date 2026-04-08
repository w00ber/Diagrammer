"""Compound manifest — save/load/instantiate a selection as a structural recipe.

A ``.dgmcomp`` file is a JSON manifest describing the sub-items that make up
a compound (components, connections, junctions, shapes, annotations) plus
their per-instance state (rotation, flip, stretch, style overrides, group
nesting).  Positions are stored relative to the compound's local origin
(top-left of the union bounding rect of the selection at save time).

When the user drops a compound from the library, every sub-item is
re-instantiated as a real, independent scene item — meaning stretchable
sub-components remain stretchable, individual styles round-trip exactly,
and the user can ungroup the compound and tweak the pieces directly.

A static ``.svg`` sibling is still written by ``compound_export.py`` so the
library panel has a thumbnail; the manifest is the authoritative source of
truth at placement time.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor

MANIFEST_VERSION = "1.0"
MANIFEST_SUFFIX = ".dgmcomp"


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_compound_manifest(
    scene,
    selected_items: list,
    output_path: Path,
    name: str,
) -> bool:
    """Serialize a selection as a compound manifest.

    Returns True on success.  ``output_path`` should end in ``.dgmcomp``.
    """
    from diagrammer.io.serializer import (
        _serialize_annotation,
        _serialize_component,
        _serialize_connection,
        _serialize_junction,
        _serialize_line,
        _serialize_shape,
    )
    from diagrammer.items.annotation_item import AnnotationItem
    from diagrammer.items.component_item import ComponentItem
    from diagrammer.items.connection_item import ConnectionItem
    from diagrammer.items.junction_item import JunctionItem
    from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem, ShapeItem

    components = [i for i in selected_items if isinstance(i, ComponentItem)]
    junctions = [i for i in selected_items if isinstance(i, JunctionItem)]
    connections = [i for i in selected_items if isinstance(i, ConnectionItem)]
    annotations = [i for i in selected_items if isinstance(i, AnnotationItem)]
    shapes = [i for i in selected_items
              if isinstance(i, (RectangleItem, EllipseItem, LineItem))]

    if not (components or annotations or shapes):
        return False

    # Pull in connections that live "inside" the selection — including
    # connections that touch JunctionItems the user did not explicitly
    # select. We do this with a fixed-point loop because newly-added
    # junctions can themselves qualify additional connections (e.g.
    # junction → bottom_inductor or junction → terminal chains). A single
    # pass over scene.items() would be order-dependent and silently drop
    # any connection visited before its junction endpoint was reachable.
    selected_ids = {id(i) for i in selected_items}
    # Seed: any connections the user explicitly selected contribute their
    # JunctionItem endpoints up front so the loop can chain through them.
    for conn in list(connections):
        for endpoint in (conn.source_port.component, conn.target_port.component):
            if isinstance(endpoint, JunctionItem):
                if endpoint not in junctions:
                    junctions.append(endpoint)
                selected_ids.add(id(endpoint))

    all_scene_connections = [
        it for it in scene.items() if isinstance(it, ConnectionItem)
    ]
    changed = True
    while changed:
        changed = False
        for item in all_scene_connections:
            if item in connections:
                continue
            src_comp = item.source_port.component
            tgt_comp = item.target_port.component
            src_in = id(src_comp) in selected_ids
            tgt_in = id(tgt_comp) in selected_ids
            # Pull in if both endpoints are already part of the compound.
            if src_in and tgt_in:
                connections.append(item)
                changed = True
                continue
            # Otherwise, if one endpoint is in the compound and the other
            # is a free JunctionItem, auto-include that junction and the
            # connection. The next pass may then chain further.
            if src_in and isinstance(tgt_comp, JunctionItem):
                connections.append(item)
                if tgt_comp not in junctions:
                    junctions.append(tgt_comp)
                selected_ids.add(id(tgt_comp))
                changed = True
            elif tgt_in and isinstance(src_comp, JunctionItem):
                connections.append(item)
                if src_comp not in junctions:
                    junctions.append(src_comp)
                selected_ids.add(id(src_comp))
                changed = True

    # Compute a local origin so saved positions are relative to (0, 0).
    all_items = components + junctions + connections + annotations + shapes
    union = None
    for item in all_items:
        rect = item.sceneBoundingRect()
        union = rect if union is None else union.united(rect)
    if union is None or union.isNull():
        return False
    ox, oy = union.x(), union.y()

    # Serialize using the existing per-item helpers, then offset positions.
    # Reusing the helpers means style overrides, stretch state, group ids,
    # waypoints, etc. all round-trip identically to the main .dgm format.
    comp_blobs = [_serialize_component(c) for c in components]
    junc_blobs = [_serialize_junction(j) for j in junctions]
    conn_blobs = [_serialize_connection(c) for c in connections]
    annot_blobs = [_serialize_annotation(a) for a in annotations]
    shape_blobs: list[dict] = []
    for s in shapes:
        if isinstance(s, RectangleItem):
            shape_blobs.append(_serialize_shape(s, "rectangle"))
        elif isinstance(s, EllipseItem):
            shape_blobs.append(_serialize_shape(s, "ellipse"))
        elif isinstance(s, LineItem):
            shape_blobs.append(_serialize_line(s))

    for blob in comp_blobs + junc_blobs + annot_blobs + shape_blobs:
        x, y = blob["pos"]
        blob["pos"] = [x - ox, y - oy]

    for blob in conn_blobs:
        blob["waypoints"] = [[wx - ox, wy - oy] for wx, wy in blob.get("waypoints", [])]

    data = {
        "version": MANIFEST_VERSION,
        "name": name,
        "size": [union.width(), union.height()],
        "components": comp_blobs,
        "junctions": junc_blobs,
        "connections": conn_blobs,
        "shapes": shape_blobs,
        "annotations": annot_blobs,
    }
    output_path = Path(output_path)
    if output_path.suffix != MANIFEST_SUFFIX:
        output_path = output_path.with_suffix(MANIFEST_SUFFIX)
    output_path.write_text(json.dumps(data, indent=2))
    return True


# ---------------------------------------------------------------------------
# Load + instantiate
# ---------------------------------------------------------------------------

def load_compound_manifest(path: Path) -> dict | None:
    """Read a manifest from disk; returns the parsed dict or None on failure."""
    try:
        return json.loads(Path(path).read_text())
    except Exception as e:
        print(f"Warning: failed to load compound manifest {path}: {e}")
        return None


def instantiate_compound(
    scene,
    manifest: dict,
    drop_pos: QPointF,
    library,
) -> list:
    """Materialize a manifest into the scene at ``drop_pos``.

    Returns the list of newly-created items so the caller can wrap them in
    a single GroupCommand or selection.  Each item is given a fresh
    instance id; connection source/target references are remapped to the
    new ids.  All previously-saved group ids inside the manifest are
    rewritten to fresh uuids so two placements of the same compound don't
    end up sharing groups.
    """
    from diagrammer.items.annotation_item import AnnotationItem
    from diagrammer.items.component_item import ComponentItem
    from diagrammer.items.connection_item import ConnectionItem
    from diagrammer.items.junction_item import JunctionItem
    from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem
    from diagrammer.models.svg_style_override import ComponentStyleOverrides

    created: list = []
    id_map: dict[str, object] = {}
    group_id_map: dict[str, str] = {}

    def _remap_groups(blob: dict) -> list[str]:
        out: list[str] = []
        for gid in blob.get("group", []) or []:
            new = group_id_map.get(gid)
            if new is None:
                new = uuid.uuid4().hex[:12]
                group_id_map[gid] = new
            out.append(new)
        return out

    def _apply_layer_and_pos(item, blob, *, set_pos=True):
        if set_pos:
            x, y = blob["pos"]
            item.setPos(QPointF(drop_pos.x() + x, drop_pos.y() + y))
        if hasattr(scene, 'assign_active_layer'):
            scene.assign_active_layer(item)
        # Group stack: existing items inside the manifest may be nested
        # already; preserve that nesting (with remapped ids) and push a
        # fresh outer group below.  The outer group is added by the caller.
        groups = _remap_groups(blob)
        item._group_ids = groups
        item._group_id = groups[-1] if groups else None

    # -- Components --
    for cd in manifest.get("components", []):
        comp_def = library.get(cd["def_key"]) if library else None
        if comp_def is None:
            continue
        item = ComponentItem(comp_def)
        if cd.get("rotation", 0):
            item.rotate_by(cd["rotation"])
        if cd.get("flip_h"):
            item.set_flip_h(True)
        if cd.get("flip_v"):
            item.set_flip_v(True)
        if cd.get("stretch_dx", 0) or cd.get("stretch_dy", 0):
            item.set_stretch(cd.get("stretch_dx", 0), cd.get("stretch_dy", 0))
        if "style_overrides" in cd:
            item.set_style_overrides(
                ComponentStyleOverrides.from_dict(cd["style_overrides"]))
        _apply_layer_and_pos(item, cd)
        scene.addItem(item)
        id_map[cd["id"]] = item
        created.append(item)

    # -- Junctions --
    for jd in manifest.get("junctions", []):
        item = JunctionItem()
        _apply_layer_and_pos(item, jd)
        scene.addItem(item)
        id_map[jd["id"]] = item
        created.append(item)

    # -- Connections --
    for cd in manifest.get("connections", []):
        src = id_map.get(cd["source"]["component_id"])
        tgt = id_map.get(cd["target"]["component_id"])
        if src is None or tgt is None:
            continue
        src_port = (src.port_by_name(cd["source"]["port_name"])
                    if hasattr(src, "port_by_name") else getattr(src, "port", None))
        tgt_port = (tgt.port_by_name(cd["target"]["port_name"])
                    if hasattr(tgt, "port_by_name") else getattr(tgt, "port", None))
        if src_port is None or tgt_port is None:
            continue
        conn = ConnectionItem(src_port, tgt_port)
        if "line_width" in cd:
            conn.line_width = cd["line_width"]
        if "line_color" in cd:
            conn.line_color = QColor(cd["line_color"])
        if "corner_radius" in cd:
            conn.corner_radius = cd["corner_radius"]
        if "routing_mode" in cd:
            conn.routing_mode = cd["routing_mode"]
        if cd.get("closed"):
            conn.closed = True
        if cd.get("waypoints"):
            conn.vertices = [
                QPointF(drop_pos.x() + wx, drop_pos.y() + wy)
                for wx, wy in cd["waypoints"]
            ]
        # Connections don't have a "pos" — skip the positional offset.
        _apply_layer_and_pos(conn, cd, set_pos=False)
        scene.addItem(conn)
        created.append(conn)

    # -- Shapes --
    for sd in manifest.get("shapes", []):
        stype = sd.get("type")
        if stype == "rectangle":
            item = RectangleItem(width=sd.get("width", 100),
                                 height=sd.get("height", 60))
        elif stype == "ellipse":
            item = EllipseItem(width=sd.get("width", 80),
                               height=sd.get("height", 80))
        elif stype == "line":
            item = LineItem(start=QPointF(sd["start"][0], sd["start"][1]),
                            end=QPointF(sd["end"][0], sd["end"][1]))
        else:
            continue
        if "stroke_color" in sd:
            item.stroke_color = QColor(sd["stroke_color"])
        if "stroke_width" in sd:
            item.stroke_width = sd["stroke_width"]
        if "dash_style" in sd:
            item.dash_style = sd["dash_style"]
        if hasattr(item, 'fill_color') and "fill_color" in sd:
            item.fill_color = QColor(sd["fill_color"])
        if hasattr(item, 'corner_radius') and "corner_radius" in sd:
            item.corner_radius = sd["corner_radius"]
        if hasattr(item, 'cap_style') and "cap_style" in sd:
            item.cap_style = sd["cap_style"]
        if hasattr(item, 'arrow_style') and "arrow_style" in sd:
            item.arrow_style = sd["arrow_style"]
        _apply_layer_and_pos(item, sd)
        scene.addItem(item)
        created.append(item)

    # -- Annotations --
    for ad in manifest.get("annotations", []):
        item = AnnotationItem()
        if "font_family" in ad:
            item.font_family = ad["font_family"]
        if "font_size" in ad:
            item.font_size = ad["font_size"]
        if "font_bold" in ad:
            item.font_bold = ad["font_bold"]
        if "font_italic" in ad:
            item.font_italic = ad["font_italic"]
        if "text_color" in ad:
            item.text_color = QColor(ad["text_color"])
        if "source_text" in ad:
            item.text_content = ad["source_text"]
        elif "html" in ad:
            item.setHtml(ad["html"])
        if ad.get("rotation"):
            item.setTransformOriginPoint(item.boundingRect().center())
            item.setRotation(ad["rotation"])
        _apply_layer_and_pos(item, ad)
        scene.addItem(item)
        created.append(item)

    return created
