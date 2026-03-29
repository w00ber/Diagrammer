"""DiagramView — the QGraphicsView that provides zoom, pan, grid rendering, and drop handling."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsView

from diagrammer.canvas.grid import (
    DEFAULT_GRID_SPACING,
    draw_grid,
    snap_to_grid,
)
from diagrammer.canvas.scene import DiagramScene
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.junction_item import JunctionItem
from diagrammer.items.port_item import PortItem
from diagrammer.panels.library_panel import COMPONENT_MIME_TYPE

ZOOM_MIN = 0.05
ZOOM_MAX = 20.0
ZOOM_FACTOR = 1.15


class DiagramView(QGraphicsView):
    """Zoomable, pannable view with background grid, drop support, and rubber-band selection."""

    def __init__(self, scene: DiagramScene, parent=None):
        super().__init__(scene, parent)
        self._diagram_scene = scene
        self._grid_spacing = DEFAULT_GRID_SPACING
        self._grid_visible = True
        self._snap_enabled = True
        self._panning = False
        self._pan_start = QPointF()
        self._dragging_components: list = []  # ComponentItem and/or JunctionItem
        self._drag_anchor_item = None  # the specific item clicked to start the drag
        self._drag_anchor_start_pos: QPointF | None = None  # scene pos of anchor at drag start
        self._drag_internal_conns: list = []  # connections with both ends in drag selection
        self._drag_conn_waypoints: dict = {}  # conn instance_id -> initial waypoints
        self._drag_auto_junctions: list = []  # junctions auto-included (need manual move)
        self._trace_routing = False
        self._trace_vertices: list[QPointF] = []
        self._rubber_band_active = False
        self._zoom_window_mode = False
        self._zoom_rect_start: QPointF | None = None
        self._zoom_rect_item = None  # QGraphicsRectItem for the zoom rectangle
        self._angle_snap = False
        self._angle_snap_increment = 45.0

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Enable rubber-band selection for group selection
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setAcceptDrops(True)

    # -- Properties --

    @property
    def grid_spacing(self) -> float:
        return self._grid_spacing

    @grid_spacing.setter
    def grid_spacing(self, value: float) -> None:
        self._grid_spacing = max(1.0, value)
        self.viewport().update()

    @property
    def grid_visible(self) -> bool:
        return self._grid_visible

    @grid_visible.setter
    def grid_visible(self, value: bool) -> None:
        self._grid_visible = value
        self.viewport().update()

    @property
    def snap_enabled(self) -> bool:
        return self._snap_enabled

    @snap_enabled.setter
    def snap_enabled(self, value: bool) -> None:
        self._snap_enabled = value

    @property
    def zoom_window_mode(self) -> bool:
        return self._zoom_window_mode

    @zoom_window_mode.setter
    def zoom_window_mode(self, value: bool) -> None:
        self._zoom_window_mode = value
        if value:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            # Clean up any in-progress zoom rect
            if self._zoom_rect_item:
                self.scene().removeItem(self._zoom_rect_item)
                self._zoom_rect_item = None
            self._zoom_rect_start = None

    @property
    def trace_routing(self) -> bool:
        return self._trace_routing

    @trace_routing.setter
    def trace_routing(self, value: bool) -> None:
        self._trace_routing = value
        self._trace_vertices.clear()
        if not value and self._diagram_scene.is_connecting:
            self._diagram_scene._cancel_connection()

    def current_scale(self) -> float:
        return self.transform().m11()

    def snap(self, scene_pos: QPointF) -> QPointF:
        return snap_to_grid(scene_pos, self._grid_spacing)

    def _snap_if_enabled(self, scene_pos: QPointF) -> QPointF:
        if self._snap_enabled:
            return self.snap(scene_pos)
        return scene_pos

    def _constrain_to_angle(self, pos: QPointF, origin: QPointF, shift_held: bool) -> QPointF:
        """Constrain pos relative to origin to the nearest angle increment.

        If shift_held, constrains to H or V only.
        Otherwise, if angle snap is enabled, snaps to the nearest angle increment.
        """
        import math
        dx = pos.x() - origin.x()
        dy = pos.y() - origin.y()
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return pos

        if shift_held:
            if abs(dx) >= abs(dy):
                return QPointF(pos.x(), origin.y())
            else:
                return QPointF(origin.x(), pos.y())

        if self._angle_snap:
            angle = math.atan2(dy, dx)
            inc_rad = math.radians(self._angle_snap_increment)
            snapped_angle = round(angle / inc_rad) * inc_rad
            return QPointF(
                origin.x() + dist * math.cos(snapped_angle),
                origin.y() + dist * math.sin(snapped_angle),
            )

        return pos

    def zoom_centered(self, factor: float) -> None:
        """Zoom by factor, centered on the current view center."""
        new_scale = self.current_scale() * factor
        if new_scale < ZOOM_MIN or new_scale > ZOOM_MAX:
            return
        center = self.mapToScene(self.viewport().rect().center())
        self.scale(factor, factor)
        new_center = self.mapToScene(self.viewport().rect().center())
        delta = new_center - center
        self.translate(delta.x(), delta.y())

    # -- Background grid --

    def drawBackground(self, painter: QPainter, rect) -> None:
        super().drawBackground(painter, rect)
        if self._grid_visible:
            draw_grid(painter, rect, self._grid_spacing, self.current_scale())

    # -- Zoom --

    def wheelEvent(self, event) -> None:
        angle = event.angleDelta().y()
        if angle == 0:
            return
        factor = ZOOM_FACTOR if angle > 0 else 1.0 / ZOOM_FACTOR
        new_scale = self.current_scale() * factor
        if new_scale < ZOOM_MIN or new_scale > ZOOM_MAX:
            return
        cursor_scene_pos = self.mapToScene(event.position().toPoint())
        self.scale(factor, factor)
        new_cursor_scene_pos = self.mapToScene(event.position().toPoint())
        delta = new_cursor_scene_pos - cursor_scene_pos
        self.translate(delta.x(), delta.y())

    # -- Mouse event handling --

    def _item_at_pos(self, viewport_pos) -> object:
        """Return the top scene item at the position, skipping rubberband overlays."""
        scene_pos = self.mapToScene(viewport_pos.toPoint() if hasattr(viewport_pos, 'toPoint') else viewport_pos)
        # Use items() to get all items at the position, then skip overlays
        from PySide6.QtCore import QRectF
        from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsPathItem
        for item in self.scene().items(scene_pos, Qt.ItemSelectionMode.IntersectsItemShape,
                                        Qt.SortOrder.DescendingOrder, self.transform()):
            # Skip rubberband path and dot overlays
            if item is self._diagram_scene._rubberband_path:
                continue
            if item in self._diagram_scene._rubberband_dots:
                continue
            return item
        return None

    def mousePressEvent(self, event) -> None:
        # Zoom window mode — start rectangle
        if self._zoom_window_mode and event.button() == Qt.MouseButton.LeftButton:
            self._zoom_rect_start = self.mapToScene(event.position().toPoint())
            from PySide6.QtWidgets import QGraphicsRectItem
            from PySide6.QtCore import QRectF
            self._zoom_rect_item = QGraphicsRectItem(QRectF(self._zoom_rect_start, self._zoom_rect_start))
            self._zoom_rect_item.setPen(QPen(QColor(0, 120, 215, 180), 1.0, Qt.PenStyle.DashLine))
            self._zoom_rect_item.setBrush(QBrush(QColor(0, 120, 215, 30)))
            self._zoom_rect_item.setZValue(200)
            self.scene().addItem(self._zoom_rect_item)
            event.accept()
            return

        # Middle-click pan
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            # Temporarily disable rubber-band during pan
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            event.accept()
            return

        # Right-click during connection → cancel
        if event.button() == Qt.MouseButton.RightButton:
            if self._diagram_scene.is_connecting:
                self._trace_vertices.clear()
                self._diagram_scene._cancel_connection()
                event.accept()
                return

        if event.button() == Qt.MouseButton.LeftButton:
            item = self._item_at_pos(event.position())
            scene_pos = self.mapToScene(event.position().toPoint())

            # -- Trace routing mode: place waypoints or finish --
            if self._trace_routing and self._diagram_scene.is_connecting:
                from diagrammer.items.connection_item import ConnectionItem

                # If a target port is highlighted (green pulse), clicking
                # ANYWHERE finishes the connection to that port — the user
                # doesn't need to click precisely on the small port circle.
                target_port = self._diagram_scene._current_target_port
                if target_port is not None or isinstance(item, PortItem):
                    self._diagram_scene.finish_connection_with_vertices(
                        self._trace_vertices, cursor_pos=scene_pos,
                    )
                    self._trace_vertices.clear()
                    event.accept()
                    return
                elif isinstance(item, ConnectionItem):
                    # Click on an existing wire → finish with junction
                    self._diagram_scene.finish_connection_with_vertices(
                        self._trace_vertices, cursor_pos=scene_pos,
                    )
                    self._trace_vertices.clear()
                    event.accept()
                    return
                else:
                    # Click on empty space → add a waypoint
                    snapped = self._snap_if_enabled(scene_pos)
                    if self._trace_vertices:
                        origin = self._trace_vertices[-1]
                    else:
                        origin = self._diagram_scene._connecting_from_port.scene_center()
                    shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    snapped = self._constrain_to_angle(snapped, origin, shift_held)
                    self._trace_vertices.append(snapped)
                    event.accept()
                    return

            # -- Ctrl+click on port → toggle alignment selection --
            if isinstance(item, PortItem) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._diagram_scene.toggle_alignment_port(item)
                event.accept()
                return

            # -- Shift+click on port → set as rotation pivot --
            if isinstance(item, PortItem) and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._diagram_scene.set_rotation_pivot(item)
                event.accept()
                return

            # -- Click on a port → start connection drag --
            if isinstance(item, PortItem):
                self._trace_vertices.clear()
                self._diagram_scene.begin_connection(item)
                # Disable rubber-band during connection
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                event.accept()
                return

            # -- Shift+click → toggle multi-select (components, connections, shapes, junctions) --
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                from diagrammer.items.connection_item import ConnectionItem
                from diagrammer.items.shape_item import LineItem, ShapeItem
                if isinstance(item, (ComponentItem, ConnectionItem, ShapeItem, LineItem, JunctionItem)):
                    item.setSelected(not item.isSelected())
                    event.accept()
                    return

            # -- Option/Alt+click on component → duplicate and drag --
            elif isinstance(item, ComponentItem) and event.modifiers() & Qt.KeyboardModifier.AltModifier:
                from diagrammer.commands.add_command import AddComponentCommand
                cmd = AddComponentCommand(self._diagram_scene, item.component_def, item.pos())
                self._diagram_scene.undo_stack.push(cmd)
                clone = cmd.item
                if clone:
                    # Copy transforms
                    if item.rotation_angle:
                        clone.rotate_by(item.rotation_angle)
                    if item.flip_h:
                        clone.set_flip_h(True)
                    if item.flip_v:
                        clone.set_flip_v(True)
                    # Start dragging the clone
                    self._diagram_scene.clearSelection()
                    clone.setSelected(True)
                    clone.set_snap_anchor_closest_to(scene_pos)
                    self._dragging_components = [clone]
                    self._drag_anchor_item = clone
                    self._diagram_scene.record_move_start(clone.instance_id, clone.pos())
                    self.setDragMode(QGraphicsView.DragMode.NoDrag)
                event.accept()
                return

            # -- Click on a component or junction → set up snap anchor and record move start --
            elif isinstance(item, (ComponentItem, JunctionItem)):
                if isinstance(item, ComponentItem):
                    item.set_snap_anchor_closest_to(scene_pos)
                selected = [
                    i for i in self._diagram_scene.selectedItems()
                    if isinstance(i, (ComponentItem, JunctionItem))
                ]
                if item not in selected:
                    selected = [item]

                # Auto-include connected junctions not in the selection
                # (e.g. invisible T-junction markers on wires)
                from diagrammer.items.connection_item import ConnectionItem
                selected_ids = set(id(c) for c in selected)
                self._drag_auto_junctions = []  # junctions we added that Qt won't drag
                for si in self._diagram_scene.items():
                    if not isinstance(si, ConnectionItem):
                        continue
                    src_comp = si.source_port.component
                    tgt_comp = si.target_port.component
                    if id(src_comp) in selected_ids and isinstance(tgt_comp, JunctionItem) and id(tgt_comp) not in selected_ids:
                        selected.append(tgt_comp)
                        selected_ids.add(id(tgt_comp))
                        self._drag_auto_junctions.append(tgt_comp)
                    elif id(tgt_comp) in selected_ids and isinstance(src_comp, JunctionItem) and id(src_comp) not in selected_ids:
                        selected.append(src_comp)
                        selected_ids.add(id(src_comp))
                        self._drag_auto_junctions.append(src_comp)

                self._dragging_components = selected
                self._drag_anchor_item = item  # the actual clicked item
                self._drag_anchor_start_pos = QPointF(item.pos())
                for comp in selected:
                    self._diagram_scene.record_move_start(comp.instance_id, comp.pos())

                # Capture internal connections and their initial waypoints
                comp_ids = set(id(c) for c in selected)
                self._drag_internal_conns = []
                self._drag_conn_waypoints = {}
                for si in self._diagram_scene.items():
                    if isinstance(si, ConnectionItem):
                        src_in = id(si.source_port.component) in comp_ids
                        tgt_in = id(si.target_port.component) in comp_ids
                        if src_in and tgt_in:
                            self._drag_internal_conns.append(si)
                            self._drag_conn_waypoints[si.instance_id] = [
                                QPointF(w) for w in si.vertices
                            ]

                self.setDragMode(QGraphicsView.DragMode.NoDrag)

        super().mousePressEvent(event)
        scene_pos = self.mapToScene(event.position().toPoint())
        self._diagram_scene.cursor_scene_pos_changed.emit(scene_pos.x(), scene_pos.y())

    def mouseMoveEvent(self, event) -> None:
        # Zoom window — update rectangle
        if self._zoom_window_mode and self._zoom_rect_item and self._zoom_rect_start:
            from PySide6.QtCore import QRectF
            current = self.mapToScene(event.position().toPoint())
            rect = QRectF(self._zoom_rect_start, current).normalized()
            self._zoom_rect_item.setRect(rect)
            event.accept()
            return

        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.translate(
                delta.x() / self.current_scale(),
                delta.y() / self.current_scale(),
            )
            event.accept()
            return

        # Update rubber-band polyline during connection drag
        if self._diagram_scene.is_connecting:
            scene_pos = self.mapToScene(event.position().toPoint())
            # Apply angle/shift constraint for visual preview
            if self._trace_routing:
                if self._trace_vertices:
                    origin = self._trace_vertices[-1]
                elif self._diagram_scene._connecting_from_port:
                    origin = self._diagram_scene._connecting_from_port.scene_center()
                else:
                    origin = scene_pos
                shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                scene_pos = self._constrain_to_angle(scene_pos, origin, shift_held)
            self._diagram_scene.update_connection_rubberband(
                scene_pos,
                trace_vertices=self._trace_vertices if self._trace_routing else None,
            )
            self._diagram_scene.cursor_scene_pos_changed.emit(scene_pos.x(), scene_pos.y())
            event.accept()
            return

        super().mouseMoveEvent(event)

        # Update connections if components are being dragged
        if self._dragging_components:
            # Shift internal connection waypoints by the drag delta
            if self._drag_anchor_start_pos and self._drag_anchor_item:
                delta = self._drag_anchor_item.pos() - self._drag_anchor_start_pos
                # Manually move ALL junctions in the drag group.
                # Qt's group drag may skip invisible junctions even if they're
                # selected.  setPos(start+delta) is idempotent — safe even if
                # Qt already moved them.
                for item in self._dragging_components:
                    if isinstance(item, JunctionItem):
                        start = self._diagram_scene._drag_start_positions.get(item.instance_id)
                        if start is not None:
                            item._skip_snap = True
                            item.setPos(start + delta)
                            item._skip_snap = False
                for conn in self._drag_internal_conns:
                    orig_wps = self._drag_conn_waypoints.get(conn.instance_id, [])
                    if orig_wps:
                        conn._waypoints = [
                            QPointF(w.x() + delta.x(), w.y() + delta.y())
                            for w in orig_wps
                        ]
            self._diagram_scene.update_connections()

        scene_pos = self.mapToScene(event.position().toPoint())
        self._diagram_scene.cursor_scene_pos_changed.emit(scene_pos.x(), scene_pos.y())

    def mouseReleaseEvent(self, event) -> None:
        # Zoom window — complete: zoom to the drawn rectangle
        if self._zoom_window_mode and event.button() == Qt.MouseButton.LeftButton:
            if self._zoom_rect_item and self._zoom_rect_start:
                rect = self._zoom_rect_item.rect()
                self.scene().removeItem(self._zoom_rect_item)
                self._zoom_rect_item = None
                self._zoom_rect_start = None
                # Only zoom if the rectangle is big enough (not just a click)
                if rect.width() > 5 and rect.height() > 5:
                    self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
                # Exit zoom window mode after use
                self.zoom_window_mode = False
                # Notify main window to uncheck the menu action
                self._diagram_scene.mode_changed.emit(self._diagram_scene.mode)
            event.accept()
            return

        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            # In trace routing mode, connection finishes on click in mousePressEvent
            if self._trace_routing and self._diagram_scene.is_connecting:
                event.accept()
                return

            # Finish standard connection drag
            if self._diagram_scene.is_connecting:
                release_pos = self.mapToScene(event.position().toPoint())
                self._diagram_scene.finish_connection(cursor_pos=release_pos)
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
                event.accept()
                return

            # Record move end for undo for all dragged items
            if self._dragging_components:
                self._diagram_scene.undo_stack.beginMacro("Move group")
                # Record each item's move WITHOUT triggering per-item updates
                for comp in self._dragging_components:
                    self._diagram_scene.record_move_end(comp, update=False)
                    if hasattr(comp, 'clear_snap_anchor'):
                        comp.clear_snap_anchor()
                # Record waypoint changes for internal connections
                from diagrammer.commands.connect_command import EditWaypointsCommand
                for conn in self._drag_internal_conns:
                    orig_wps = self._drag_conn_waypoints.get(conn.instance_id, [])
                    new_wps = [QPointF(w) for w in conn.vertices]
                    if orig_wps != new_wps:
                        cmd = EditWaypointsCommand(conn, orig_wps, new_wps)
                        self._diagram_scene.undo_stack.push(cmd)
                self._diagram_scene.undo_stack.endMacro()
                # Clear drag state BEFORE update so approach segments aren't suppressed
                self._dragging_components = []
                self._drag_anchor_item = None
                self._drag_internal_conns = []
                self._drag_conn_waypoints = {}
                self._drag_anchor_start_pos = None
                self._drag_auto_junctions = []
                # Now update with drag state cleared
                self._diagram_scene.update_connections()
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        super().mouseReleaseEvent(event)

        # Safety: always restore rubber-band drag mode after any mouse release,
        # in case an operation set NoDrag and didn't restore it.
        if (not self._panning
                and not self._zoom_window_mode
                and not self._diagram_scene.is_connecting
                and self.dragMode() != QGraphicsView.DragMode.RubberBandDrag):
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._zoom_window_mode:
                self.zoom_window_mode = False
                event.accept()
                return
            if self._diagram_scene.is_connecting:
                self._trace_vertices.clear()
                self._diagram_scene._cancel_connection()
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
                event.accept()
                return
            # Clear rotation pivot and alignment selection on Escape
            self._diagram_scene.clear_rotation_pivot()
            self._diagram_scene.clear_alignment_ports()
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click in trace routing mode terminates the wire at the cursor."""
        if (event.button() == Qt.MouseButton.LeftButton
                and self._trace_routing
                and self._diagram_scene.is_connecting):
            scene_pos = self.mapToScene(event.position().toPoint())
            snapped = self._snap_if_enabled(scene_pos)
            # Place an invisible endpoint junction (just for connectivity, not shown)
            from diagrammer.items.junction_item import JunctionItem
            junction = JunctionItem()
            junction.setPos(snapped)
            junction.setVisible(False)  # endpoint, not a visual junction
            self._diagram_scene.addItem(junction)
            if hasattr(self._diagram_scene, 'assign_active_layer'):
                self._diagram_scene.assign_active_layer(junction)
            self._diagram_scene._current_target_port = junction.port
            self._diagram_scene.finish_connection_with_vertices(
                self._trace_vertices, cursor_pos=snapped,
            )
            self._trace_vertices.clear()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # -- Drag-and-drop from library panel --

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME_TYPE):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME_TYPE):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME_TYPE):
            key = bytes(event.mimeData().data(COMPONENT_MIME_TYPE)).decode("utf-8")
            comp_def = self._diagram_scene.library.get(key)
            if comp_def is not None:
                scene_pos = self.mapToScene(event.position().toPoint())
                snapped = self.snap(scene_pos)
                self._diagram_scene.place_component(comp_def, snapped)
                # Record as recently used
                main_win = self.parent()
                if hasattr(main_win, '_library_panel'):
                    main_win._library_panel.record_use(key)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # -- Fit view --

    def fit_all(self) -> None:
        items_rect = self.scene().itemsBoundingRect()
        if items_rect.isNull():
            from PySide6.QtCore import QRectF
            items_rect = QRectF(-500, -500, 1000, 1000)
        else:
            margin = max(items_rect.width(), items_rect.height()) * 0.1
            items_rect.adjust(-margin, -margin, margin, margin)
        self.fitInView(items_rect, Qt.AspectRatioMode.KeepAspectRatio)
