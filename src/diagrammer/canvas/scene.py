"""DiagramScene — the QGraphicsScene that manages all diagram items and interaction modes."""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QUndoStack
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsScene

from diagrammer.models.component_def import ComponentDef
from diagrammer.models.library import ComponentLibrary


class InteractionMode(Enum):
    """The current mouse interaction mode for the scene."""
    SELECT = auto()
    PLACE = auto()
    CONNECT = auto()


# Rubber-band line style for in-progress connections
_RUBBERBAND_PEN = QPen(QColor(100, 100, 255, 180), 2.0, Qt.PenStyle.DashLine)

# Maximum distance (in scene units) to snap to a target port during connection
PORT_SNAP_DISTANCE = 20.0


class DiagramScene(QGraphicsScene):
    """Central scene that holds all diagram items.

    Manages interaction modes, owns the undo stack, and handles
    connection creation via port-to-port dragging.
    """

    mode_changed = Signal(InteractionMode)
    cursor_scene_pos_changed = Signal(float, float)  # x, y in scene coords

    def __init__(self, library: ComponentLibrary | None = None, parent=None):
        super().__init__(parent)
        self._mode = InteractionMode.SELECT
        self._undo_stack = QUndoStack(self)
        # Rebuild routes after any undo/redo so waypoints and positions stay in sync
        self._undo_stack.indexChanged.connect(self._on_undo_redo)
        self._library = library or ComponentLibrary()

        # Drag-tracking for move undo support
        self._drag_start_positions: dict[str, QPointF] = {}  # instance_id -> start pos

        # Connection-in-progress state
        self._connecting_from_port = None  # PortItem or None
        self._rubberband_path: QGraphicsPathItem | None = None
        self._rubberband_dots: list[QGraphicsEllipseItem] = []  # waypoint markers
        self._current_target_port = None   # PortItem currently highlighted as target

        # Rotation pivot port — set via Shift+click on a port
        self._rotation_pivot_port = None  # PortItem or None

        # Ports selected for alignment — set via Ctrl+click on ports
        self._alignment_ports: list = []  # list of PortItem

        # Default routing mode for new connections
        from diagrammer.items.connection_item import ROUTE_ORTHO
        self._default_routing_mode: str = ROUTE_ORTHO

        # Layer management
        from diagrammer.panels.layers_panel import LayerManager
        self._layer_manager = LayerManager()

        # Scene bounds — large default workspace
        self.setSceneRect(-5000, -5000, 10000, 10000)

    # -- Mode management --

    @property
    def mode(self) -> InteractionMode:
        return self._mode

    @mode.setter
    def mode(self, value: InteractionMode) -> None:
        if self._mode != value:
            self._cancel_connection()
            self._mode = value
            self.mode_changed.emit(value)

    @property
    def undo_stack(self) -> QUndoStack:
        return self._undo_stack

    @property
    def library(self) -> ComponentLibrary:
        return self._library

    @property
    def layer_manager(self):
        return self._layer_manager

    def apply_layer_state(self) -> None:
        """Update item visibility and selectability based on layer settings."""
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import LineItem, ShapeItem

        for item in self.items():
            layer_idx = getattr(item, '_layer_index', 0)
            if layer_idx < 0 or layer_idx >= len(self._layer_manager.layers):
                layer_idx = 0

            layer = self._layer_manager.layers[layer_idx]

            # Visibility
            if isinstance(item, (ComponentItem, ConnectionItem, JunctionItem, ShapeItem, LineItem)):
                if isinstance(item, JunctionItem):
                    # Respect show_junctions setting — never show if user hid them
                    from diagrammer.panels.settings_dialog import app_settings
                    item.setVisible(layer.visible and app_settings.show_junctions)
                else:
                    item.setVisible(layer.visible)
                # Lock: prevent selection and movement
                movable = not layer.locked
                item.setFlag(item.GraphicsItemFlag.ItemIsMovable, movable)
                item.setFlag(item.GraphicsItemFlag.ItemIsSelectable, not layer.locked)

    def assign_active_layer(self, item) -> None:
        """Assign the active layer index to a newly created item."""
        item._layer_index = self._layer_manager.active_index

    def flash_layer(self, layer_index: int) -> None:
        """Briefly highlight all items on the given layer with a selection flash."""
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import LineItem, ShapeItem
        from PySide6.QtCore import QTimer

        # Collect items on this layer
        targets = []
        for item in self.items():
            if not isinstance(item, (ComponentItem, ConnectionItem, JunctionItem, ShapeItem, LineItem)):
                continue
            if getattr(item, '_layer_index', 0) == layer_index:
                targets.append(item)

        if not targets:
            return

        # Flash: temporarily select them, then restore after 400ms
        old_selection = [i for i in self.selectedItems()]
        self.clearSelection()
        for item in targets:
            if item.isVisible():
                item.setSelected(True)

        def restore():
            self.clearSelection()
            for item in old_selection:
                if item.scene() is self:
                    item.setSelected(True)

        QTimer.singleShot(400, restore)

    @property
    def default_routing_mode(self) -> str:
        return self._default_routing_mode

    @default_routing_mode.setter
    def default_routing_mode(self, mode: str) -> None:
        self._default_routing_mode = mode
        # Update all existing connections to the new mode
        from diagrammer.items.connection_item import ConnectionItem
        for item in self.items():
            if isinstance(item, ConnectionItem):
                item.routing_mode = mode

    # -- Component placement via drop --

    def is_active_layer_locked(self) -> bool:
        """Check if the active layer is locked."""
        return self._layer_manager.active_layer.locked

    def place_component(self, component_def: ComponentDef, pos: QPointF) -> None:
        """Place a component on the scene at the given position (undoable)."""
        if self.is_active_layer_locked():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(None, "Layer Locked",
                                f"Cannot add to locked layer \"{self._layer_manager.active_layer.name}\".\n"
                                "Unlock the layer or switch to a different one.")
            return
        from diagrammer.commands.add_command import AddComponentCommand
        cmd = AddComponentCommand(self, component_def, pos)
        self._undo_stack.push(cmd)

    # -- Connection creation --

    def _all_component_items(self):
        """Yield all ComponentItem instances in the scene."""
        from diagrammer.items.component_item import ComponentItem
        for item in self.items():
            if isinstance(item, ComponentItem):
                yield item

    def _all_port_items(self):
        """Yield all PortItem instances in the scene."""
        for comp in self._all_component_items():
            yield from comp.ports

    def begin_connection(self, port) -> None:
        """Start drawing a connection from the given port."""
        from diagrammer.items.port_item import PortItem
        if not isinstance(port, PortItem):
            return
        self._connecting_from_port = port

        # Show ALL ports on ALL components so the user can see targets
        for comp in self._all_component_items():
            comp.show_all_ports()

        # Create rubber-band path (polyline that shows waypoints)
        self._rubberband_path = QGraphicsPathItem()
        self._rubberband_path.setPen(_RUBBERBAND_PEN)
        self._rubberband_path.setZValue(100)
        self.addItem(self._rubberband_path)

    def update_connection_rubberband(
        self, scene_pos: QPointF, trace_vertices: list[QPointF] | None = None,
    ) -> None:
        """Update the rubber-band polyline and highlight the nearest valid target port.

        Args:
            scene_pos: Current cursor position in scene coords.
            trace_vertices: Accumulated waypoints from trace routing mode.
        """
        if not self._rubberband_path or not self._connecting_from_port:
            return

        start = self._connecting_from_port.scene_center()

        # Find nearest valid target port
        nearest_port = self._find_nearest_target_port(scene_pos)

        # Update target highlight
        if nearest_port is not self._current_target_port:
            if self._current_target_port is not None:
                self._current_target_port.set_target_highlight(False)
            if nearest_port is not None:
                nearest_port.set_target_highlight(True)
            self._current_target_port = nearest_port

        # Determine end point — port, wire snap, or cursor
        wire_snap_pos = None
        if nearest_port is not None:
            end = nearest_port.scene_center()
        else:
            # Check for nearby wire to show junction snap preview
            wire_snap_pos = self._find_nearest_wire_snap(scene_pos)
            end = wire_snap_pos if wire_snap_pos is not None else scene_pos

        # Build the routed preview: expand each segment through ortho_route
        from diagrammer.utils.geometry import ortho_route, ortho_route_45
        waypoints = trace_vertices or []
        key_pts = [start] + list(waypoints) + [end]

        # Choose router based on current routing mode
        use_45 = getattr(self, '_default_routing_mode', 'ortho') == 'ortho_45'
        router = ortho_route_45 if use_45 else ortho_route

        # Expand each pair of consecutive key-points
        expanded: list[QPointF] = []
        for i in range(len(key_pts) - 1):
            seg = router(key_pts[i], key_pts[i + 1])
            if expanded:
                expanded.extend(seg[1:])  # skip duplicate junction
            else:
                expanded.extend(seg)

        path = QPainterPath()
        if expanded:
            path.moveTo(expanded[0])
            for pt in expanded[1:]:
                path.lineTo(pt)
        self._rubberband_path.setPath(path)

        # Draw waypoint dot markers
        self._clear_rubberband_dots()
        dot_pen = QPen(QColor(100, 100, 255), 1.0)
        dot_brush = QBrush(QColor(100, 100, 255, 200))
        for wp in waypoints:
            r = 4.0
            dot = QGraphicsEllipseItem(QRectF(-r, -r, r * 2, r * 2))
            dot.setPos(wp)
            dot.setPen(dot_pen)
            dot.setBrush(dot_brush)
            dot.setZValue(101)
            self.addItem(dot)
            self._rubberband_dots.append(dot)

        # Grid snap crosshair indicator — shows where the next waypoint would land
        if nearest_port is None and wire_snap_pos is None:
            from diagrammer.canvas.grid import snap_to_grid
            views = self.views()
            if views and getattr(views[0], '_snap_enabled', True):
                snapped = snap_to_grid(end, views[0].grid_spacing)
                cross_size = 6.0
                cross_pen = QPen(QColor(150, 150, 150, 180), 1.0)
                from PySide6.QtWidgets import QGraphicsLineItem
                h_line = QGraphicsLineItem(
                    snapped.x() - cross_size, snapped.y(),
                    snapped.x() + cross_size, snapped.y(),
                )
                h_line.setPen(cross_pen)
                h_line.setZValue(101)
                self.addItem(h_line)
                self._rubberband_dots.append(h_line)
                v_line = QGraphicsLineItem(
                    snapped.x(), snapped.y() - cross_size,
                    snapped.x(), snapped.y() + cross_size,
                )
                v_line.setPen(cross_pen)
                v_line.setZValue(101)
                self.addItem(v_line)
                self._rubberband_dots.append(v_line)

        # Wire-snap junction preview (orange dot)
        if wire_snap_pos is not None:
            r = 6.0
            jdot = QGraphicsEllipseItem(QRectF(-r, -r, r * 2, r * 2))
            jdot.setPos(wire_snap_pos)
            jdot.setPen(QPen(QColor(255, 140, 0), 2.0))
            jdot.setBrush(QBrush(QColor(255, 140, 0, 120)))
            jdot.setZValue(101)
            self.addItem(jdot)
            self._rubberband_dots.append(jdot)

    def _clear_rubberband_dots(self) -> None:
        for dot in self._rubberband_dots:
            self.removeItem(dot)
        self._rubberband_dots.clear()

    def _find_nearest_wire_snap(self, scene_pos: QPointF) -> QPointF | None:
        """Find the nearest point on an existing wire within snap distance."""
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.utils.geometry import closest_point_on_segment

        WIRE_SNAP_DIST = 15.0
        source = self._connecting_from_port

        best_proj = None
        best_dist = WIRE_SNAP_DIST

        for item in self.items():
            if not isinstance(item, ConnectionItem):
                continue
            if source and (item.source_port is source or item.target_port is source):
                continue
            pts = item.all_points()
            for i in range(len(pts) - 1):
                proj, dist = closest_point_on_segment(scene_pos, pts[i], pts[i + 1])
                if dist < best_dist:
                    best_dist = dist
                    best_proj = proj

        return best_proj

    def _find_nearest_target_port(self, scene_pos: QPointF):
        """Find the nearest valid target port within PORT_SNAP_DISTANCE of scene_pos."""
        from diagrammer.items.port_item import PortItem

        source = self._connecting_from_port
        if source is None:
            return None

        best_port = None
        best_dist = PORT_SNAP_DISTANCE

        for port in self._all_port_items():
            # Skip the source port and ports on the same component
            if port is source or port.component is source.component:
                continue
            # Skip ports that already have a connection to the source
            if self._connection_exists(source, port):
                continue

            port_center = port.scene_center()
            dx = port_center.x() - scene_pos.x()
            dy = port_center.y() - scene_pos.y()
            dist = (dx * dx + dy * dy) ** 0.5

            if dist < best_dist:
                best_dist = dist
                best_port = port

        return best_port

    def finish_connection(self, cursor_pos: QPointF | None = None) -> bool:
        """Complete the connection to the currently highlighted target port.

        Returns True if a connection was created.
        """
        return self.finish_connection_with_vertices([], cursor_pos=cursor_pos)

    def finish_connection_with_vertices(
        self, vertices: list[QPointF], cursor_pos: QPointF | None = None,
    ) -> bool:
        """Complete the connection with pre-placed intermediate vertices (trace routing).

        If no target port is highlighted, checks if the cursor is near an
        existing wire and creates a junction to terminate on it.

        Returns True if a connection was created.
        """
        target = self._current_target_port
        source = self._connecting_from_port

        if source is None:
            self._cancel_connection()
            return False

        # If no target port, try to terminate on an existing wire
        if target is None and cursor_pos is not None:
            target = self._try_junction_on_wire(cursor_pos)

        if target is None:
            self._cancel_connection()
            return False

        # Create the connection
        from diagrammer.commands.connect_command import CreateConnectionCommand
        cmd = CreateConnectionCommand(self, source, target)
        self._undo_stack.push(cmd)

        # Apply vertices if provided
        if vertices and cmd.connection:
            cmd.connection.vertices = vertices

        self._cleanup_connection_state()
        return True

    def _try_junction_on_wire(self, cursor_pos: QPointF):
        """If cursor_pos is near an existing wire, place a junction on it.

        Returns the junction's port, or None.
        """
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.utils.geometry import closest_point_on_segment

        WIRE_SNAP_DIST = 15.0
        best_conn = None
        best_proj = None
        best_dist = WIRE_SNAP_DIST

        source = self._connecting_from_port
        for item in self.items():
            if not isinstance(item, ConnectionItem):
                continue
            # Don't terminate on a wire that starts from the same port
            if item.source_port is source or item.target_port is source:
                continue
            pts = item.all_points()
            for i in range(len(pts) - 1):
                proj, dist = closest_point_on_segment(cursor_pos, pts[i], pts[i + 1])
                if dist < best_dist:
                    best_dist = dist
                    best_proj = proj
                    best_conn = item

        if best_conn is None or best_proj is None:
            return None

        # Snap the junction position to the nearest grid point if possible.
        # Grid snap takes priority — if a grid point is close to the wire
        # projection, use that instead.
        from diagrammer.canvas.grid import snap_to_grid
        views = self.views()
        if views and getattr(views[0], '_snap_enabled', True):
            grid_pos = snap_to_grid(best_proj, views[0].grid_spacing)
            # Use grid position if it's close to the wire
            from diagrammer.utils.geometry import point_distance
            grid_wire_dist = point_distance(grid_pos, best_proj)
            if grid_wire_dist < views[0].grid_spacing * 0.6:
                best_proj = grid_pos

        # Check if junctions should be shown
        from diagrammer.panels.settings_dialog import app_settings
        if not app_settings.show_junctions:
            # Still create a junction for connectivity, just hide it
            pass

        # Place a junction at the snap point on the existing wire.
        from diagrammer.items.junction_item import JunctionItem
        junction = JunctionItem()
        junction.setPos(best_proj)
        if not app_settings.show_junctions:
            junction.setVisible(False)
        self.addItem(junction)

        return junction.port

    def _cancel_connection(self) -> None:
        """Clean up connection-in-progress state without creating a connection."""
        self._cleanup_connection_state()

    def _cleanup_connection_state(self) -> None:
        """Shared cleanup for both finish and cancel."""
        # Remove rubber-band path and waypoint dots
        if self._rubberband_path:
            self.removeItem(self._rubberband_path)
            self._rubberband_path = None
        self._clear_rubberband_dots()

        # Un-highlight target
        if self._current_target_port is not None:
            self._current_target_port.set_target_highlight(False)
            self._current_target_port = None

        self._connecting_from_port = None

        # Restore normal port visibility
        for comp in self._all_component_items():
            comp.restore_port_visibility()

    def _connection_exists(self, port_a, port_b) -> bool:
        """Check if a connection already exists between two ports (in either direction)."""
        from diagrammer.items.connection_item import ConnectionItem
        for item in self.items():
            if isinstance(item, ConnectionItem):
                if (item.source_port is port_a and item.target_port is port_b) or \
                   (item.source_port is port_b and item.target_port is port_a):
                    return True
        return False

    @property
    def is_connecting(self) -> bool:
        """True if a connection drag is in progress."""
        return self._connecting_from_port is not None

    # -- Update connections when components move --

    def _on_undo_redo(self, index: int) -> None:
        """Called after undo/redo to rebuild routes with final state."""
        self.update_connections()

    def update_connections(self) -> None:
        """Rebuild routes for all connections and refresh component lead rendering."""
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        for item in self.items():
            if isinstance(item, ConnectionItem):
                item.update_route()
        # Recompute lead shortening (outside paint path to avoid perf issues)
        for item in self.items():
            if isinstance(item, ComponentItem):
                item.refresh_lead_shortening()

    # -- Move tracking for undo --

    def record_move_start(self, instance_id: str, pos: QPointF) -> None:
        """Record the starting position of a component drag."""
        self._drag_start_positions[instance_id] = QPointF(pos)

    def record_move_end(self, item, update: bool = True) -> None:
        """Record the end of a component/junction drag and push a MoveCommand if it moved.

        Args:
            update: If True (default), update connections and check auto-join.
                   Set to False during group moves to defer updates.
        """
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.junction_item import JunctionItem
        if not isinstance(item, (ComponentItem, JunctionItem)):
            return
        old_pos = self._drag_start_positions.pop(item.instance_id, None)
        if old_pos is None or old_pos == item.pos():
            return
        from diagrammer.commands.add_command import MoveComponentCommand
        cmd = MoveComponentCommand(item, old_pos, item.pos())
        self._undo_stack.push(cmd)
        if update:
            self.update_connections()
            if isinstance(item, ComponentItem):
                self.auto_join_overlapping_ports(item)

    # -- Rotation pivot port --

    @property
    def rotation_pivot_port(self):
        """The port selected as the rotation pivot (via Shift+click), or None."""
        return self._rotation_pivot_port

    def set_rotation_pivot(self, port) -> None:
        """Set a port as the rotation pivot. Highlights it visually."""
        from diagrammer.items.port_item import PortItem
        # Clear previous pivot
        if self._rotation_pivot_port is not None:
            self._rotation_pivot_port.set_target_highlight(False)
        if isinstance(port, PortItem):
            self._rotation_pivot_port = port
            port.set_target_highlight(True)
        else:
            self._rotation_pivot_port = None

    def clear_rotation_pivot(self) -> None:
        if self._rotation_pivot_port is not None:
            self._rotation_pivot_port.set_target_highlight(False)
            self._rotation_pivot_port = None

    # -- Alignment port selection (Ctrl+click) --

    @property
    def alignment_ports(self) -> list:
        """Ports currently selected for alignment."""
        return self._alignment_ports

    def toggle_alignment_port(self, port) -> None:
        """Toggle a port's alignment-selection state (Ctrl+click)."""
        from diagrammer.items.port_item import PortItem
        if not isinstance(port, PortItem):
            return
        if port in self._alignment_ports:
            port.set_alignment_selected(False)
            self._alignment_ports.remove(port)
        else:
            port.set_alignment_selected(True)
            self._alignment_ports.append(port)

    def clear_alignment_ports(self) -> None:
        """Deselect all alignment-selected ports."""
        for port in self._alignment_ports:
            port.set_alignment_selected(False)
        self._alignment_ports.clear()

    # -- Auto-join overlapping ports --

    def auto_join_overlapping_ports(self, moved_item) -> None:
        """After a component is moved, auto-create connections if its ports overlap other ports."""
        from diagrammer.items.component_item import ComponentItem
        if not isinstance(moved_item, ComponentItem):
            return

        # Use half the grid spacing as overlap tolerance so ports snapping
        # to adjacent grid lines still join
        views = self.views()
        grid_spacing = views[0].grid_spacing if views else 20.0
        PORT_OVERLAP_DISTANCE = max(5.0, grid_spacing * 0.6)

        for my_port in moved_item.ports:
            my_center = my_port.scene_center()
            for other_comp in self._all_component_items():
                if other_comp is moved_item:
                    continue
                for other_port in other_comp.ports:
                    other_center = other_port.scene_center()
                    dx = my_center.x() - other_center.x()
                    dy = my_center.y() - other_center.y()
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist < PORT_OVERLAP_DISTANCE:
                        if not self._connection_exists(my_port, other_port):
                            from diagrammer.commands.connect_command import CreateConnectionCommand
                            cmd = CreateConnectionCommand(self, my_port, other_port)
                            self._undo_stack.push(cmd)
