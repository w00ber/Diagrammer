"""ConnectionItem -- a routed connection line between two ports.

Implements KiCad/Altium-style interactive routing:
  - User-placed **waypoints** define the coarse shape of a connection.
  - Between consecutive waypoints (and from/to the source/target ports)
    an automatic orthogonal router fills in H/V segments.
  - The user can **drag individual segments** of the expanded route;
    dragging a horizontal segment moves it vertically (and stretches
    its neighbours), and vice-versa for vertical segments.
  - Double-click on a segment to insert a new waypoint.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from diagrammer.utils.geometry import (
    build_rounded_path,
    closest_point_on_segment,
    ortho_route,
    ortho_route_45,
    point_distance,
    segment_orientation,
)

# Routing mode constants
ROUTE_ORTHO = "ortho"        # H/V segments only
ROUTE_ORTHO_45 = "ortho_45"  # H/V + 45-degree diagonals
ROUTE_DIRECT = "direct"      # Straight lines between waypoints (for rotated groups)

if TYPE_CHECKING:
    from diagrammer.items.port_item import PortItem

# ---------------------------------------------------------------------------
# Visual defaults
# ---------------------------------------------------------------------------

DEFAULT_LINE_COLOR = QColor(50, 50, 50)
DEFAULT_LINE_WIDTH = 3.0  # match SVG component wiring stroke width
DEFAULT_CORNER_RADIUS = 8.0
SELECTION_COLOR = QColor(0, 120, 215)
VERTEX_HANDLE_SIZE = 6.0
VERTEX_HANDLE_COLOR = QColor(0, 120, 215)
SEGMENT_HOVER_COLOR = QColor(0, 120, 215, 80)
HIT_TOLERANCE = 8.0

# Tolerance (px) for deciding whether a segment is H or V.
_ORIENT_TOL = 2.0


class ConnectionItem(QGraphicsPathItem):
    """A connection line between two ports with orthogonal routing.

    **Data model**

    ``_waypoints``
        User-placed intermediate points (does *not* include the port
        endpoints).  This is the persistent representation saved/restored
        by the undo system and clipboard.

    The *expanded* route (``all_points()``) inserts auto-routed orthogonal
    segments between each pair of consecutive key-points (source port,
    waypoints, target port).

    **Interaction**

    - Drag a waypoint handle to move it freely.
    - Drag a segment of the expanded route to shift it perpendicular to its
      orientation (KiCad-style); adjacent segments stretch to stay connected.
    - Double-click a segment to insert a new waypoint.
    """

    def __init__(
        self,
        source_port: PortItem,
        target_port: PortItem,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._group_id: str | None = None
        self._group_ids: list[str] = []
        self._source_port = source_port
        self._target_port = target_port

        # Style ----------------------------------------------------------
        self._line_color = QColor(DEFAULT_LINE_COLOR)
        self._line_width = DEFAULT_LINE_WIDTH
        self._corner_radius = DEFAULT_CORNER_RADIUS
        self._routing_mode: str = ROUTE_ORTHO  # default; scene can override

        # Waypoints (user-placed intermediate points, NOT port endpoints) -
        self._waypoints: list[QPointF] = []

        # Cached expanded route (rebuilt by update_route) -----------------
        self._expanded: list[QPointF] = []

        # Multi-waypoint selection (Shift+click to toggle) ----------------
        self._selected_waypoints: set[int] = set()

        # Dragging state --------------------------------------------------
        self._dragging_waypoint: int | None = None  # index into _waypoints
        self._dragging_group: bool = False           # True when dragging selected group
        self._dragging_segment: int | None = None    # index into _expanded
        self._drag_start_pos: QPointF | None = None
        self._drag_start_expanded: list[QPointF] | None = None
        self._drag_start_waypoints: list[QPointF] | None = None  # for undo

        # Flags -----------------------------------------------------------
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(6)  # above components (z=5) — approach segments fill lead gaps cleanly

        # Initial route
        self.update_route()

    # =====================================================================
    # Public API
    # =====================================================================

    @property
    def instance_id(self) -> str:
        return self._id

    @property
    def source_port(self) -> PortItem:
        return self._source_port

    @property
    def target_port(self) -> PortItem:
        return self._target_port

    # -- Style properties -------------------------------------------------

    @property
    def line_color(self) -> QColor:
        return self._line_color

    @line_color.setter
    def line_color(self, color: QColor) -> None:
        self._line_color = color
        self.update()

    @property
    def line_width(self) -> float:
        return self._line_width

    @line_width.setter
    def line_width(self, width: float) -> None:
        self._line_width = width
        self.update()

    @property
    def corner_radius(self) -> float:
        return self._corner_radius

    @corner_radius.setter
    def corner_radius(self, radius: float) -> None:
        self._corner_radius = radius
        self.update_route()

    @property
    def routing_mode(self) -> str:
        """Routing mode: ROUTE_ORTHO (H/V only) or ROUTE_ORTHO_45 (H/V + 45-degree)."""
        return self._routing_mode

    @routing_mode.setter
    def routing_mode(self, mode: str) -> None:
        if mode != self._routing_mode:
            self._routing_mode = mode
            self.update_route()

    # -- Waypoint / vertex access ----------------------------------------
    # The public property is called ``vertices`` for backward compatibility
    # with the undo commands and copy/paste logic.  Internally these are
    # *waypoints*.

    @property
    def vertices(self) -> list[QPointF]:
        """Return the user-placed waypoints (backward-compat name)."""
        return self._waypoints

    @vertices.setter
    def vertices(self, verts: list[QPointF]) -> None:
        self._waypoints = [QPointF(v) for v in verts]
        self.update_route()

    # =====================================================================
    # Route expansion
    # =====================================================================

    def _key_points(self) -> list[QPointF]:
        """Return the ordered key-points: [source_port, *waypoints, target_port]."""
        start = self._source_port.scene_center()
        end = self._target_port.scene_center()
        return [start] + [QPointF(w) for w in self._waypoints] + [end]

    def _expand_route(self, p1: QPointF, p2: QPointF) -> list[QPointF]:
        """Generate routed segment points between two key-points.

        Uses ortho_route (H/V only) or ortho_route_45 (H/V + diagonals)
        depending on the connection's routing_mode.

        Returns at least ``[p1, ..., p2]``.  The first point is always *p1*
        and the last is always *p2* so that consecutive expansions can be
        concatenated by dropping the duplicate junction point.
        """
        if self._routing_mode == ROUTE_DIRECT:
            return [QPointF(p1), QPointF(p2)]  # straight line, no auto-routing
        if self._routing_mode == ROUTE_ORTHO_45:
            return ortho_route_45(p1, p2)
        return ortho_route(p1, p2)

    def _build_expanded(self) -> list[QPointF]:
        """Expand the full route from key-points through ortho_route.

        Adds short lead-extension segments at ports so that right-angle
        junctions between the wire and component leads get properly rounded
        by build_rounded_path.

        Returns the complete point list used for rendering.
        """
        kps = self._key_points()
        if len(kps) < 2:
            return list(kps)

        result: list[QPointF] = []
        for i in range(len(kps) - 1):
            seg = self._expand_route(kps[i], kps[i + 1])
            if result:
                result.extend(seg[1:])
            else:
                result.extend(seg)

        # Add approach segments at ports to fill the gap left by shortened
        # leads — but only when not in the middle of a drag operation,
        # to avoid route corruption during multi-item moves.
        ext = self._corner_radius
        scene = self.scene()
        is_dragging = False
        if scene:
            views = scene.views()
            if views and hasattr(views[0], '_dragging_components'):
                is_dragging = bool(views[0]._dragging_components)
        if ext > 0 and len(result) >= 2 and not is_dragging:
            self._add_lead_approach(result, self._source_port, at_start=True, ext=ext)
            self._add_lead_approach(result, self._target_port, at_start=False, ext=ext)
            # Wire-to-wire junction: extend into the other wire's direction
            # for smooth corner rounding where two wires meet
            if scene:
                self._add_wire_junction_approach(result, self._source_port,
                                                 at_start=True, ext=ext, scene=scene)
                self._add_wire_junction_approach(result, self._target_port,
                                                 at_start=False, ext=ext, scene=scene)

        return result

    @staticmethod
    def _get_scene_approach(port) -> tuple[float, float]:
        """Get the port's lead direction in scene coords (accounts for rotation/flip)."""
        adx = port.approach_dx
        ady = port.approach_dy
        if abs(adx) + abs(ady) == 0:
            return (0.0, 0.0)
        comp = port.component
        p0 = port.scene_center()
        local_offset = QPointF(port.pos().x() + adx, port.pos().y() + ady)
        p1 = comp.mapToScene(local_offset)
        dx = p1.x() - p0.x()
        dy = p1.y() - p0.y()
        length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
        return (dx / length, dy / length)

    @staticmethod
    def _add_lead_approach(
        points: list[QPointF], port, *, at_start: bool, ext: float
    ) -> None:
        """Add a short segment along the lead direction at a port.

        This fills the gap created by lead shortening.  The segment goes
        INWARD from the port (opposite of approach), overlapping where
        the lead used to be.  The port becomes an intermediate bend point
        that build_rounded_path can round.

        Only added when the wire arrives perpendicular to the lead.
        """
        adx, ady = ConnectionItem._get_scene_approach(port)
        if abs(adx) + abs(ady) == 0:
            return

        # Check terminal segment direction
        if at_start and len(points) >= 2:
            seg_dx = points[1].x() - points[0].x()
            seg_dy = points[1].y() - points[0].y()
        elif not at_start and len(points) >= 2:
            seg_dx = points[-1].x() - points[-2].x()
            seg_dy = points[-1].y() - points[-2].y()
        else:
            return

        seg_len = max((seg_dx ** 2 + seg_dy ** 2) ** 0.5, 1e-9)
        dot = abs(adx * (seg_dx / seg_len) + ady * (seg_dy / seg_len))
        if dot > 0.5:
            return  # already aligned with lead — no bend needed

        # Inward point: fills the gap left by the shortened lead.
        # 2x radius so build_rounded_path applies the full corner radius
        # (it clamps to half the shortest segment).
        reach = ext * 2.0
        if at_start:
            inner = QPointF(
                points[0].x() - adx * reach,
                points[0].y() - ady * reach,
            )
            points.insert(0, inner)
        else:
            inner = QPointF(
                points[-1].x() - adx * reach,
                points[-1].y() - ady * reach,
            )
            points.append(inner)

    def _add_wire_junction_approach(
        self, points: list[QPointF], port, *, at_start: bool,
        ext: float, scene,
    ) -> None:
        """Extend a wire endpoint along the other wire's direction at a junction.

        When two wires meet at a JunctionItem, this adds a short segment
        along the OTHER wire's terminal direction so that build_rounded_path
        creates a smooth corner where the wires join.

        Only applies when the port belongs to a JunctionItem and exactly
        one other connection shares that port.
        """
        from diagrammer.items.junction_item import JunctionItem
        comp = port.component
        if not isinstance(comp, JunctionItem):
            return

        # Find the other connection on this junction
        other_conn = None
        count = 0
        for item in scene.items():
            if not isinstance(item, ConnectionItem) or item is self:
                continue
            if item.source_port is port or item.target_port is port:
                other_conn = item
                count += 1
                if count > 1:
                    return  # More than one other wire — T-junction, don't join

        if other_conn is None:
            return

        # Get the other wire's direction at this shared port
        other_kps = other_conn._key_points()
        if len(other_kps) < 2:
            return

        if other_conn.source_port is port:
            # Other wire starts at this port — its direction goes toward kps[1]
            dx = other_kps[1].x() - other_kps[0].x()
            dy = other_kps[1].y() - other_kps[0].y()
        else:
            # Other wire ends at this port — its direction comes from kps[-2]
            dx = other_kps[-2].x() - other_kps[-1].x()
            dy = other_kps[-2].y() - other_kps[-1].y()

        length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
        ux, uy = dx / length, dy / length

        # Check if this wire's terminal segment is perpendicular to the other wire
        if at_start and len(points) >= 2:
            seg_dx = points[1].x() - points[0].x()
            seg_dy = points[1].y() - points[0].y()
        elif not at_start and len(points) >= 2:
            seg_dx = points[-1].x() - points[-2].x()
            seg_dy = points[-1].y() - points[-2].y()
        else:
            return

        seg_len = max((seg_dx ** 2 + seg_dy ** 2) ** 0.5, 1e-9)
        dot = abs(ux * (seg_dx / seg_len) + uy * (seg_dy / seg_len))
        if dot > 0.5:
            return  # Already aligned — no corner needed

        # Add a point along the other wire's direction to create a bend
        reach = ext * 2.0
        if at_start:
            inner = QPointF(
                points[0].x() + ux * reach,
                points[0].y() + uy * reach,
            )
            points.insert(0, inner)
        else:
            inner = QPointF(
                points[-1].x() + ux * reach,
                points[-1].y() + uy * reach,
            )
            points.append(inner)

    def all_points(self) -> list[QPointF]:
        """Full expanded point list used for rendering.

        This is the cached expanded route (rebuilt by :meth:`update_route`).
        """
        return list(self._expanded)

    def update_route(self) -> None:
        """Rebuild the expanded route and QPainterPath."""
        self._expanded = self._build_expanded()
        path = build_rounded_path(self._expanded, self._corner_radius)
        self.setPath(path)

    # =====================================================================
    # Mapping expanded-route edits back to waypoints
    # =====================================================================

    def _expanded_index_to_key_index(self, exp_idx: int) -> int | None:
        """Map an index in ``_expanded`` to the index in ``_key_points()``
        that the point originated from, or ``None`` if it is an
        auto-generated bend (not a key-point).
        """
        kps = self._key_points()
        if not kps:
            return None
        if exp_idx < 0 or exp_idx >= len(self._expanded):
            return None
        pt = self._expanded[exp_idx]
        for ki, kp in enumerate(kps):
            if point_distance(pt, kp) < 0.5:
                return ki
        return None

    def _store_expanded_as_waypoints(self, expanded: list[QPointF]) -> None:
        """Given a modified expanded route, derive new waypoints.

        Strategy: after the user drags a segment, the expanded list has been
        mutated in place.  We want to preserve all the intermediate bend
        points as explicit waypoints so the shape is fully captured.

        We strip the first and last point (those are the port endpoints) and
        store everything in between as waypoints.
        """
        if len(expanded) <= 2:
            self._waypoints = []
        else:
            self._waypoints = [QPointF(p) for p in expanded[1:-1]]
        self.update_route()

    # =====================================================================
    # Painting
    # =====================================================================

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        pen = QPen(
            SELECTION_COLOR if (self.isSelected() and not self._group_id) else self._line_color,
            self._line_width,
        )
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

        # Draw waypoint handles when selected
        if self.isSelected():
            for i, w in enumerate(self._waypoints):
                r = VERTEX_HANDLE_SIZE / 2
                if i in self._selected_waypoints:
                    # Selected waypoints: bright orange filled diamond with border
                    sr = r * 1.6
                    painter.setPen(QPen(QColor(200, 80, 0), 2.0))
                    painter.setBrush(QColor(255, 160, 0))
                    diamond = QPolygonF([
                        QPointF(w.x(), w.y() - sr),
                        QPointF(w.x() + sr, w.y()),
                        QPointF(w.x(), w.y() + sr),
                        QPointF(w.x() - sr, w.y()),
                    ])
                    painter.drawPolygon(diamond)
                else:
                    # Unselected: small filled square
                    painter.setPen(QPen(VERTEX_HANDLE_COLOR, 1.0))
                    painter.setBrush(VERTEX_HANDLE_COLOR)
                    painter.drawRect(
                        QRectF(w.x() - r, w.y() - r, VERTEX_HANDLE_SIZE, VERTEX_HANDLE_SIZE)
                    )

    def boundingRect(self) -> QRectF:
        extra = max(self._line_width, VERTEX_HANDLE_SIZE, HIT_TOLERANCE) / 2 + 2
        return self.path().boundingRect().adjusted(-extra, -extra, extra, extra)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self._line_width + HIT_TOLERANCE, 12.0))
        return stroker.createStroke(self.path())

    # =====================================================================
    # Grid snapping for waypoints
    # =====================================================================

    def _snap_to_grid(self, pos: QPointF) -> QPointF:
        """Snap a position to the grid if snapping is enabled."""
        from diagrammer.canvas.grid import snap_to_grid
        scene = self.scene()
        if scene is None:
            return pos
        views = scene.views()
        if not views:
            return pos
        view = views[0]
        if not getattr(view, '_snap_enabled', True):
            return pos
        return snap_to_grid(pos, view.grid_spacing)

    # =====================================================================
    # Hit-testing helpers
    # =====================================================================

    def _find_waypoint_at(self, scene_pos: QPointF) -> int | None:
        """Return the index into ``_waypoints`` of the handle near *scene_pos*."""
        for i, w in enumerate(self._waypoints):
            if point_distance(scene_pos, w) < VERTEX_HANDLE_SIZE * 2:
                return i
        return None

    def _find_segment_at(self, scene_pos: QPointF) -> int | None:
        """Return the segment index in the expanded route nearest to *scene_pos*.

        A segment ``i`` connects ``_expanded[i]`` to ``_expanded[i+1]``.
        Returns ``None`` if nothing is within ``HIT_TOLERANCE * 2``.
        """
        pts = self._expanded
        if len(pts) < 2:
            return None

        best_dist = HIT_TOLERANCE * 2
        best_idx: int | None = None
        for i in range(len(pts) - 1):
            _, dist = closest_point_on_segment(scene_pos, pts[i], pts[i + 1])
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        return best_idx

    # =====================================================================
    # Segment dragging (KiCad mechanic)
    # =====================================================================

    @staticmethod
    def _seg_is_horizontal(p1: QPointF, p2: QPointF) -> bool | None:
        """Return ``True`` if the segment is horizontal, ``False`` if vertical,
        ``None`` if diagonal (ambiguous)."""
        orient = segment_orientation(p1, p2)
        if orient == "h":
            return True
        if orient == "v":
            return False
        return None

    def _apply_segment_drag(
        self,
        seg_idx: int,
        delta: QPointF,
        base_expanded: list[QPointF],
    ) -> list[QPointF]:
        """Compute a new expanded-point list after dragging segment *seg_idx*.

        *base_expanded* is the snapshot taken at drag start.

        The rule (KiCad-style):
        - A **horizontal** segment is shifted **vertically** by ``delta.y()``.
        - A **vertical** segment is shifted **horizontally** by ``delta.x()``.
        - Adjacent segments stretch/contract to stay connected.
        - Diagonal segments are shifted freely (both axes).
        """
        pts = [QPointF(p) for p in base_expanded]
        n = len(pts)
        if seg_idx < 0 or seg_idx >= n - 1:
            return pts

        p1 = base_expanded[seg_idx]
        p2 = base_expanded[seg_idx + 1]
        is_h = self._seg_is_horizontal(p1, p2)

        # Indices of the two endpoints of the dragged segment
        ia = seg_idx
        ib = seg_idx + 1

        if is_h is True:
            # Horizontal segment -- move it vertically
            dy = delta.y()
            pts[ia] = QPointF(pts[ia].x(), base_expanded[ia].y() + dy)
            pts[ib] = QPointF(pts[ib].x(), base_expanded[ib].y() + dy)
            # Stretch adjacent vertical segments: the point before ia and
            # the point after ib keep their positions, but ia/ib have new Y.
            # The segments (ia-1 -> ia) and (ib -> ib+1) naturally adjust
            # because we only moved ia and ib.  However, if the adjacent
            # segment is ALSO horizontal (e.g. at the port endpoints), we
            # need to adjust the connecting point's Y to maintain an
            # orthogonal connection.
            # -- previous neighbour --
            if ia > 0:
                prev = base_expanded[ia - 1]
                prev_orient = segment_orientation(prev, base_expanded[ia])
                if prev_orient == "h":
                    # The previous segment is horizontal too; insert a
                    # vertical step by adjusting prev's Y to match the new ia.
                    pts[ia - 1] = QPointF(prev.x(), base_expanded[ia].y() + dy)
            # -- next neighbour --
            if ib < n - 1:
                nxt = base_expanded[ib + 1]
                nxt_orient = segment_orientation(base_expanded[ib], nxt)
                if nxt_orient == "h":
                    pts[ib + 1] = QPointF(nxt.x(), base_expanded[ib].y() + dy)

        elif is_h is False:
            # Vertical segment -- move it horizontally
            dx = delta.x()
            pts[ia] = QPointF(base_expanded[ia].x() + dx, pts[ia].y())
            pts[ib] = QPointF(base_expanded[ib].x() + dx, pts[ib].y())
            if ia > 0:
                prev = base_expanded[ia - 1]
                prev_orient = segment_orientation(prev, base_expanded[ia])
                if prev_orient == "v":
                    pts[ia - 1] = QPointF(base_expanded[ia].x() + dx, prev.y())
            if ib < n - 1:
                nxt = base_expanded[ib + 1]
                nxt_orient = segment_orientation(base_expanded[ib], nxt)
                if nxt_orient == "v":
                    pts[ib + 1] = QPointF(base_expanded[ib].x() + dx, nxt.y())

        else:
            # Diagonal -- free drag (both components)
            pts[ia] = QPointF(
                base_expanded[ia].x() + delta.x(),
                base_expanded[ia].y() + delta.y(),
            )
            pts[ib] = QPointF(
                base_expanded[ib].x() + delta.x(),
                base_expanded[ib].y() + delta.y(),
            )

        # Clamp: never move the port endpoints (first / last point).
        pts[0] = QPointF(base_expanded[0])
        pts[-1] = QPointF(base_expanded[-1])

        return pts

    # =====================================================================
    # Mouse interaction
    # =====================================================================

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        # Don't allow individual wire manipulation when in a group
        if self._group_id:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            pos = event.scenePos()
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

            wi = self._find_waypoint_at(pos)

            # Ctrl+Shift+click on waypoint → delete it
            if wi is not None and ctrl and shift:
                self._delete_waypoint(wi)
                event.accept()
                return

            # Ctrl+click on segment → insert a new waypoint
            if wi is None and ctrl and not shift:
                self._insert_waypoint_at(pos)
                event.accept()
                return

            # Shift+click on waypoint → toggle its selection
            if wi is not None and shift:
                if wi in self._selected_waypoints:
                    self._selected_waypoints.discard(wi)
                else:
                    self._selected_waypoints.add(wi)
                self.update()
                event.accept()
                return

            # Click on a selected waypoint → start group drag
            if wi is not None and wi in self._selected_waypoints:
                self._dragging_waypoint = wi
                self._dragging_group = True
                self._drag_start_pos = QPointF(pos)
                self._drag_start_expanded = [QPointF(p) for p in self._expanded]
                self._drag_start_waypoints = [QPointF(w) for w in self._waypoints]
                event.accept()
                return

            # Click on an unselected waypoint → select it and start drag
            if wi is not None:
                if not shift:
                    self._selected_waypoints.clear()
                self._selected_waypoints.add(wi)
                self._dragging_waypoint = wi
                self._dragging_group = len(self._selected_waypoints) > 1
                self._drag_start_pos = QPointF(pos)
                self._drag_start_expanded = [QPointF(p) for p in self._expanded]
                self._drag_start_waypoints = [QPointF(w) for w in self._waypoints]
                self.update()
                event.accept()
                return

            # Click on empty space within the connection → clear waypoint selection
            if not shift:
                self._selected_waypoints.clear()
                self.update()

            # Segment drag
            si = self._find_segment_at(pos)
            if si is not None:
                self._dragging_segment = si
                self._drag_start_pos = QPointF(pos)
                self._drag_start_expanded = [QPointF(p) for p in self._expanded]
                self._drag_start_waypoints = [QPointF(w) for w in self._waypoints]
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        pos = event.scenePos()

        # --- Waypoint drag (single or group, snaps to grid) ---
        if self._dragging_waypoint is not None:
            if self._dragging_group and self._selected_waypoints and self._drag_start_pos:
                # Group drag: move all selected waypoints by the same snapped delta
                snapped_pos = self._snap_to_grid(pos)
                snapped_start = self._snap_to_grid(self._drag_start_pos)
                delta = snapped_pos - snapped_start
                if self._drag_start_waypoints:
                    for si in self._selected_waypoints:
                        if 0 <= si < len(self._waypoints) and si < len(self._drag_start_waypoints):
                            orig = self._drag_start_waypoints[si]
                            self._waypoints[si] = QPointF(orig.x() + delta.x(), orig.y() + delta.y())
                self.update_route()
            else:
                # Single waypoint drag
                wi = self._dragging_waypoint
                if 0 <= wi < len(self._waypoints):
                    self._waypoints[wi] = self._snap_to_grid(pos)
                    self.update_route()
            event.accept()
            return

        # --- Segment drag (KiCad-style, snapped to grid) ---
        if (
            self._dragging_segment is not None
            and self._drag_start_pos is not None
            and self._drag_start_expanded is not None
        ):
            # Snap the drag delta to grid increments for clean alignment
            snapped_pos = self._snap_to_grid(pos)
            snapped_start = self._snap_to_grid(self._drag_start_pos)
            delta = snapped_pos - snapped_start
            new_expanded = self._apply_segment_drag(
                self._dragging_segment, delta, self._drag_start_expanded
            )
            self._store_expanded_as_waypoints(new_expanded)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._dragging_waypoint is not None or self._dragging_segment is not None:
            # Push undo command for the waypoint change
            if self._drag_start_waypoints is not None:
                new_waypoints = [QPointF(w) for w in self._waypoints]
                if new_waypoints != self._drag_start_waypoints:
                    from diagrammer.commands.connect_command import EditWaypointsCommand
                    scene = self.scene()
                    if scene and hasattr(scene, 'undo_stack'):
                        cmd = EditWaypointsCommand(
                            self, self._drag_start_waypoints, new_waypoints
                        )
                        scene.undo_stack.push(cmd)
            self._dragging_waypoint = None
            self._dragging_group = False
            self._dragging_segment = None
            self._drag_start_pos = None
            self._drag_start_expanded = None
            self._drag_start_waypoints = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        """Right-click on a waypoint handle → context menu with Delete Waypoint."""
        if not self.isSelected():
            super().contextMenuEvent(event)
            return

        pos = event.scenePos()
        wi = self._find_waypoint_at(pos)
        if wi is not None:
            from PySide6.QtWidgets import QMenu
            menu = QMenu()
            delete_act = menu.addAction(f"Delete Waypoint {wi + 1}")
            action = menu.exec(event.screenPos())
            if action is delete_act:
                self._delete_waypoint(wi)
            event.accept()
            return
        super().contextMenuEvent(event)

    def _delete_waypoint(self, index: int) -> None:
        """Remove the waypoint at the given index and rebuild the route."""
        if 0 <= index < len(self._waypoints):
            self._waypoints.pop(index)
            self.update_route()

    def _insert_waypoint_at(self, pos: QPointF) -> None:
        """Insert a new waypoint at the closest point on the route to pos (Ctrl+Click)."""
        pts = self._expanded
        best_dist = float("inf")
        best_idx = 0
        best_proj = pos

        for i in range(len(pts) - 1):
            proj, dist = closest_point_on_segment(pos, pts[i], pts[i + 1])
            if dist < best_dist:
                best_dist = dist
                best_idx = i
                best_proj = proj

        if best_dist < HIT_TOLERANCE * 3:
            kps = self._key_points()
            kp_exp_indices = self._key_point_expanded_indices(kps)
            insert_wp_idx = self._wp_insert_index_for_expanded_segment(
                best_idx, kp_exp_indices
            )
            self._waypoints.insert(insert_wp_idx, self._snap_to_grid(best_proj))
            self.update_route()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        """Double-click on a connection — no action (waypoint add/remove uses Ctrl+Click)."""
        # Waypoint creation: Ctrl+Click (handled in mousePressEvent)
        # Waypoint deletion: Ctrl+Shift+Click (handled in mousePressEvent)
        super().mouseDoubleClickEvent(event)

    # -- Helpers for double-click insertion --------------------------------

    def _key_point_expanded_indices(
        self, kps: list[QPointF] | None = None
    ) -> list[int]:
        """Return for each key-point the index in ``_expanded`` where it appears.

        Because ``_build_expanded`` concatenates segments and drops duplicate
        junctions, key-point *k* always appears at a specific index in the
        expanded list.
        """
        if kps is None:
            kps = self._key_points()
        if len(kps) < 2:
            return list(range(len(kps)))

        indices: list[int] = [0]  # First key-point is always index 0
        offset = 0
        for i in range(len(kps) - 1):
            seg = self._expand_route(kps[i], kps[i + 1])
            # This segment contributes (len(seg) - 1) new points to the
            # expanded list (the first point overlaps with the previous
            # segment's last point, except for the very first segment).
            if i == 0:
                offset += len(seg) - 1
            else:
                offset += len(seg) - 1
            indices.append(offset)
        return indices

    def _wp_insert_index_for_expanded_segment(
        self, exp_seg_idx: int, kp_exp_indices: list[int]
    ) -> int:
        """Determine the waypoint-list insertion index for a click on
        expanded segment *exp_seg_idx*.

        ``kp_exp_indices[k]`` gives the expanded index of key-point *k*.
        Key-point 0 is the source port; key-points 1..N-2 are the waypoints;
        key-point N-1 is the target port.

        The new waypoint should be inserted into ``_waypoints`` at the
        position corresponding to the key-point span that contains the
        clicked segment.
        """
        # Find which key-point span the segment falls in.
        # Span *k* covers expanded indices [kp_exp_indices[k] .. kp_exp_indices[k+1]).
        for k in range(len(kp_exp_indices) - 1):
            if exp_seg_idx < kp_exp_indices[k + 1]:
                # The segment is in span k (between key-point k and k+1).
                # In _waypoints, key-point 0 = source port (not a waypoint),
                # key-point 1 = _waypoints[0], etc.  So inserting after
                # key-point k means inserting at waypoint index k.
                return k
        # Fallback: append
        return len(self._waypoints)
