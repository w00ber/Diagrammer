"""DiagramView — the QGraphicsView that provides zoom, pan, grid rendering, and drop handling."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsView

from diagrammer.canvas.grid import (
    DEFAULT_GRID_SPACING,
    draw_grid,
    snap_to_grid,
)
from diagrammer.canvas.scene import DiagramScene
from diagrammer.items.annotation_item import AnnotationItem
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.connection_item import ConnectionItem
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
        self._drag_mouse_start: QPointF | None = None
        # Unified waypoint+component drag state
        self._selected_waypoint_set: dict[str, set[int]] = {}  # conn instance_id → selected wp indices
        self._selected_waypoint_conns: dict = {}  # conn instance_id → ConnectionItem
        self._unified_drag_active = False
        self._unified_drag_waypoint_starts: dict[str, list[QPointF]] = {}
        self._trace_routing = False
        self._trace_vertices: list[QPointF] = []
        self._extending_wire = None      # ConnectionItem being extended
        self._extending_end: str = ""    # "source" or "target"
        self._rubber_band_active = False
        self._rubber_band_rect = QRectF()  # scene-coords rect of last rubber-band
        self._zoom_window_mode = False
        self._zoom_rect_start: QPointF | None = None
        self._zoom_rect_item = None  # QGraphicsRectItem for the zoom rectangle
        self._angle_snap = False
        self._angle_snap_increment = 45.0

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        # Force a light canvas regardless of the chrome theme. The
        # diagram artwork, grid, and component SVGs are all authored
        # against a white background, so even in dark mode the canvas
        # itself stays light for legibility.
        from diagrammer.app import CANVAS_BACKGROUND
        self.setBackgroundBrush(QBrush(CANVAS_BACKGROUND))
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Enable rubber-band selection for group selection
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.rubberBandChanged.connect(self._on_rubber_band_changed)
        self.setAcceptDrops(True)
        scene.selectionChanged.connect(self._on_scene_selection_changed)
        # Accept native gestures (pinch-to-zoom on trackpad)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.grabGesture(Qt.GestureType.PinchGesture)

    # -- Rubber-band tracking --

    def _on_rubber_band_changed(self, rubberBandRect, fromScenePoint, toScenePoint):  # noqa: ANN001, N803
        """Track the rubber-band rect in scene coordinates."""
        if not rubberBandRect.isNull():
            self._rubber_band_rect = self.mapToScene(rubberBandRect).boundingRect()
        # When the rect goes null, the drag has ended — keep the last valid rect

    # -- Unified waypoint selection helpers --

    def _sync_waypoint_selection(self) -> None:
        """Rebuild unified waypoint selection from individual ConnectionItems."""
        self._selected_waypoint_set.clear()
        self._selected_waypoint_conns.clear()
        for item in self._diagram_scene.selectedItems():
            if isinstance(item, ConnectionItem) and item._selected_waypoints:
                self._selected_waypoint_set[item.instance_id] = set(item._selected_waypoints)
                self._selected_waypoint_conns[item.instance_id] = item

    def _on_scene_selection_changed(self) -> None:
        """Remove stale entries when connections are deselected.

        Guarded against a shutdown race: during window close Qt can
        emit ``selectionChanged`` one last time while the scene's C++
        object is being torn down (e.g. as child items are deleted
        and their selection state flips). At that point the Python
        wrapper ``self._diagram_scene`` points at an already-deleted
        C++ object, and calling ``selectedItems()`` on it raises
        ``RuntimeError: Internal C++ object (DiagramScene) already
        deleted.`` We swallow that specific case and bail out — the
        view is about to go away anyway.
        """
        try:
            scene_items = self._diagram_scene.selectedItems()
        except RuntimeError:
            return
        selected_conn_ids = set()
        for item in scene_items:
            if isinstance(item, ConnectionItem):
                selected_conn_ids.add(item.instance_id)
        stale = [cid for cid in self._selected_waypoint_set if cid not in selected_conn_ids]
        for cid in stale:
            del self._selected_waypoint_set[cid]
            self._selected_waypoint_conns.pop(cid, None)

    def _find_selected_waypoint_at(self, scene_pos: QPointF):
        """Check if scene_pos is near a waypoint in the unified selection.

        Returns (ConnectionItem, waypoint_index) or (None, None).
        """
        from diagrammer.utils.geometry import point_distance
        for conn_id, indices in self._selected_waypoint_set.items():
            conn = self._selected_waypoint_conns.get(conn_id)
            if conn is None:
                continue
            for idx in indices:
                if 0 <= idx < len(conn.vertices):
                    if point_distance(scene_pos, conn.vertices[idx]) < 16.0:
                        return conn, idx
        return None, None

    def _find_any_waypoint_at(self, scene_pos: QPointF):
        """Find any waypoint handle near scene_pos on any selected connection."""
        for item in self._diagram_scene.selectedItems():
            if isinstance(item, ConnectionItem):
                wi = item._find_waypoint_at(scene_pos)
                if wi is not None:
                    return item, wi
        return None, None

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
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            # Override cursor for zoom mode (setDragMode→_update_cursor skips zoom)
            zoom_cursor = self._make_zoom_cursor(zoom_in=True)
            self.setCursor(zoom_cursor)
            self.viewport().setCursor(zoom_cursor)
        else:
            # Clean up any in-progress zoom rect
            if self._zoom_rect_item:
                self.scene().removeItem(self._zoom_rect_item)
                self._zoom_rect_item = None
            self._zoom_rect_start = None
            # Restore normal drag mode — _update_cursor handles the cursor
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        # Notify main window to update mode label and checkbox
        self._diagram_scene.mode_changed.emit(self._diagram_scene.mode)

    @staticmethod
    def _make_zoom_cursor(zoom_in: bool = True):
        """Create a magnifying glass cursor with + or - sign."""
        from PySide6.QtGui import QCursor, QPixmap
        size = 28
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Lens circle
        p.setPen(QPen(QColor(0, 0, 0), 2))
        p.setBrush(QBrush(QColor(255, 255, 255, 160)))
        p.drawEllipse(1, 1, 18, 18)
        # Handle
        p.setPen(QPen(QColor(60, 60, 60), 3))
        p.drawLine(16, 16, 25, 25)
        # Plus or minus sign inside the lens
        p.setPen(QPen(QColor(0, 0, 0), 2))
        p.drawLine(7, 10, 13, 10)  # horizontal bar (both + and -)
        if zoom_in:
            p.drawLine(10, 7, 10, 13)  # vertical bar (+ only)
        p.end()
        return QCursor(pixmap, 10, 10)

    @property
    def trace_routing(self) -> bool:
        return self._trace_routing

    @trace_routing.setter
    def trace_routing(self, value: bool) -> None:
        self._trace_routing = value
        self._trace_vertices.clear()
        if not value and self._diagram_scene.is_connecting:
            self._diagram_scene._cancel_connection()
        self._update_cursor()

    def setDragMode(self, mode) -> None:  # noqa: N802, ANN001
        """Override to re-apply the mode-appropriate cursor after Qt resets it."""
        super().setDragMode(mode)
        self._update_cursor()

    def _update_cursor(self) -> None:
        """Set the cursor to match the current interaction mode."""
        if self._zoom_window_mode:
            return  # zoom window manages its own cursor
        if self._panning:
            return  # pan manages its own cursor
        if self._trace_routing:
            cursor = Qt.CursorShape.CrossCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)
        self.viewport().setCursor(cursor)

    def current_scale(self) -> float:
        return self.transform().m11()

    def snap(self, scene_pos: QPointF) -> QPointF:
        return snap_to_grid(scene_pos, self._grid_spacing)

    def _snap_if_enabled(self, scene_pos: QPointF) -> QPointF:
        if self._snap_enabled:
            return self.snap(scene_pos)
        return scene_pos

    def _find_wire_endpoint_for_extend(self, scene_pos: QPointF):
        """Find an existing wire endpoint near *scene_pos* for extension.

        Returns (ConnectionItem, end_name, port) where end_name is 'source'
        or 'target', or (None, '', None) if nothing nearby.
        """
        from diagrammer.utils.geometry import point_distance
        ENDPOINT_SNAP = 15.0
        best_conn = None
        best_end = ""
        best_port = None
        best_dist = ENDPOINT_SNAP
        for item in self._diagram_scene.items():
            if not isinstance(item, ConnectionItem):
                continue
            for end_name, port in [("source", item.source_port), ("target", item.target_port)]:
                d = point_distance(scene_pos, port.scene_center())
                if d < best_dist:
                    best_dist = d
                    best_conn = item
                    best_end = end_name
                    best_port = port
        return best_conn, best_end, best_port

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

    def zoom_at(self, factor: float, scene_pos: QPointF) -> None:
        """Zoom by factor, keeping scene_pos visually stationary.

        Uses the same anchor technique as the original wheelEvent:
        record the viewport pixel position, scale, then measure how
        that pixel now maps to a different scene point, and translate
        to compensate.
        """
        new_scale = self.current_scale() * factor
        if new_scale < ZOOM_MIN or new_scale > ZOOM_MAX:
            return
        # Convert scene_pos to a viewport pixel BEFORE scaling
        view_point = self.mapFromScene(scene_pos)
        self.scale(factor, factor)
        # That same viewport pixel now maps to a new scene point
        new_scene_pos = self.mapToScene(view_point)
        # Translate so the original scene_pos lands back under that pixel
        delta = new_scene_pos - scene_pos
        self.translate(delta.x(), delta.y())

    # -- Background grid --

    def drawBackground(self, painter: QPainter, rect) -> None:
        super().drawBackground(painter, rect)
        if self._grid_visible:
            draw_grid(painter, rect, self._grid_spacing, self.current_scale())

    def drawForeground(self, painter: QPainter, rect) -> None:
        """Draw group bounding box when a group is selected."""
        super().drawForeground(painter, rect)
        selected = self._diagram_scene.selectedItems()
        if not selected:
            return
        # Check if any selected item belongs to a group
        from diagrammer.commands.group_command import get_top_group
        group_ids = set()
        for item in selected:
            gid = get_top_group(item)
            if gid:
                group_ids.add(gid)
        if not group_ids:
            return
        # Draw a combined bounding box for each selected group
        from PySide6.QtCore import QRectF
        for gid in group_ids:
            members = self._diagram_scene.get_group_members(gid)
            if len(members) < 2:
                continue
            union = QRectF()
            for m in members:
                union = union.united(m.sceneBoundingRect())
            if union.isNull():
                continue
            pen = QPen(QColor(0, 120, 215, 120), 1.0 / self.current_scale())
            pen.setDashPattern([6, 4])
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            margin = 4.0 / self.current_scale()
            painter.drawRect(union.adjusted(-margin, -margin, margin, margin))

    # -- Scroll / Zoom --

    def event(self, event) -> bool:
        """Handle gesture events (pinch-to-zoom on trackpad)."""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.Gesture:
            return self._handle_gesture(event)
        return super().event(event)

    def _handle_gesture(self, event) -> bool:
        from PySide6.QtWidgets import QPinchGesture
        pinch = event.gesture(Qt.GestureType.PinchGesture)
        if pinch is not None:
            flags = pinch.changeFlags()
            if flags & QPinchGesture.ChangeFlag.ScaleFactorChanged:
                factor = pinch.scaleFactor()
                center = self.mapToScene(self.mapFromGlobal(pinch.centerPoint().toPoint()))
                self.zoom_at(factor, center)
            return True
        return False

    def wheelEvent(self, event) -> None:
        # Pinch-to-zoom on trackpad (phase-based gesture)
        if event.phase() in (Qt.ScrollPhase.NoScrollPhase,):
            # Discrete mouse wheel: zoom toward cursor
            angle = event.angleDelta().y()
            if angle == 0:
                return
            factor = ZOOM_FACTOR if angle > 0 else 1.0 / ZOOM_FACTOR
            cursor_scene_pos = self.mapToScene(event.position().toPoint())
            self.zoom_at(factor, cursor_scene_pos)
            return

        # Trackpad two-finger scroll: pan (like graphulator)
        dx = event.pixelDelta().x()
        dy = event.pixelDelta().y()
        if dx == 0 and dy == 0:
            # Fall back to angle delta if pixel delta unavailable
            dx = event.angleDelta().x()
            dy = event.angleDelta().y()
        if dx != 0 or dy != 0:
            scale = self.current_scale()
            self.translate(dx / scale, dy / scale)

    # -- Mouse event handling --

    def _item_at_pos(self, viewport_pos) -> object:
        """Return the top scene item at the position, skipping rubberband overlays."""
        scene_pos = self.mapToScene(viewport_pos.toPoint() if hasattr(viewport_pos, 'toPoint') else viewport_pos)
        # Use items() to get all items at the position, then skip overlays
        for item in self.scene().items(scene_pos, Qt.ItemSelectionMode.IntersectsItemShape,
                                        Qt.SortOrder.DescendingOrder, self.transform()):
            # Skip rubberband path and dot overlays
            if item is self._diagram_scene._rubberband_path:
                continue
            if item in self._diagram_scene._rubberband_dots:
                continue
            return item

        # Fallback: check AnnotationItems by bounding rect manually.
        # When math is rendered, QGraphicsTextItem's internal shape may be
        # empty (document cleared), so scene.items() misses them even though
        # our shape() override returns the full rect.
        for item in self.scene().items():
            if isinstance(item, AnnotationItem):
                if item.mapToScene(item.boundingRect()).containsPoint(scene_pos, Qt.FillRule.WindingFill):
                    return item
        return None

    def mousePressEvent(self, event) -> None:
        # Zoom window mode — start rectangle (don't change selection)
        if self._zoom_window_mode and event.button() == Qt.MouseButton.LeftButton:
            self._zoom_rect_start = self.mapToScene(event.position().toPoint())
            # Save current selection so we can restore it after super() runs
            self._zoom_saved_selection = list(self._diagram_scene.selectedItems())
            from PySide6.QtWidgets import QGraphicsRectItem
            from PySide6.QtCore import QRectF
            self._zoom_rect_item = QGraphicsRectItem(QRectF(self._zoom_rect_start, self._zoom_rect_start))
            self._zoom_rect_item.setPen(QPen(QColor(0, 120, 215, 180), 1.0, Qt.PenStyle.DashLine))
            self._zoom_rect_item.setBrush(QBrush(QColor(0, 120, 215, 30)))
            self._zoom_rect_item.setZValue(200)
            self.scene().addItem(self._zoom_rect_item)
            event.accept()
            # Restore selection in case Qt cleared it
            for item in self._zoom_saved_selection:
                item.setSelected(True)
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
                self._extending_wire = None
                self._extending_end = ""
                self._diagram_scene._cancel_connection()
                event.accept()
                return

        if event.button() == Qt.MouseButton.LeftButton:
            item = self._item_at_pos(event.position())
            scene_pos = self.mapToScene(event.position().toPoint())

            # Resolve PortItem to its parent component for drag/selection
            # purposes (ports sit at z=10, above their parent, so they
            # intercept clicks meant for the component/junction).
            if isinstance(item, PortItem) and not self._trace_routing:
                item = item.component

            # If a JunctionItem is hit but a selected connection has a
            # waypoint at the same spot, redirect to the ConnectionItem so
            # that waypoint drag takes priority over junction drag.
            if isinstance(item, JunctionItem) and not self._trace_routing:
                for si in self._diagram_scene.selectedItems():
                    if isinstance(si, ConnectionItem):
                        wi = si._find_waypoint_at(scene_pos)
                        if wi is not None:
                            item = si
                            break

            # -- Trace routing mode: start from empty space or wire endpoint --
            if self._trace_routing and not self._diagram_scene.is_connecting:
                # Not currently connecting — start a new trace
                if isinstance(item, PortItem):
                    # Clicking a port starts normally
                    pass  # fall through to the port handler below
                elif item is None or not isinstance(item, (PortItem, ComponentItem)):
                    # Check if we're near an existing wire's endpoint
                    snapped = self._snap_if_enabled(scene_pos)
                    ext_conn, ext_end, ext_port = self._find_wire_endpoint_for_extend(snapped)
                    if ext_port is not None:
                        # Enter continuation mode: extend the existing wire
                        self._extending_wire = ext_conn
                        self._extending_end = ext_end
                        self._trace_vertices.clear()
                        self._diagram_scene.begin_connection(ext_port)
                        self.setDragMode(QGraphicsView.DragMode.NoDrag)
                        event.accept()
                        return
                    # Clicking empty space: create a free junction and start from it
                    from diagrammer.items.junction_item import JunctionItem as _JI
                    junc = _JI()
                    junc.setPos(snapped)
                    junc.setVisible(False)
                    self._diagram_scene.addItem(junc)
                    if hasattr(self._diagram_scene, 'assign_active_layer'):
                        self._diagram_scene.assign_active_layer(junc)
                    self._trace_vertices.clear()
                    self._diagram_scene.begin_connection(junc.port)
                    self.setDragMode(QGraphicsView.DragMode.NoDrag)
                    event.accept()
                    return

            # -- Trace routing mode: place waypoints or finish --
            if self._trace_routing and self._diagram_scene.is_connecting:

                # If a target port is highlighted (green pulse), clicking
                # ANYWHERE finishes the connection to that port — the user
                # doesn't need to click precisely on the small port circle.
                target_port = self._diagram_scene._current_target_port
                if target_port is not None or isinstance(item, PortItem):
                    self._finish_trace(scene_pos)
                    event.accept()
                    return
                elif isinstance(item, ConnectionItem):
                    # Click on an existing wire → finish with junction
                    self._finish_trace(scene_pos)
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

            # -- Shift+click → toggle multi-select (group-aware) --
            # Exception: if Shift+clicking an already-selected ConnectionItem,
            # let the event pass through so the connection's own handler can
            # do waypoint-level Shift+Click selection.
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                from diagrammer.items.shape_item import LineItem, ShapeItem
                if isinstance(item, ConnectionItem) and item.isSelected():
                    pass  # fall through to let ConnectionItem handle waypoint selection
                elif isinstance(item, (ComponentItem, ConnectionItem, ShapeItem, LineItem, JunctionItem, AnnotationItem)):
                    if item.isSelected():
                        self._deselect_with_group(item)
                    else:
                        self._select_with_group(item)
                    event.accept()
                    return

            # -- Click on a selected waypoint with components also selected → unified drag --
            elif (not event.modifiers()
                  and self._selected_waypoint_set):
                clicked_wp_conn, clicked_wp_idx = self._find_selected_waypoint_at(scene_pos)
                if clicked_wp_conn is not None:
                    has_components = any(
                        isinstance(i, (ComponentItem, JunctionItem, AnnotationItem))
                        for i in self._diagram_scene.selectedItems()
                    )
                    has_multi_conn_wps = len(self._selected_waypoint_set) > 1
                    if has_components or has_multi_conn_wps:
                        self._setup_unified_drag(scene_pos)
                        event.accept()
                        return

            # -- Option/Alt+click → duplicate and drag (selected items or single item) --
            from diagrammer.items.shape_item import (
                LineItem as _AltLineItem,
                ShapeItem as _AltShapeItem,
            )
            if event.modifiers() & Qt.KeyboardModifier.AltModifier and (
                isinstance(
                    item,
                    (ComponentItem, AnnotationItem, ConnectionItem,
                     _AltShapeItem, _AltLineItem),
                )
            ):
                # For grouped wires, redirect to a component in the group
                if isinstance(item, ConnectionItem) and getattr(item, '_group_id', None):
                    from diagrammer.commands.group_command import get_top_group
                    gid = get_top_group(item)
                    if gid:
                        for m in self._diagram_scene.get_group_members(gid):
                            if isinstance(m, ComponentItem):
                                item = m
                                break
                # For free wires, pass the ConnectionItem directly —
                # _duplicate_selection will auto-include endpoint junctions
                if isinstance(item, ConnectionItem) and not getattr(item, '_group_id', None):
                    pass  # handled inside _duplicate_selection
                result = self._duplicate_selection(item, scene_pos)
                clones, cloned_conns = result if result else ([], [])
                if clones:
                    anchor = clones[0]
                    self._diagram_scene.clearSelection()
                    for c in clones:
                        c.setSelected(True)
                    self._dragging_components = clones
                    self._drag_anchor_item = anchor
                    self._drag_anchor_start_pos = QPointF(anchor.pos())
                    for c in clones:
                        self._diagram_scene.record_move_start(c.instance_id, c.pos())
                    # Track cloned connections so their waypoints shift during drag
                    self._drag_internal_conns = cloned_conns
                    self._drag_conn_waypoints = {
                        conn.instance_id: [QPointF(w) for w in conn.vertices]
                        for conn in cloned_conns
                    }
                    self._drag_mouse_start = QPointF(scene_pos)
                    self.setDragMode(QGraphicsView.DragMode.NoDrag)
                event.accept()
                return

            # -- Click on an annotation → set up drag --
            elif isinstance(item, AnnotationItem):
                selected = self._expand_group_selection(item)
                self._dragging_components = selected
                self._drag_anchor_item = item
                self._drag_anchor_start_pos = QPointF(item.pos())
                self._drag_mouse_start = QPointF(scene_pos)
                for comp in selected:
                    self._diagram_scene.record_move_start(comp.instance_id, comp.pos())
                self._drag_internal_conns = []
                self._drag_conn_waypoints = {}
                self.setDragMode(QGraphicsView.DragMode.NoDrag)

            # -- Click on a grouped connection → redirect to group drag --
            elif isinstance(item, ConnectionItem) and getattr(item, '_group_id', None):
                from diagrammer.commands.group_command import get_top_group as _conn_gtg
                gid = _conn_gtg(item)
                if gid:
                    members = self._diagram_scene.get_group_members(gid)
                    # Find a movable member to use as drag anchor
                    anchor = None
                    for m in members:
                        if isinstance(m, ComponentItem):
                            anchor = m
                            break
                    if anchor is None:
                        for m in members:
                            if isinstance(m, JunctionItem):
                                anchor = m
                                break
                    if anchor is not None:
                        if isinstance(anchor, ComponentItem):
                            anchor.set_snap_anchor_closest_to(scene_pos)
                        selected = self._expand_group_selection(anchor)
                        for s in selected:
                            if s is not anchor and hasattr(s, '_skip_snap'):
                                s._skip_snap = True
                        # ConnectionItem imported at module level
                        selected_ids = set(id(c) for c in selected)
                        self._drag_auto_junctions = []
                        # Gather junctions
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
                        self._drag_anchor_item = anchor
                        self._drag_anchor_start_pos = QPointF(anchor.pos())
                        # Qt didn't receive press on anchor, so we must track
                        # mouse delta ourselves (same mechanism as alt-drag)
                        anchor._alt_drag_clone = True
                        self._drag_mouse_start = QPointF(scene_pos)
                        for comp in selected:
                            self._diagram_scene.record_move_start(comp.instance_id, comp.pos())
                        # Capture group connections
                        comp_ids = set(id(c) for c in selected)
                        self._drag_internal_conns = []
                        self._drag_conn_waypoints = {}
                        seen_conn_ids = set()
                        for si in self._diagram_scene.get_group_members(gid):
                            if isinstance(si, ConnectionItem) and id(si) not in seen_conn_ids:
                                self._drag_internal_conns.append(si)
                                self._drag_conn_waypoints[si.instance_id] = [
                                    QPointF(w) for w in si.vertices
                                ]
                                seen_conn_ids.add(id(si))
                                for port_item in (si.source_port, si.target_port):
                                    pc = port_item.component
                                    if isinstance(pc, JunctionItem) and id(pc) not in comp_ids:
                                        selected.append(pc)
                                        comp_ids.add(id(pc))
                                        self._drag_auto_junctions.append(pc)
                                        self._diagram_scene.record_move_start(pc.instance_id, pc.pos())
                        for si in self._diagram_scene.items():
                            if isinstance(si, ConnectionItem) and id(si) not in seen_conn_ids:
                                src_in = id(si.source_port.component) in comp_ids
                                tgt_in = id(si.target_port.component) in comp_ids
                                if src_in and tgt_in:
                                    self._drag_internal_conns.append(si)
                                    self._drag_conn_waypoints[si.instance_id] = [
                                        QPointF(w) for w in si.vertices
                                    ]
                                    seen_conn_ids.add(id(si))
                        self.setDragMode(QGraphicsView.DragMode.NoDrag)
                        event.accept()
                        return

            # -- Click on a component or junction → set up snap anchor and record move start --
            elif isinstance(item, (ComponentItem, JunctionItem)):
                if isinstance(item, ComponentItem):
                    item.set_snap_anchor_closest_to(scene_pos)

                # Expand selection to include group members
                from diagrammer.commands.group_command import get_top_group as _gtg_drag
                selected = self._expand_group_selection(item)

                # Detect explicit multi-selection (user rubber-banded or
                # shift-clicked, as opposed to clicking a single unselected
                # item).  When multi-selecting, the user's selection is
                # authoritative — don't auto-include extra junctions.
                _is_explicit_multiselect = (
                    not _gtg_drag(item)
                    and len(selected) > 1
                    and item in self._diagram_scene.selectedItems()
                )

                # Only the clicked item should snap — disable snap on group siblings
                for s in selected:
                    if s is not item and hasattr(s, '_skip_snap'):
                        s._skip_snap = True

                # Auto-include connected junctions not in the selection,
                # but only for single-item or group drags — not when the
                # user has built an explicit multi-selection.
                selected_ids = set(id(c) for c in selected)
                self._drag_auto_junctions = []
                if not _is_explicit_multiselect:
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
                self._drag_anchor_item = item
                self._drag_anchor_start_pos = QPointF(item.pos())
                self._drag_mouse_start = QPointF(scene_pos)
                for comp in selected:
                    self._diagram_scene.record_move_start(comp.instance_id, comp.pos())

                # Capture internal connections: group-member connections first,
                # then endpoint-detected connections for non-grouped selections
                comp_ids = set(id(c) for c in selected)
                self._drag_internal_conns = []
                self._drag_conn_waypoints = {}
                seen_conn_ids = set()

                # 1) Connections that are group members
                from diagrammer.commands.group_command import get_top_group
                gid = get_top_group(item)
                if gid:
                    for si in self._diagram_scene.get_group_members(gid):
                        if isinstance(si, ConnectionItem) and id(si) not in seen_conn_ids:
                            self._drag_internal_conns.append(si)
                            self._drag_conn_waypoints[si.instance_id] = [
                                QPointF(w) for w in si.vertices
                            ]
                            seen_conn_ids.add(id(si))
                            # Also ensure the connection's endpoint junctions are in the drag set
                            for port_item in (si.source_port, si.target_port):
                                pc = port_item.component
                                if isinstance(pc, JunctionItem) and id(pc) not in comp_ids:
                                    selected.append(pc)
                                    comp_ids.add(id(pc))
                                    self._drag_auto_junctions.append(pc)
                                    self._diagram_scene.record_move_start(pc.instance_id, pc.pos())

                # 2) Endpoint-detected connections (for non-grouped multi-select)
                for si in self._diagram_scene.items():
                    if isinstance(si, ConnectionItem) and id(si) not in seen_conn_ids:
                        src_in = id(si.source_port.component) in comp_ids
                        tgt_in = id(si.target_port.component) in comp_ids
                        if src_in and tgt_in:
                            self._drag_internal_conns.append(si)
                            self._drag_conn_waypoints[si.instance_id] = [
                                QPointF(w) for w in si.vertices
                            ]
                            seen_conn_ids.add(id(si))

                self.setDragMode(QGraphicsView.DragMode.NoDrag)

                # If waypoints are also selected, activate unified drag
                if self._selected_waypoint_set:
                    self._unified_drag_active = True
                    self._unified_drag_waypoint_starts = {}
                    for conn_id, conn in self._selected_waypoint_conns.items():
                        self._unified_drag_waypoint_starts[conn_id] = [
                            QPointF(w) for w in conn.vertices
                        ]

        # Save selection before Qt's default handler clears it (Qt deselects
        # everything on a plain click, but we need multi-select to survive).
        _saved_selection = (
            list(self._diagram_scene.selectedItems())
            if len(self._dragging_components) > 1
            else None
        )

        super().mousePressEvent(event)

        # Restore the multi-selection that Qt's click handler cleared
        if _saved_selection is not None:
            for sel_item in _saved_selection:
                if not sel_item.isSelected():
                    sel_item.setSelected(True)

        # After Qt's default selection handling, expand selection to full groups.
        from diagrammer.commands.group_command import get_top_group as _gtg
        for item in list(self._diagram_scene.selectedItems()):
            gid = _gtg(item)
            if gid:
                for m in self._diagram_scene.get_group_members(gid):
                    if not m.isSelected():
                        m.setSelected(True)

        # Sync view-level waypoint selection from ConnectionItems
        # (picks up shift+click waypoint toggles handled by ConnectionItem)
        self._sync_waypoint_selection()

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

        # Check if a stretch drag is in progress on any dragged component
        _stretch_active = False
        if self._dragging_components:
            for comp in self._dragging_components:
                if (getattr(comp, '_dragging_stretch_h', False) or
                        getattr(comp, '_dragging_stretch_v', False)):
                    _stretch_active = True
                    break

        # If we're handling a position drag ourselves, skip Qt's move.
        # But always call super for stretch drags (handled by ComponentItem)
        # and when not dragging at all (rubber-band, etc.).
        if _stretch_active or not self._dragging_components:
            super().mouseMoveEvent(event)

        # Skip position drag code when a stretch drag is active
        if _stretch_active:
            # Just update connections for the stretch
            self._diagram_scene.update_connections()
            return

        # Update connections if components are being dragged
        if self._dragging_components and self._unified_drag_active:
            # Unified drag: move components + selected waypoints together
            mouse_scene = self.mapToScene(event.position().toPoint())
            if self._drag_mouse_start is None:
                self._drag_mouse_start = mouse_scene
            snapped_pos = self._snap_if_enabled(mouse_scene)
            snapped_start = self._snap_if_enabled(self._drag_mouse_start)
            delta = snapped_pos - snapped_start

            # Move components
            for item in self._dragging_components:
                start = self._diagram_scene._drag_start_positions.get(item.instance_id)
                if start is not None:
                    item._skip_snap = True
                    item.setPos(start + delta)
                    item._skip_snap = False

            # Move unified-selected waypoints
            for conn_id, orig_wps in self._unified_drag_waypoint_starts.items():
                conn = self._selected_waypoint_conns.get(conn_id)
                if conn is None:
                    continue
                sel_indices = self._selected_waypoint_set.get(conn_id, set())
                for idx in sel_indices:
                    if 0 <= idx < len(conn._waypoints) and idx < len(orig_wps):
                        conn._waypoints[idx] = QPointF(
                            orig_wps[idx].x() + delta.x(),
                            orig_wps[idx].y() + delta.y(),
                        )
                conn.update_route()

            # Shift internal connection waypoints
            for conn in self._drag_internal_conns:
                orig_wps = self._drag_conn_waypoints.get(conn.instance_id, [])
                if orig_wps:
                    conn._waypoints = [
                        QPointF(w.x() + delta.x(), w.y() + delta.y())
                        for w in orig_wps
                    ]
                conn.update_route()

            # Update external connections
            internal_ids = set(id(c) for c in self._drag_internal_conns)
            unified_ids = set(
                id(self._selected_waypoint_conns[cid])
                for cid in self._selected_waypoint_conns
            )
            for item in self._diagram_scene.items():
                if isinstance(item, ConnectionItem):
                    if id(item) not in internal_ids and id(item) not in unified_ids:
                        item.update_route()
            from diagrammer.items.component_item import ComponentItem as _UCItem
            for item in self._diagram_scene.items():
                if isinstance(item, _UCItem):
                    item.refresh_lead_shortening()

            scene_pos = self.mapToScene(event.position().toPoint())
            self._diagram_scene.cursor_scene_pos_changed.emit(scene_pos.x(), scene_pos.y())
            return

        if self._dragging_components:
            if self._drag_anchor_start_pos and self._drag_anchor_item:
                # Always compute delta from mouse position — don't rely on
                # Qt moving the anchor, which fails after save/reload and
                # when clicking grouped connections.
                mouse_scene = self.mapToScene(event.position().toPoint())
                if self._drag_mouse_start is None:
                    self._drag_mouse_start = mouse_scene
                raw_delta = mouse_scene - self._drag_mouse_start

                # Snap the delta to grid via the anchor's snap logic
                anchor = self._drag_anchor_item
                anchor_start = self._drag_anchor_start_pos
                tentative_pos = anchor_start + raw_delta

                # Snap: use the anchor's port-based snap offset if available,
                # so the PORT lands on grid (not the item's origin)
                from diagrammer.canvas.grid import snap_to_grid
                views = self._diagram_scene.views()
                if views and getattr(views[0], '_snap_enabled', True):
                    spacing = views[0].grid_spacing
                    snap_offset = getattr(anchor, '_snap_anchor_offset', None)
                    if snap_offset is not None:
                        # Snap the port position, then derive item position
                        port_pos = tentative_pos + snap_offset
                        snapped_port = snap_to_grid(port_pos, spacing)
                        snapped_pos = tentative_pos + (snapped_port - port_pos)
                    else:
                        snapped_pos = snap_to_grid(tentative_pos, spacing)
                else:
                    snapped_pos = tentative_pos
                delta = snapped_pos - anchor_start

                # Reposition ALL items (including anchor) using the snapped delta
                for item in self._dragging_components:
                    start = self._diagram_scene._drag_start_positions.get(item.instance_id)
                    if start is not None:
                        item._skip_snap = True
                        item.setPos(start + delta)
                        item._skip_snap = False
                # Shift internal connection waypoints and rebuild their routes
                for conn in self._drag_internal_conns:
                    orig_wps = self._drag_conn_waypoints.get(conn.instance_id, [])
                    if orig_wps:
                        conn._waypoints = [
                            QPointF(w.x() + delta.x(), w.y() + delta.y())
                            for w in orig_wps
                        ]
                    conn.update_route()

                # Update non-internal connections (external wires to the group)
                internal_ids = set(id(c) for c in self._drag_internal_conns)
                for item in self._diagram_scene.items():
                    if isinstance(item, ConnectionItem) and id(item) not in internal_ids:
                        item.update_route()
                # Refresh lead shortening
                from diagrammer.items.component_item import ComponentItem
                for item in self._diagram_scene.items():
                    if isinstance(item, ComponentItem):
                        item.refresh_lead_shortening()

        scene_pos = self.mapToScene(event.position().toPoint())
        self._diagram_scene.cursor_scene_pos_changed.emit(scene_pos.x(), scene_pos.y())

    def mouseReleaseEvent(self, event) -> None:
        # Zoom window — complete
        if self._zoom_window_mode and event.button() == Qt.MouseButton.LeftButton:
            if self._zoom_rect_item and self._zoom_rect_start:
                rect = self._zoom_rect_item.rect()
                self.scene().removeItem(self._zoom_rect_item)
                self._zoom_rect_item = None
                click_pos = self._zoom_rect_start
                self._zoom_rect_start = None
                if rect.width() > 5 and rect.height() > 5:
                    # Drag box: zoom to fit the rectangle
                    self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
                else:
                    # Click: zoom in at cursor (Shift+click = zoom out)
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        self.zoom_at(0.5, click_pos)
                    else:
                        self.zoom_at(2.0, click_pos)
                # Stay in zoom mode (don't exit — user can click repeatedly)
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
                from diagrammer.commands.connect_command import EditWaypointsCommand
                self._diagram_scene.undo_stack.beginMacro("Move group")
                # Record each item's move WITHOUT triggering per-item updates
                for comp in self._dragging_components:
                    if hasattr(comp, '_alt_drag_clone'):
                        del comp._alt_drag_clone
                    if hasattr(comp, '_skip_snap'):
                        comp._skip_snap = False
                    self._diagram_scene.record_move_end(comp, update=False)
                    if hasattr(comp, 'clear_snap_anchor'):
                        comp.clear_snap_anchor()
                # Record waypoint changes for unified-selected connections
                if self._unified_drag_active:
                    for conn_id, orig_wps in self._unified_drag_waypoint_starts.items():
                        conn = self._selected_waypoint_conns.get(conn_id)
                        if conn is None:
                            continue
                        new_wps = [QPointF(w) for w in conn.vertices]
                        if orig_wps != new_wps:
                            cmd = EditWaypointsCommand(conn, orig_wps, new_wps)
                            self._diagram_scene.undo_stack.push(cmd)
                # Record waypoint changes for internal connections
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
                self._drag_mouse_start = None
                self._unified_drag_active = False
                self._unified_drag_waypoint_starts = {}
                # Now update with drag state cleared
                self._diagram_scene.update_connections()
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        super().mouseReleaseEvent(event)

        # After rubber-band selection, expand to full groups — but only
        # include group members whose scene bounding rect intersects the
        # rubber-band area so we don't pull in items far outside the box.
        from diagrammer.commands.group_command import get_top_group as _gtg2
        rb = self._rubber_band_rect
        for item in list(self._diagram_scene.selectedItems()):
            gid = _gtg2(item)
            if gid:
                for m in self._diagram_scene.get_group_members(gid):
                    if not m.isSelected():
                        if rb.isNull() or rb.intersects(m.sceneBoundingRect()):
                            m.setSelected(True)

        # After rubber-band selection, deselect any junction whose center
        # is outside the rubber-band rect (prevents connected junctions from
        # being pulled in when the wire intersects the box but the junction
        # itself doesn't).
        if not rb.isNull():
            from diagrammer.items.junction_item import JunctionItem
            for item in list(self._diagram_scene.selectedItems()):
                if isinstance(item, JunctionItem):
                    if not rb.contains(item.pos()):
                        item.setSelected(False)

        # After rubber-band selection, select waypoints within the rect
        # on ALL selected connections (and also on connections whose waypoints
        # fall inside the rect even if the wire itself wasn't selected).
        if not self._rubber_band_rect.isNull():
            rect = self._rubber_band_rect
            self._selected_waypoint_set.clear()
            self._selected_waypoint_conns.clear()
            # Check all connections in the scene for waypoints inside the rect
            for item in self._diagram_scene.items():
                if not isinstance(item, ConnectionItem):
                    continue
                item._selected_waypoints.clear()
                for idx, wp in enumerate(item.vertices):
                    if rect.contains(wp):
                        item._selected_waypoints.add(idx)
                if item._selected_waypoints:
                    # Ensure the connection is selected so it draws handles
                    if not item.isSelected():
                        item.setSelected(True)
                    self._selected_waypoint_set[item.instance_id] = set(item._selected_waypoints)
                    self._selected_waypoint_conns[item.instance_id] = item
                item.update()
            self._rubber_band_rect = QRectF()  # reset

        # Safety: always restore rubber-band drag mode after any mouse release,
        # in case an operation set NoDrag and didn't restore it.
        if (not self._panning
                and not self._zoom_window_mode
                and not self._diagram_scene.is_connecting
                and self.dragMode() != QGraphicsView.DragMode.RubberBandDrag):
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

    def _finish_trace(self, cursor_pos: QPointF) -> None:
        """Finish a trace route — either extending an existing wire or creating a new one."""
        if self._extending_wire is not None:
            # Extension mode: append new waypoints to the existing wire
            self._diagram_scene.extend_wire(
                self._extending_wire,
                self._extending_end,
                self._trace_vertices,
                new_target_port=self._diagram_scene._current_target_port,
            )
            self._extending_wire = None
            self._extending_end = ""
        else:
            # Normal mode: create a new connection
            self._diagram_scene.finish_connection_with_vertices(
                self._trace_vertices, cursor_pos=cursor_pos,
            )
        self._trace_vertices.clear()

    def _enter_select_mode(self) -> None:
        """Return to Select mode from any other mode."""
        from diagrammer.canvas.scene import InteractionMode
        if self._zoom_window_mode:
            self.zoom_window_mode = False
        if self._trace_routing:
            self._trace_routing = False
            self._trace_vertices.clear()
            self._diagram_scene.mode = InteractionMode.SELECT
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self._diagram_scene.mode_changed.emit(InteractionMode.SELECT)
            # Uncheck trace mode action in main window
            main_win = self.window()
            if hasattr(main_win, '_trace_mode_act'):
                main_win._trace_mode_act.blockSignals(True)
                main_win._trace_mode_act.setChecked(False)
                main_win._trace_mode_act.blockSignals(False)
        self._extending_wire = None
        self._extending_end = ""
        if self._diagram_scene.is_connecting:
            self._trace_vertices.clear()
            self._diagram_scene._cancel_connection()
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        # Exit any component isolation modes
        for item in self._diagram_scene.items():
            if isinstance(item, ComponentItem) and getattr(item, '_isolation_mode', False):
                item.isolation_mode = False
        self._diagram_scene.clear_rotation_pivot()
        self._diagram_scene.clear_alignment_ports()

    def keyPressEvent(self, event) -> None:
        # Escape and the user-configured "select mode" key both return
        # to Select mode. Compared against the live registry so user
        # overrides take effect.
        from diagrammer.shortcuts import get_shortcut as _gs
        from PySide6.QtGui import QKeySequence as _KS
        ev_seq = _KS(int(event.key()) | int(event.modifiers().value))
        ev_portable = ev_seq.toString(_KS.SequenceFormat.PortableText)

        escape_seq = _gs("canvas.escape").key_sequence.toString(
            _KS.SequenceFormat.PortableText)
        select_seq = _gs("canvas.select_mode").key_sequence.toString(
            _KS.SequenceFormat.PortableText)

        if ev_portable and ev_portable == escape_seq:
            self._enter_select_mode()
            event.accept()
            return
        if ev_portable and ev_portable == select_seq:
            # Don't steal the key while editing an annotation
            editing = any(isinstance(i, AnnotationItem) and i.is_editing
                          for i in self._diagram_scene.selectedItems())
            if not editing:
                self._enter_select_mode()
                event.accept()
                return

        # Arrow keys: nudge selected items (20% of grid), or pan if nothing selected
        # Skip nudge when an annotation is being edited (arrows navigate text)
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            editing_annot = any(
                isinstance(i, AnnotationItem) and i.is_editing
                for i in self._diagram_scene.selectedItems()
            )
            if not editing_annot:
                selected = [
                    i for i in self._diagram_scene.selectedItems()
                    if hasattr(i, 'setPos')
                ]
                if selected:
                    fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    self._nudge_selected(selected, event.key(), fine=fine)
                    event.accept()
                    return

        # Update zoom cursor when Shift is pressed
        if self._zoom_window_mode and event.key() == Qt.Key.Key_Shift:
            self.setCursor(self._make_zoom_cursor(zoom_in=False))

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        # Update zoom cursor when Shift is released
        if self._zoom_window_mode and event.key() == Qt.Key.Key_Shift:
            self.setCursor(self._make_zoom_cursor(zoom_in=True))
        super().keyReleaseEvent(event)

    def _select_with_group(self, item) -> None:
        """Select an item and all its group members. If the item is in a group,
        select all members; otherwise just select the item."""
        from diagrammer.commands.group_command import get_top_group
        gid = get_top_group(item)
        if gid:
            for m in self._diagram_scene.get_group_members(gid):
                m.setSelected(True)
        else:
            item.setSelected(True)

    def _deselect_with_group(self, item) -> None:
        """Deselect an item and all its group members."""
        from diagrammer.commands.group_command import get_top_group
        gid = get_top_group(item)
        if gid:
            for m in self._diagram_scene.get_group_members(gid):
                m.setSelected(False)
        else:
            item.setSelected(False)

    def _setup_unified_drag(self, scene_pos: QPointF) -> None:
        """Set up a drag that moves both components and selected waypoints."""
        # Gather selected components/junctions/annotations
        selected_components = [
            i for i in self._diagram_scene.selectedItems()
            if isinstance(i, (ComponentItem, JunctionItem, AnnotationItem))
        ]
        for comp in selected_components:
            if hasattr(comp, '_skip_snap'):
                comp._skip_snap = True
            self._diagram_scene.record_move_start(comp.instance_id, comp.pos())

        # Snapshot waypoint start positions
        self._unified_drag_waypoint_starts = {}
        for conn_id, conn in self._selected_waypoint_conns.items():
            self._unified_drag_waypoint_starts[conn_id] = [QPointF(w) for w in conn.vertices]

        self._dragging_components = selected_components
        self._drag_anchor_item = None
        self._drag_anchor_start_pos = None
        self._drag_mouse_start = QPointF(scene_pos)
        self._unified_drag_active = True

        # Capture internal connections (both endpoints in component set,
        # excluding connections being waypoint-dragged)
        comp_ids = set(id(c) for c in selected_components)
        unified_conn_ids = set(self._selected_waypoint_set.keys())
        self._drag_internal_conns = []
        self._drag_conn_waypoints = {}
        for si in self._diagram_scene.items():
            if isinstance(si, ConnectionItem) and si.instance_id not in unified_conn_ids:
                src_in = id(si.source_port.component) in comp_ids
                tgt_in = id(si.target_port.component) in comp_ids
                if src_in and tgt_in:
                    self._drag_internal_conns.append(si)
                    self._drag_conn_waypoints[si.instance_id] = [
                        QPointF(w) for w in si.vertices
                    ]
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def _expand_group_selection(self, clicked_item) -> list:
        """Expand selection to include all group members of the clicked item.

        If the clicked item belongs to a group, all group members are selected
        and returned. Otherwise, returns the current multi-selection or just
        the clicked item.
        """
        from diagrammer.commands.group_command import get_top_group

        gid = get_top_group(clicked_item)
        if gid:
            # Select all group members
            members = self._diagram_scene.get_group_members(gid)
            for m in members:
                m.setSelected(True)
            # Return movable members (not connections — they move via waypoints)
            return [m for m in members if not isinstance(m, ConnectionItem)]

        # No group — use current multi-selection or just the clicked item
        selected = [
            i for i in self._diagram_scene.selectedItems()
            if isinstance(i, (ComponentItem, JunctionItem, AnnotationItem))
        ]
        if clicked_item not in selected:
            selected = [clicked_item]
        return selected

    def _duplicate_selection(self, clicked_item, scene_pos) -> list:
        """Duplicate all selected items (or just the clicked item) for Alt+drag.

        Clones components, annotations, junctions, shapes (rectangle/
        ellipse/line/arrow), and recreates internal connections between
        the cloned items. All clones are flagged for manual drag
        positioning.
        """
        from diagrammer.commands.connect_command import CreateConnectionCommand

        # Determine what to clone: expand from clicked item's group first,
        # then merge with any other selected items
        from diagrammer.commands.group_command import get_top_group
        from diagrammer.items.shape_item import LineItem, ShapeItem

        # Start with the clicked item's group (or just the clicked item)
        gid = get_top_group(clicked_item)
        if gid:
            selected = [m for m in self._diagram_scene.get_group_members(gid)
                        if not isinstance(m, ConnectionItem)]
        else:
            selected = [
                i for i in self._diagram_scene.selectedItems()
                if isinstance(
                    i,
                    (ComponentItem, JunctionItem, AnnotationItem,
                     ShapeItem, LineItem),
                )
            ]
            # If clicked item is a free ConnectionItem, include its endpoint junctions
            if isinstance(clicked_item, ConnectionItem):
                for port in (clicked_item.source_port, clicked_item.target_port):
                    comp = port.component
                    if isinstance(comp, JunctionItem) and comp not in selected:
                        selected.append(comp)
            if clicked_item not in selected and not isinstance(clicked_item, ConnectionItem):
                selected = [clicked_item]

        # Expand to include any other selected groups
        gids = set()
        for item in selected:
            g = get_top_group(item)
            if g:
                gids.add(g)
        for gid in gids:
            for m in self._diagram_scene.get_group_members(gid):
                if m not in selected and not isinstance(m, ConnectionItem):
                    selected.append(m)

        # Auto-include connected junctions (invisible T-junction markers)
        main_win = self.window()
        if hasattr(main_win, '_gather_connected_junctions'):
            extra_juncs = main_win._gather_connected_junctions(selected)
            for j in extra_juncs:
                if j not in selected:
                    selected.append(j)

        if not selected:
            return [], []

        # Suppress route rebuilds during the macro — clones overlap originals
        # and update_connections would create visual artifacts
        self._diagram_scene._suppress_undo_updates = True
        self._diagram_scene.undo_stack.beginMacro("Duplicate")

        # Clone each item, mapping old instance_id → new item
        old_to_new = {}  # id(old_item) → new_item
        clones = []
        for item in selected:
            clone = self._duplicate_item(item)
            if clone:
                old_to_new[id(item)] = clone
                clones.append(clone)

        # Preserve group membership: remap entire group stack to new ids
        import uuid as _uuid
        from diagrammer.commands.group_command import set_group_ids
        old_gid_to_new = {}
        for item in selected:
            old_stack = getattr(item, '_group_ids', []) or []
            if old_stack and id(item) in old_to_new:
                new_stack = []
                for gid in old_stack:
                    if gid not in old_gid_to_new:
                        old_gid_to_new[gid] = _uuid.uuid4().hex[:12]
                    new_stack.append(old_gid_to_new[gid])
                set_group_ids(old_to_new[id(item)], new_stack)

        # Recreate internal connections between cloned items
        cloned_conns = []
        selected_ids = set(id(i) for i in selected)
        # Snapshot scene items BEFORE adding cloned connections
        scene_items_snapshot = list(self._diagram_scene.items())
        for si in scene_items_snapshot:
            if not isinstance(si, ConnectionItem):
                continue
            src_comp = si.source_port.component
            tgt_comp = si.target_port.component
            if id(src_comp) in selected_ids and id(tgt_comp) in selected_ids:
                new_src = old_to_new.get(id(src_comp))
                new_tgt = old_to_new.get(id(tgt_comp))
                if new_src is None or new_tgt is None:
                    continue
                # Find matching ports
                src_port = None
                tgt_port = None
                if hasattr(new_src, 'port_by_name'):
                    src_port = new_src.port_by_name(si.source_port.port_name)
                elif hasattr(new_src, 'port'):
                    src_port = new_src.port
                if hasattr(new_tgt, 'port_by_name'):
                    tgt_port = new_tgt.port_by_name(si.target_port.port_name)
                elif hasattr(new_tgt, 'port'):
                    tgt_port = new_tgt.port
                if src_port and tgt_port:
                    cmd = CreateConnectionCommand(self._diagram_scene, src_port, tgt_port)
                    self._diagram_scene.undo_stack.push(cmd)
                    conn = cmd.connection
                    if conn:
                        conn.line_width = si.line_width
                        conn.line_color = si.line_color
                        conn.corner_radius = si.corner_radius
                        conn.routing_mode = si.routing_mode
                        if si.vertices:
                            conn.vertices = [QPointF(w) for w in si.vertices]
                        # Preserve group membership on connection
                        old_stack = getattr(si, '_group_ids', []) or []
                        if old_stack:
                            new_stack = []
                            for gid in old_stack:
                                if gid not in old_gid_to_new:
                                    old_gid_to_new[gid] = _uuid.uuid4().hex[:12]
                                new_stack.append(old_gid_to_new[gid])
                            set_group_ids(conn, new_stack)
                        cloned_conns.append(conn)

        self._diagram_scene.undo_stack.endMacro()
        self._diagram_scene._suppress_undo_updates = False

        return clones, cloned_conns

    def _duplicate_item(self, item):
        """Create an exact clone of an item at the same position (for Alt+drag).

        The clone is flagged with ``_alt_drag_clone = True`` so that
        mouseMoveEvent manually positions it (Qt's built-in drag won't
        track an item that didn't receive the original press event).
        """
        clone = None
        if isinstance(item, ComponentItem):
            from diagrammer.commands.add_command import AddComponentCommand
            cmd = AddComponentCommand(self._diagram_scene, item.component_def, item.pos())
            self._diagram_scene.undo_stack.push(cmd)
            clone = cmd.item
            if clone:
                clone._skip_snap = True
                if item.rotation_angle:
                    clone.rotate_by(item.rotation_angle)
                if item.flip_h:
                    clone.set_flip_h(True)
                if item.flip_v:
                    clone.set_flip_v(True)
                if item.stretch_dx or item.stretch_dy:
                    clone.set_stretch(item.stretch_dx, item.stretch_dy)
                clone.setPos(item.pos())
                clone.setZValue(item.zValue() + 0.01)
                clone._skip_snap = False
        elif isinstance(item, JunctionItem):
            clone = JunctionItem()
            clone._skip_snap = True
            clone.setPos(item.pos())
            clone._skip_snap = False
            clone.setVisible(item.isVisible())
            clone.setZValue(item.zValue() + 0.01)
            self._diagram_scene.addItem(clone)
        elif isinstance(item, AnnotationItem):
            clone = AnnotationItem(item.source_text)
            clone.font_family = item.font_family
            clone.font_size = item.font_size
            clone.font_bold = item.font_bold
            clone.font_italic = item.font_italic
            clone.text_color = item.text_color
            # Preserve rotation — setTransformOriginPoint must be called
            # before setRotation so the clone rotates around its center
            # like the original, otherwise the copy pivots around (0,0).
            if item.rotation():
                clone.setTransformOriginPoint(clone.boundingRect().center())
                clone.setRotation(item.rotation())
            clone.setPos(item.pos())
            clone.setZValue(item.zValue() + 0.01)
            self._diagram_scene.addItem(clone)
        else:
            from diagrammer.items.shape_item import (
                EllipseItem,
                LineItem,
                RectangleItem,
            )
            if isinstance(item, RectangleItem):
                clone = RectangleItem(
                    width=item.shape_width, height=item.shape_height
                )
                clone.stroke_color = QColor(item.stroke_color)
                clone.fill_color = QColor(item.fill_color)
                clone.stroke_width = item.stroke_width
                clone.dash_style = item.dash_style
                clone.corner_radius = item.corner_radius
                clone._skip_snap = True
                clone.setPos(item.pos())
                clone._skip_snap = False
                clone.setZValue(item.zValue() + 0.01)
                self._diagram_scene.addItem(clone)
            elif isinstance(item, EllipseItem):
                clone = EllipseItem(
                    width=item.shape_width, height=item.shape_height
                )
                clone.stroke_color = QColor(item.stroke_color)
                clone.fill_color = QColor(item.fill_color)
                clone.stroke_width = item.stroke_width
                clone.dash_style = item.dash_style
                clone._skip_snap = True
                clone.setPos(item.pos())
                clone._skip_snap = False
                clone.setZValue(item.zValue() + 0.01)
                self._diagram_scene.addItem(clone)
            elif isinstance(item, LineItem):
                clone = LineItem(
                    start=QPointF(item.line_start),
                    end=QPointF(item.line_end),
                )
                clone.stroke_color = QColor(item.stroke_color)
                clone.stroke_width = item.stroke_width
                clone.dash_style = item.dash_style
                clone.cap_style = item.cap_style
                clone.arrow_style = item.arrow_style
                clone.arrow_type = item.arrow_type
                clone.arrow_scale = item.arrow_scale
                clone.arrow_extend = item.arrow_extend
                clone._skip_snap = True
                clone.setPos(item.pos())
                clone._skip_snap = False
                clone.setZValue(item.zValue() + 0.01)
                self._diagram_scene.addItem(clone)

        if clone is not None:
            clone._alt_drag_clone = True
        return clone

    def _nudge_selected(self, items, key, *, fine: bool = False) -> None:
        """Move selected items by 20% of grid (or 10% with Shift held)."""
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand

        from diagrammer.panels.settings_dialog import app_settings
        frac = app_settings.nudge_fraction
        step = self._grid_spacing * (frac * 0.5 if fine else frac)
        dx, dy = 0.0, 0.0
        if key == Qt.Key.Key_Left:
            dx = -step
        elif key == Qt.Key.Key_Right:
            dx = step
        elif key == Qt.Key.Key_Up:
            dy = -step
        elif key == Qt.Key.Key_Down:
            dy = step

        # Split into movable items and selected connections
        movable = [i for i in items if not isinstance(i, ConnectionItem)]
        selected_conns = [i for i in items if isinstance(i, ConnectionItem)]

        if not movable and not selected_conns:
            return

        self._diagram_scene.undo_stack.beginMacro("Nudge")

        # Move items (components, junctions, annotations, shapes)
        for item in movable:
            old_pos = item.pos()
            new_pos = QPointF(old_pos.x() + dx, old_pos.y() + dy)
            cmd = MoveComponentCommand(item, old_pos, new_pos)
            self._diagram_scene.undo_stack.push(cmd)

        # Shift waypoints of internal connections (both ends in movable set)
        item_ids = set(id(i) for i in movable)
        for si in self._diagram_scene.items():
            if not isinstance(si, ConnectionItem):
                continue
            if (id(si.source_port.component) in item_ids and
                    id(si.target_port.component) in item_ids):
                old_wps = [QPointF(w) for w in si.vertices]
                if old_wps:
                    new_wps = [QPointF(w.x() + dx, w.y() + dy) for w in old_wps]
                    cmd = EditWaypointsCommand(si, old_wps, new_wps)
                    self._diagram_scene.undo_stack.push(cmd)

        # Shift waypoints of explicitly selected connections. For free
        # wires, also nudge the endpoint JunctionItems so they stay
        # anchored to the wire — otherwise arrow-key nudging leaves
        # trailing stubs because the junctions don't move with the
        # waypoints.
        from diagrammer.items.junction_item import JunctionItem
        already_moved_ids = set(id(i) for i in movable)
        for conn in selected_conns:
            old_wps = [QPointF(w) for w in conn.vertices]
            if old_wps:
                new_wps = [QPointF(w.x() + dx, w.y() + dy) for w in old_wps]
                cmd = EditWaypointsCommand(conn, old_wps, new_wps)
                self._diagram_scene.undo_stack.push(cmd)
            for port_item in (conn.source_port, conn.target_port):
                pc = port_item.component if port_item else None
                if isinstance(pc, JunctionItem) and id(pc) not in already_moved_ids:
                    old_pos = pc.pos()
                    new_pos = QPointF(old_pos.x() + dx, old_pos.y() + dy)
                    self._diagram_scene.undo_stack.push(
                        MoveComponentCommand(pc, old_pos, new_pos)
                    )
                    already_moved_ids.add(id(pc))

        self._diagram_scene.undo_stack.endMacro()
        self._diagram_scene.update_connections()

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
            self._finish_trace(snapped)
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
                # Compounds: instantiate from the structural manifest so
                # sub-components keep their stretchability and the placement
                # can be ungrouped/edited.
                if getattr(comp_def, "compound_manifest_path", None):
                    from diagrammer.commands.add_compound_command import (
                        AddCompoundCommand,
                    )
                    from diagrammer.io.compound_manifest import (
                        load_compound_manifest,
                    )
                    manifest = load_compound_manifest(
                        comp_def.compound_manifest_path)
                    if manifest is not None:
                        cmd = AddCompoundCommand(
                            self._diagram_scene, manifest, snapped,
                            self._diagram_scene.library, name=comp_def.name,
                        )
                        self._diagram_scene.undo_stack.push(cmd)
                    else:
                        # Fall back to the static SVG placement if the
                        # manifest can't be parsed.
                        self._diagram_scene.place_component(comp_def, snapped)
                else:
                    self._diagram_scene.place_component(comp_def, snapped)
                # Record as recently used
                main_win = self.window()
                if hasattr(main_win, '_library_panel'):
                    main_win._library_panel.record_use(key)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # -- Context menu --

    def contextMenuEvent(self, event) -> None:
        from PySide6.QtWidgets import QMenu
        from diagrammer.items.shape_item import LineItem, ShapeItem

        scene_pos = self.mapToScene(event.pos())
        item = self._item_at_pos(event.pos())
        menu = QMenu(self)
        main_win = self.window()

        # Check for waypoint at this position on any selected connection
        wp_conn, wp_idx = self._find_any_waypoint_at(scene_pos)
        if wp_conn is not None:
            _wc, _wi = wp_conn, wp_idx  # capture for lambda
            delete_wp_act = menu.addAction(f"Delete Waypoint {wp_idx + 1}")
            delete_wp_act.triggered.connect(lambda: _wc._delete_waypoint(_wi))

        if isinstance(item, ConnectionItem):
            if menu.actions():
                menu.addSeparator()
            adopt_act = menu.addAction("Use as Default Style")
            adopt_act.triggered.connect(lambda: self._adopt_connection_style(item))
        elif isinstance(item, (ShapeItem, LineItem)):
            adopt_act = menu.addAction("Use as Default Style")
            adopt_act.triggered.connect(lambda: self._adopt_shape_style(item))

        selected = self._diagram_scene.selectedItems()

        # Group / Ungroup
        if selected:
            from diagrammer.commands.group_command import get_top_group as _ctx_gtg
            has_group = any(_ctx_gtg(i) for i in selected)
            ungrouped_count = sum(1 for i in selected
                                  if hasattr(i, '_group_ids') and not _ctx_gtg(i))

            if menu.actions():
                menu.addSeparator()

            if has_group:
                ungroup_act = menu.addAction("Ungroup")
                ungroup_act.triggered.connect(main_win._ungroup_selected)
            if len(selected) >= 2:
                group_act = menu.addAction("Group")
                group_act.triggered.connect(main_win._group_selected)

        # Create Component from Selection
        if selected and len(selected) >= 2:
            if menu.actions():
                menu.addSeparator()
            create_comp_act = menu.addAction("Create Component from Selection...")
            create_comp_act.triggered.connect(main_win._create_component_from_selection)

        # Delete
        if selected:
            if menu.actions():
                menu.addSeparator()
            del_act = menu.addAction("Delete")
            del_act.triggered.connect(main_win._delete_selected)

        if menu.actions():
            menu.exec(event.globalPos())
        event.accept()

    def _adopt_connection_style(self, item) -> None:
        """Set global defaults to match this connection's style."""
        from diagrammer.panels.settings_dialog import app_settings
        app_settings.default_line_width = item.line_width
        app_settings.default_line_color = item.line_color
        app_settings.default_corner_radius = item.corner_radius
        app_settings.save()

    def _adopt_shape_style(self, item) -> None:
        """Set global defaults to match this shape's style."""
        from diagrammer.panels.settings_dialog import app_settings
        from diagrammer.items.shape_item import ShapeItem
        app_settings.default_line_width = item.stroke_width
        app_settings.default_line_color = item.stroke_color
        if isinstance(item, ShapeItem):
            app_settings.default_component_fill = item.fill_color
        app_settings.save()

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
