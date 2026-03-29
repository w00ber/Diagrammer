"""Connection commands — undoable creation and removal of connections."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsScene

from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.port_item import PortItem


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
            # Apply the scene's default routing mode
            if hasattr(self._scene, 'default_routing_mode'):
                self._connection.routing_mode = self._scene.default_routing_mode
            # Apply style defaults from app settings
            from diagrammer.panels.settings_dialog import app_settings
            self._connection.line_width = app_settings.default_line_width
            self._connection.line_color = app_settings.default_line_color
            self._connection.corner_radius = app_settings.default_corner_radius
            # Assign to active layer
            if hasattr(self._scene, 'assign_active_layer'):
                self._scene.assign_active_layer(self._connection)
        self._scene.addItem(self._connection)

    def undo(self) -> None:
        if self._connection is not None:
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
