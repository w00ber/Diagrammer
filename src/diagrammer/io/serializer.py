"""DiagramSerializer — save and load diagrams as .dgm JSON files."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPointF, Qt


def _restore_group(item, data: dict) -> None:
    """Restore group stack from saved data (supports old and new format)."""
    raw = data.get("group")
    if isinstance(raw, list):
        item._group_ids = raw
        item._group_id = raw[-1] if raw else None
    elif isinstance(raw, str):
        # Legacy single group_id
        item._group_ids = [raw]
        item._group_id = raw
    else:
        item._group_ids = []
        item._group_id = None
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

# ---------------------------------------------------------------------------
# .dgm file format versioning
# ---------------------------------------------------------------------------
#
# Version string is "MAJOR.MINOR".
#
#   * Bump MINOR for additive, backward-compatible changes (new optional
#     fields). Old loaders ignore unknown keys; new loaders must fall back
#     to sensible defaults when an old file omits a new field.
#
#   * Bump MAJOR for breaking changes (renames, removals, semantic shifts,
#     restructured nesting). Each MAJOR bump must add a migration step in
#     ``_migrate`` that upgrades the previous MAJOR to the current one.
#
# Loader policy (see ``DiagramSerializer.load``):
#
#   * file MAJOR > code MAJOR  ->  refuse to load (file is from the future)
#   * file MAJOR < code MAJOR  ->  run migrations in sequence
#   * file MINOR > code MINOR  ->  load anyway (forward-compat best effort)
#   * file MINOR < code MINOR  ->  load anyway (defaults fill missing fields)
#
# When you bump the version, re-save the bundled examples by running
# ``python tools/resave_examples.py`` so they ship in the current format.
#
# Version history:
#   1.0 - initial format
# ---------------------------------------------------------------------------

FORMAT_MAJOR = 1
FORMAT_MINOR = 0
FORMAT_VERSION = f"{FORMAT_MAJOR}.{FORMAT_MINOR}"


def _parse_version(v: str) -> tuple[int, int]:
    """Parse a 'MAJOR.MINOR' version string into a (major, minor) tuple.

    Tolerates missing minor ('1' -> (1, 0)) and ignores trailing junk.
    Returns (1, 0) for unparseable input so legacy files load by default.
    """
    try:
        parts = str(v).split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return major, minor
    except (ValueError, IndexError):
        return 1, 0


def _migrate(data: dict, from_major: int) -> dict:
    """Upgrade *data* from ``from_major`` to the current ``FORMAT_MAJOR``.

    Each step migrates one major version forward and is registered below.
    No-op when ``from_major == FORMAT_MAJOR``.
    """
    # No migrations registered yet — when adding the first MAJOR bump,
    # add an ``if from_major == 1: data = _migrate_1_to_2(data)`` line here.
    return data


class _IncompatibleFormatError(Exception):
    """Raised when a .dgm file was saved by a newer MAJOR version."""


class DiagramSerializer:
    """Save and load diagram state to/from JSON files."""

    @staticmethod
    def save(scene: QGraphicsScene, path: str | Path) -> None:
        """Serialize all scene items to a .dgm JSON file."""
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem, ShapeItem

        # Normalize z-values before saving to capture actual stacking order.
        # scene.items() returns descending stacking order (topmost first).
        # Items with equal z-values are ordered by insertion order in Qt,
        # which is lost on reload — so we assign unique z-values now.
        all_items = scene.items(Qt.SortOrder.AscendingOrder)  # bottom to top
        for i, item in enumerate(all_items):
            if isinstance(item, (ComponentItem, ConnectionItem, JunctionItem,
                                 RectangleItem, EllipseItem, LineItem, AnnotationItem)):
                item.setZValue(float(i))

        # Save layers
        from diagrammer.canvas.scene import DiagramScene
        layers_data = []
        if isinstance(scene, DiagramScene):
            layers_data = scene.layer_manager.to_list()

        data: dict = {
            "version": FORMAT_VERSION,
            "layers": layers_data,
            "components": [],
            "connections": [],
            "junctions": [],
            "shapes": [],
            "annotations": [],
        }

        # Serialize components
        for item in scene.items():
            if isinstance(item, ComponentItem):
                data["components"].append(_serialize_component(item))

        # Serialize junctions
        for item in scene.items():
            if isinstance(item, JunctionItem):
                data["junctions"].append(_serialize_junction(item))

        # Serialize connections (after components/junctions so IDs are known)
        for item in scene.items():
            if isinstance(item, ConnectionItem):
                data["connections"].append(_serialize_connection(item))

        # Serialize shapes
        for item in scene.items():
            if isinstance(item, RectangleItem):
                data["shapes"].append(_serialize_shape(item, "rectangle"))
            elif isinstance(item, EllipseItem):
                data["shapes"].append(_serialize_shape(item, "ellipse"))
            elif isinstance(item, LineItem):
                data["shapes"].append(_serialize_line(item))

        # Serialize annotations
        for item in scene.items():
            if isinstance(item, AnnotationItem):
                data["annotations"].append(_serialize_annotation(item))

        Path(path).write_text(json.dumps(data, indent=2))

    @staticmethod
    def load(scene: QGraphicsScene, path: str | Path, library=None) -> None:
        """Load a .dgm JSON file and populate the scene."""
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem
        from diagrammer.panels.settings_dialog import app_settings

        data = json.loads(Path(path).read_text())
        file_major, file_minor = _parse_version(data.get("version", "1.0"))

        if file_major > FORMAT_MAJOR:
            raise _IncompatibleFormatError(
                f"Cannot open '{Path(path).name}': file format version "
                f"{file_major}.{file_minor} is newer than this build supports "
                f"(max {FORMAT_VERSION}). Please update Diagrammer."
            )
        if file_major < FORMAT_MAJOR:
            data = _migrate(data, file_major)

        # Clear the scene
        scene.clear()

        # Restore layers
        from diagrammer.canvas.scene import DiagramScene
        if isinstance(scene, DiagramScene) and "layers" in data:
            scene.layer_manager.from_list(data["layers"])

        # ID → item mapping for resolving connection references
        id_map: dict[str, object] = {}

        # Load components
        for cd in data.get("components", []):
            if library is None:
                continue
            comp_def = library.get(cd["def_key"])
            if comp_def is None:
                continue
            item = ComponentItem(comp_def, instance_id=cd.get("id"))
            item.setPos(QPointF(cd["pos"][0], cd["pos"][1]))
            if cd.get("rotation", 0):
                item.rotate_by(cd["rotation"])
            if cd.get("flip_h"):
                item.set_flip_h(True)
            if cd.get("flip_v"):
                item.set_flip_v(True)
            if cd.get("stretch_dx", 0) or cd.get("stretch_dy", 0):
                item.set_stretch(cd.get("stretch_dx", 0), cd.get("stretch_dy", 0))
            # Restore per-instance style overrides
            if "style_overrides" in cd:
                from diagrammer.models.svg_style_override import ComponentStyleOverrides
                item.set_style_overrides(ComponentStyleOverrides.from_dict(cd["style_overrides"]))
            item._layer_index = cd.get("layer", 0)
            scene.addItem(item)
            if "z" in cd:
                item.setZValue(cd["z"])
            _restore_group(item, cd)
            id_map[item.instance_id] = item

        # Load junctions
        for jd in data.get("junctions", []):
            item = JunctionItem(instance_id=jd.get("id"))
            item.setPos(QPointF(jd["pos"][0], jd["pos"][1]))
            item._layer_index = jd.get("layer", 0)
            scene.addItem(item)
            if "z" in jd:
                item.setZValue(jd["z"])
            _restore_group(item, jd)
            id_map[item.instance_id] = item

        # Load connections
        for cd in data.get("connections", []):
            src_comp = id_map.get(cd["source"]["component_id"])
            tgt_comp = id_map.get(cd["target"]["component_id"])
            if src_comp is None or tgt_comp is None:
                continue

            # Find ports
            src_port = None
            tgt_port = None
            if hasattr(src_comp, 'port_by_name'):
                src_port = src_comp.port_by_name(cd["source"]["port_name"])
            elif hasattr(src_comp, 'port'):
                src_port = src_comp.port  # JunctionItem
            if hasattr(tgt_comp, 'port_by_name'):
                tgt_port = tgt_comp.port_by_name(cd["target"]["port_name"])
            elif hasattr(tgt_comp, 'port'):
                tgt_port = tgt_comp.port

            if src_port is None or tgt_port is None:
                continue

            conn = ConnectionItem(src_port, tgt_port, instance_id=cd.get("id"))
            # Apply style
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
            # Apply waypoints
            if cd.get("waypoints"):
                conn.vertices = [QPointF(w[0], w[1]) for w in cd["waypoints"]]
            conn._layer_index = cd.get("layer", 0)
            scene.addItem(conn)
            if "z" in cd:
                conn.setZValue(cd["z"])
            _restore_group(conn, cd)
            id_map[conn.instance_id] = conn

        # Load shapes
        for sd in data.get("shapes", []):
            shape_type = sd.get("type")
            if shape_type == "rectangle":
                item = RectangleItem(
                    width=sd.get("width", 100),
                    height=sd.get("height", 60),
                    instance_id=sd.get("id"),
                )
            elif shape_type == "ellipse":
                item = EllipseItem(
                    width=sd.get("width", 80),
                    height=sd.get("height", 80),
                    instance_id=sd.get("id"),
                )
            elif shape_type == "line":
                item = LineItem(
                    start=QPointF(sd["start"][0], sd["start"][1]),
                    end=QPointF(sd["end"][0], sd["end"][1]),
                    instance_id=sd.get("id"),
                )
            else:
                continue

            item.setPos(QPointF(sd["pos"][0], sd["pos"][1]))
            item._layer_index = sd.get("layer", 0)
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
            # Line-specific properties
            if hasattr(item, 'cap_style') and "cap_style" in sd:
                item.cap_style = sd["cap_style"]
            if hasattr(item, 'arrow_style') and "arrow_style" in sd:
                item.arrow_style = sd["arrow_style"]
            if hasattr(item, 'arrow_type') and "arrow_type" in sd:
                item.arrow_type = sd["arrow_type"]
            if hasattr(item, 'arrow_scale') and "arrow_scale" in sd:
                item.arrow_scale = sd["arrow_scale"]
            if hasattr(item, 'arrow_extend') and "arrow_extend" in sd:
                item.arrow_extend = sd["arrow_extend"]
            scene.addItem(item)
            if "z" in sd:
                item.setZValue(sd["z"])
            _restore_group(item, sd)

        # Load annotations
        for ad in data.get("annotations", []):
            item = AnnotationItem(instance_id=ad.get("id"))
            # Restore font properties BEFORE setting text (affects math rendering)
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
            # Use source_text if available (preserves $..$ math for re-editing)
            if "source_text" in ad:
                item.text_content = ad["source_text"]
            elif "html" in ad:
                item.setHtml(ad["html"])
            elif "text" in ad:
                item.setPlainText(ad["text"])
            if ad.get("rotation"):
                item.setTransformOriginPoint(item.boundingRect().center())
                item.setRotation(ad["rotation"])
            item.setPos(QPointF(ad["pos"][0], ad["pos"][1]))
            item._layer_index = ad.get("layer", 0)
            scene.addItem(item)
            if "z" in ad:
                item.setZValue(ad["z"])
            _restore_group(item, ad)

        # Update all connection routes
        from diagrammer.canvas.scene import DiagramScene
        if isinstance(scene, DiagramScene):
            scene.update_connections()


# -- Serialization helpers --

def _serialize_component(item) -> dict:
    d = {
        "id": item.instance_id,
        "def_key": item.library_key,
        "pos": [item.pos().x(), item.pos().y()],
        "rotation": item.rotation_angle,
        "flip_h": item.flip_h,
        "flip_v": item.flip_v,
        "stretch_dx": item.stretch_dx,
        "stretch_dy": item.stretch_dy,
        "layer": getattr(item, '_layer_index', 0),
        "z": item.zValue(),
        "group": getattr(item, '_group_ids', []) or [],
    }
    # Only serialize style overrides if non-empty
    if hasattr(item, '_style_overrides') and not item.style_overrides.is_empty():
        d["style_overrides"] = item.style_overrides.to_dict()
    return d


def _serialize_junction(item) -> dict:
    return {
        "id": item.instance_id,
        "pos": [item.pos().x(), item.pos().y()],
        "layer": getattr(item, '_layer_index', 0),
        "z": item.zValue(),
        "group": getattr(item, '_group_ids', []) or [],
    }


def _serialize_connection(item) -> dict:
    src_port = item.source_port
    tgt_port = item.target_port

    return {
        "id": item.instance_id,
        "source": {
            "component_id": src_port.component.instance_id,
            "port_name": src_port.port_name,
        },
        "target": {
            "component_id": tgt_port.component.instance_id,
            "port_name": tgt_port.port_name,
        },
        "waypoints": [[w.x(), w.y()] for w in item.vertices],
        "line_width": item.line_width,
        "line_color": item.line_color.name(),
        "corner_radius": item.corner_radius,
        "routing_mode": item.routing_mode,
        "closed": item.closed,
        "layer": getattr(item, '_layer_index', 0),
        "z": item.zValue(),
        "group": getattr(item, '_group_ids', []) or [],
    }


def _serialize_shape(item, shape_type: str) -> dict:
    d = {
        "id": item.instance_id,
        "type": shape_type,
        "pos": [item.pos().x(), item.pos().y()],
        "width": item.shape_width,
        "height": item.shape_height,
        "stroke_color": item.stroke_color.name(QColor.NameFormat.HexArgb),
        "stroke_width": item.stroke_width,
        "dash_style": item.dash_style,
    }
    if hasattr(item, 'fill_color'):
        d["fill_color"] = item.fill_color.name(QColor.NameFormat.HexArgb)
    if hasattr(item, 'corner_radius'):
        d["corner_radius"] = item.corner_radius
    d["layer"] = getattr(item, '_layer_index', 0)
    d["z"] = item.zValue()
    d["group"] = getattr(item, '_group_ids', []) or []
    return d


def _serialize_line(item) -> dict:
    return {
        "id": item.instance_id,
        "type": "line",
        "pos": [item.pos().x(), item.pos().y()],
        "start": [item.line_start.x(), item.line_start.y()],
        "end": [item.line_end.x(), item.line_end.y()],
        "stroke_color": item.stroke_color.name(QColor.NameFormat.HexArgb),
        "stroke_width": item.stroke_width,
        "dash_style": item.dash_style,
        "cap_style": item.cap_style,
        "arrow_style": item.arrow_style,
        "arrow_type": item.arrow_type,
        "arrow_scale": item.arrow_scale,
        "arrow_extend": item.arrow_extend,
        "layer": getattr(item, '_layer_index', 0),
        "z": item.zValue(),
        "group": getattr(item, '_group_ids', []) or [],
    }


def _serialize_annotation(item) -> dict:
    return {
        "id": item.instance_id,
        "pos": [item.pos().x(), item.pos().y()],
        "source_text": item.source_text,
        "html": item.html_content,
        "font_family": item.font_family,
        "font_size": item.font_size,
        "font_bold": item.font_bold,
        "font_italic": item.font_italic,
        "text_color": item.text_color.name(),
        "rotation": item.rotation(),
        "layer": getattr(item, '_layer_index', 0),
        "z": item.zValue(),
        "group": getattr(item, '_group_ids', []) or [],
    }
