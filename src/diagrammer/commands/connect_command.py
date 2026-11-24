"""Connection commands — undoable creation and removal of connections."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsScene

from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.port_item import PortItem


def lead_style_of(cdef, pdef):
    """The ``LeadStyle`` a part definition draws at one of its ports, as
    the library file has it: the lead that ends at that port, else the
    part's first lead (``ComponentDef.default_lead``)."""
    from diagrammer.models.lead_style import LeadStyle
    if pdef is not None and (pdef.lead_width is not None
                             or pdef.lead_color is not None):
        fallback = cdef.default_lead
        return LeadStyle(
            pdef.lead_width if pdef.lead_width is not None
            else getattr(fallback, "width", None),
            pdef.lead_color if pdef.lead_color is not None
            else getattr(fallback, "color", None))
    return cdef.default_lead


def lead_style_at(port: PortItem):
    """The ``LeadStyle`` of the lead a wire on *port* continues, or None.

    The lead that ends at this very port when there is one; otherwise the
    part's first lead (``ComponentDef.default_lead``), which is what every
    port used to get. A junction has no lead of its own: None.
    """
    comp = getattr(port, "component", None)
    cdef = getattr(comp, "component_def", None)
    if cdef is None:
        return None
    return lead_style_of(cdef, getattr(port, "port_def", None))


def _get_lead_stroke_width(port: PortItem) -> float | None:
    """Stroke width of the lead a wire on *port* continues (see
    :func:`lead_style_at`). Kept under its old name: svglint's messages and
    the headless API refer to it."""
    style = lead_style_at(port)
    return style.width if style is not None else None


def wire_width_for(source: PortItem, target: PortItem) -> float | None:
    """The lead width a new wire between *source* and *target* should take.

    The source end's lead when it has one, else the target's: a wire drawn
    *from* a junction or a lead-less terminal used to fall straight to the
    global default even when its other end was a part with a 3 px lead.
    """
    for port in (source, target):
        w = _get_lead_stroke_width(port)
        if w:
            return w
    return None


class AddJunctionCommand(QUndoCommand):
    """Add a JunctionItem to the scene (undoable).

    Junctions that anchor wire endpoints (trace started in empty space,
    double-click terminators, T-junctions on a wire) must ride the undo
    stack together with their connection — otherwise undoing the wire
    leaks an invisible junction into the scene and the saved file.

    Tolerates the junction already being in the scene on the first
    redo() (creation sites add it eagerly so the connection preview can
    reference its port).
    """

    def __init__(
        self,
        scene: QGraphicsScene,
        junction,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._junction = junction
        self._pos = junction.pos()
        self.setText("Add junction")

    def redo(self) -> None:
        if self._junction.scene() is None:
            self._scene.addItem(self._junction)
        self._junction.setPos(self._pos)

    def undo(self) -> None:
        if self._junction.scene() is self._scene:
            self._scene.removeItem(self._junction)


class SetCrossoverStyleCommand(QUndoCommand):
    """Set/replace/remove the crossover override for a pair of wires.

    The override lives in a scene-level dict keyed by the unordered pair
    of instance_ids (see DiagramScene._crossover_overrides), not as an
    item attribute — so the generic ChangeStyleCommand cannot be reused.
    Pasted wires get fresh instance_ids, so overrides do not follow
    copy/paste.
    """

    def __init__(
        self,
        scene,
        conn_a,
        conn_b,
        old_entry: dict | None,
        new_entry: dict | None,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._conn_a = conn_a
        self._conn_b = conn_b
        self._old = dict(old_entry) if old_entry else None
        self._new = dict(new_entry) if new_entry else None
        self.setText("Change wire crossing style")

    def redo(self) -> None:
        self._apply(self._new)

    def undo(self) -> None:
        self._apply(self._old)

    def _apply(self, entry: dict | None) -> None:
        self._scene.set_crossover_override(
            self._conn_a.instance_id, self._conn_b.instance_id, entry,
        )
        # Both wires rebuild — ownership may flip which one paints the arc
        for conn in (self._conn_a, self._conn_b):
            if conn.scene() is self._scene:
                conn.update_route()


class CreateConnectionCommand(QUndoCommand):
    """Create a connection between two ports (undoable)."""

    def __init__(
        self,
        scene: QGraphicsScene,
        source_port: PortItem,
        target_port: PortItem,
        parent: QUndoCommand | None = None,
        *,
        style: tuple[float | None, str | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._source_port = source_port
        self._target_port = target_port
        # (width, colour) chosen by the caller — Regularize Wiring passes the
        # leads' — applied on first redo so it is part of this undo step.
        # None per attribute keeps the default behaviour.
        self._style = style
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
            # Match the lead the wire continues: the source end's, or the
            # target's when the source is a junction or has no lead.
            lead_width = wire_width_for(self._source_port, self._target_port)
            self._connection.line_width = lead_width if lead_width else app_settings.default_line_width
            self._connection.line_color = app_settings.default_line_color
            if self._style is not None:
                width, color = self._style
                if width is not None:
                    self._connection.line_width = width
                if color is not None:
                    from PySide6.QtGui import QColor
                    self._connection.line_color = QColor(color)
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
    """Record a waypoint edit on a connection for undo/redo.

    Waypoints are scene coordinates and nothing re-binds them, so this
    restores the same shape whichever order a macro replays its
    children in — a port move before or after makes no difference.
    """

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
        self._connection._set_waypoints_from_scene(self._new)
        self._connection.update_route()

    def undo(self) -> None:
        self._connection._set_waypoints_from_scene(self._old)
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
        self._connection._set_waypoint_at(self._vertex_index, self._new_pos)
        self._connection.update_route()

    def undo(self) -> None:
        self._connection._set_waypoint_at(self._vertex_index, self._old_pos)
        self._connection.update_route()


class SetRoutingModeCommand(QUndoCommand):
    """Switch a connection's routing_mode (undoable).

    Used by transform_ops when fine-rotating a wire by a non-90° angle:
    the orthogonal router can't represent the rotated shape, so the
    rotation code switches the wire to ROUTE_DIRECT. This wraps that
    one-line mutation so undo restores the previous routing mode.
    """

    def __init__(
        self,
        connection: ConnectionItem,
        new_mode: str,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._connection = connection
        self._old_mode = connection.routing_mode
        self._new_mode = new_mode
        self.setText("Switch wire routing mode")

    def redo(self) -> None:
        self._connection.routing_mode = self._new_mode

    def undo(self) -> None:
        self._connection.routing_mode = self._old_mode
