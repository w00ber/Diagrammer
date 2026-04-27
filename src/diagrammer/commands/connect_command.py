"""Connection commands — undoable creation and removal of connections."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsScene

from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.port_item import PortItem


def _get_lead_stroke_width(port: PortItem) -> float | None:
    """Extract the stroke width of the lead connected to this port from the SVG.

    Parses the component's SVG file, finds the leads layer, and reads
    the stroke-width from CSS classes or inline styles. Returns None
    if no leads layer exists or no stroke width is found.
    """
    import re
    import xml.etree.ElementTree as ET

    comp = port.component
    if not hasattr(comp, 'component_def'):
        return None

    cdef = comp.component_def
    try:
        tree = ET.parse(str(cdef.svg_path))
        root = tree.getroot()
    except (OSError, ET.ParseError):
        return None

    def _strip_ns(tag):
        return tag.split("}", 1)[1] if "}" in tag else tag

    # Parse CSS classes
    css_classes: dict[str, dict[str, str]] = {}
    for elem in root.iter():
        if _strip_ns(elem.tag) == "style" and elem.text:
            for m in re.finditer(r'([^{}]+)\{([^}]+)\}', elem.text):
                props = {}
                for pm in re.finditer(r'([\w-]+)\s*:\s*([^;]+)', m.group(2)):
                    props[pm.group(1).strip()] = pm.group(2).strip()
                for cm in re.finditer(r'\.(\w+)', m.group(1)):
                    css_classes.setdefault(cm.group(1), {}).update(props)

    # Find any lead layer (legacy "leads" or direction-tagged variants).
    # We pick whichever appears first; their stroke-width should match
    # since they all represent the same lead style.
    leads = None
    lead_ids = ("leads", "leads-left", "leads-right", "leads-top", "leads-bottom")
    for elem in root.iter():
        if _strip_ns(elem.tag) == "g" and elem.get("id") in lead_ids:
            leads = elem
            break

    if leads is None:
        return None

    # Check the first leaf element in leads for stroke-width
    for elem in leads.iter():
        tag = _strip_ns(elem.tag)
        if tag in ("line", "path", "polyline"):
            # Check inline style first
            style = elem.get("style", "")
            if style:
                m = re.search(r'stroke-width\s*:\s*([\d.]+)', style)
                if m:
                    return float(m.group(1))
            # Check direct attribute
            sw = elem.get("stroke-width")
            if sw:
                return float(sw.replace("px", "").replace("pt", "").strip())
            # Check CSS class
            for cls in elem.get("class", "").split():
                if cls in css_classes:
                    sw_val = css_classes[cls].get("stroke-width")
                    if sw_val:
                        return float(sw_val.replace("px", "").replace("pt", "").strip())

    return None


class CreateConnectionCommand(QUndoCommand):
    """Create a connection between two ports (undoable)."""

    def __init__(
        self,
        scene: QGraphicsScene,
        source_port: PortItem,
        target_port: PortItem,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._source_port = source_port
        self._target_port = target_port
        self._connection: ConnectionItem | None = None
        self._z: float | None = None
        src_comp = source_port.component
        tgt_comp = target_port.component
        src_label = getattr(src_comp, 'component_def', None)
        tgt_label = getattr(tgt_comp, 'component_def', None)
        src_name = f"{src_label.name}:{source_port.port_name}" if src_label else f"junction:{source_port.port_name}"
        tgt_name = f"{tgt_label.name}:{target_port.port_name}" if tgt_label else f"junction:{target_port.port_name}"
        self.setText(f"Connect {src_name} \u2192 {tgt_name}")

    @property
    def connection(self) -> ConnectionItem | None:
        return self._connection

    def redo(self) -> None:
        if self._connection is None:
            self._connection = ConnectionItem(self._source_port, self._target_port)
            # Apply style defaults — prefer scene's live routing mode
            # (always in sync with the preview) over app_settings which
            # can get out of sync during toggle/restore cycles.
            from diagrammer.panels.settings_dialog import app_settings
            if hasattr(self._scene, 'default_routing_mode'):
                self._connection.routing_mode = self._scene.default_routing_mode
            else:
                self._connection.routing_mode = app_settings.default_routing_mode
            # Try to match the lead stroke width from the source port's component
            lead_width = _get_lead_stroke_width(self._source_port)
            self._connection.line_width = lead_width if lead_width else app_settings.default_line_width
            self._connection.line_color = app_settings.default_line_color
            self._connection.corner_radius = app_settings.default_corner_radius
            # Assign to active layer
            if hasattr(self._scene, 'assign_active_layer'):
                self._scene.assign_active_layer(self._connection)
        self._scene.addItem(self._connection)
        self._scene.register_connection(self._connection)
        if self._z is None:
            self._z = self._scene.assign_item_z(self._connection)
        else:
            self._connection.setZValue(self._z)

    def undo(self) -> None:
        if self._connection is not None:
            self._scene.unregister_connection(self._connection)
            self._scene.removeItem(self._connection)


class EditWaypointsCommand(QUndoCommand):
    """Record a waypoint edit (drag/segment move) on a connection for undo/redo."""

    def __init__(
        self,
        connection: ConnectionItem,
        old_waypoints: list[QPointF],
        new_waypoints: list[QPointF],
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._connection = connection
        self._old = [QPointF(w) for w in old_waypoints]
        self._new = [QPointF(w) for w in new_waypoints]
        self.setText("Edit connection route")

    def redo(self) -> None:
        self._connection._waypoints = [QPointF(w) for w in self._new]
        self._connection.update_route()

    def undo(self) -> None:
        self._connection._waypoints = [QPointF(w) for w in self._old]
        self._connection.update_route()


class MoveVertexCommand(QUndoCommand):
    """Record a vertex drag on a connection for undo/redo."""

    def __init__(
        self,
        connection: ConnectionItem,
        vertex_index: int,
        old_pos: QPointF,
        new_pos: QPointF,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._connection = connection
        self._vertex_index = vertex_index
        self._old_pos = QPointF(old_pos)
        self._new_pos = QPointF(new_pos)
        self.setText("Move connection vertex")

    def redo(self) -> None:
        self._connection.vertices[self._vertex_index] = QPointF(self._new_pos)
        self._connection.update_route()

    def undo(self) -> None:
        self._connection.vertices[self._vertex_index] = QPointF(self._old_pos)
        self._connection.update_route()
