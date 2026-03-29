"""DiagramSerializer — save and load diagrams as .dgm JSON files."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

FORMAT_VERSION = "1.0"


class DiagramSerializer:
    """Save and load diagram state to/from JSON files."""

    @staticmethod
    def save(scene: QGraphicsScene, path: str | Path) -> None:
        """Serialize all scene items to a .dgm JSON file."""
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem, ShapeItem

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

        Path(path).write_text(json.dumps(data, indent=2))

    @staticmethod
    def load(scene: QGraphicsScene, path: str | Path, library=None) -> None:
        """Load a .dgm JSON file and populate the scene."""
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem
        from diagrammer.panels.settings_dialog import app_settings

        data = json.loads(Path(path).read_text())
        version = data.get("version", "1.0")

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
            item._layer_index = cd.get("layer", 0)
            scene.addItem(item)
            id_map[item.instance_id] = item

        # Load junctions
        for jd in data.get("junctions", []):
            item = JunctionItem(instance_id=jd.get("id"))
            item.setPos(QPointF(jd["pos"][0], jd["pos"][1]))
            item._layer_index = jd.get("layer", 0)
            scene.addItem(item)
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
            # Apply waypoints
            if cd.get("waypoints"):
                conn.vertices = [QPointF(w[0], w[1]) for w in cd["waypoints"]]
            conn._layer_index = cd.get("layer", 0)
            scene.addItem(conn)
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
            if hasattr(item, 'fill_color') and "fill_color" in sd:
                item.fill_color = QColor(sd["fill_color"])
            scene.addItem(item)

        # Update all connection routes
        from diagrammer.canvas.scene import DiagramScene
        if isinstance(scene, DiagramScene):
            scene.update_connections()


# -- Serialization helpers --

def _serialize_component(item) -> dict:
    return {
        "id": item.instance_id,
        "def_key": item.library_key,
        "pos": [item.pos().x(), item.pos().y()],
        "rotation": item.rotation_angle,
        "flip_h": item.flip_h,
        "flip_v": item.flip_v,
        "stretch_dx": item.stretch_dx,
        "stretch_dy": item.stretch_dy,
        "layer": getattr(item, '_layer_index', 0),
    }


def _serialize_junction(item) -> dict:
    return {
        "id": item.instance_id,
        "pos": [item.pos().x(), item.pos().y()],
        "layer": getattr(item, '_layer_index', 0),
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
        "layer": getattr(item, '_layer_index', 0),
    }


def _serialize_shape(item, shape_type: str) -> dict:
    d = {
        "id": item.instance_id,
        "type": shape_type,
        "pos": [item.pos().x(), item.pos().y()],
        "width": item.shape_width,
        "height": item.shape_height,
        "stroke_color": item.stroke_color.name(),
        "stroke_width": item.stroke_width,
    }
    if hasattr(item, 'fill_color'):
        d["fill_color"] = item.fill_color.name(QColor.NameFormat.HexArgb)
    d["layer"] = getattr(item, '_layer_index', 0)
    return d


def _serialize_line(item) -> dict:
    return {
        "id": item.instance_id,
        "type": "line",
        "pos": [item.pos().x(), item.pos().y()],
        "start": [item.line_start.x(), item.line_start.y()],
        "end": [item.line_end.x(), item.line_end.y()],
        "stroke_color": item.stroke_color.name(),
        "stroke_width": item.stroke_width,
        "layer": getattr(item, '_layer_index', 0),
    }
