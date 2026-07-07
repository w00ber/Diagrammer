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

import dataclasses
import uuid
from dataclasses import dataclass
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
    fraction_at_point,
    ortho_route,
    ortho_route_45,
    point_at_fraction,
    point_distance,
    segment_orientation,
)

# Routing mode constants
ROUTE_ORTHO = "ortho"        # H/V segments only
ROUTE_ORTHO_45 = "ortho_45"  # H/V + 45-degree diagonals
ROUTE_DIRECT = "direct"      # Straight lines between waypoints (for rotated groups)

if TYPE_CHECKING:
    from diagrammer.items.port_item import PortItem


class Waypoint:
    """A user-placed wire bend, stored relative to a connection port.

    Each waypoint anchors to one of the connection's two endpoint ports
    (``source_port`` or ``target_port``) by an offset ``(dx, dy)`` in
    the port's *local* coordinate system. Its scene position is
    reconstructed on demand via ``anchor.mapToScene((dx, dy))``.

    Because the port inherits its parent component's transform,
    port-local offsets rotate / flip with the parent automatically —
    so when the user transforms the parent (or a group containing it),
    the wire shape follows as a rigid body without any special-case
    transform code at the call site. This is what lets the group-rotate
    code drop the pre-capture / ROUTE_DIRECT hack.
    """

    __slots__ = ("anchor", "dx", "dy")

    def __init__(self, anchor: "PortItem", dx: float, dy: float) -> None:
        self.anchor = anchor
        self.dx = float(dx)
        self.dy = float(dy)

    def to_scene(self) -> QPointF:
        return self.anchor.mapToScene(QPointF(self.dx, self.dy))

    def __repr__(self) -> str:
        port_name = getattr(self.anchor, "port_name", "?")
        return f"Waypoint({port_name}, {self.dx:.1f}, {self.dy:.1f})"

@dataclass
class WireArrow:
    """A signal-flow direction arrow riding on a connection.

    Positioned by normalized arclength fraction ``t`` along the wire's
    expanded route, so it follows reroutes and component moves. Style
    fields left as ``None`` resolve against the app-wide defaults at
    paint time (junction end-marker pattern), so changing the global
    default restyles all default-styled arrows live.
    """

    t: float                          # arclength fraction, 0 = source, 1 = target
    forward: bool = True              # True = points source -> target
    style: str | None = None          # "filled" | "open"
    size: float | None = None         # arrow length, scene units
    line_width: float | None = None   # outline width for "open" style

    def copy(self) -> "WireArrow":
        return dataclasses.replace(self)


ARROW_STYLE_FILLED = "filled"
ARROW_STYLE_OPEN = "open"
WIRE_ARROW_STYLES = (ARROW_STYLE_FILLED, ARROW_STYLE_OPEN)


# ---------------------------------------------------------------------------
# Visual defaults
# ---------------------------------------------------------------------------

DEFAULT_LINE_COLOR = QColor(50, 50, 50)
DEFAULT_LINE_WIDTH = 3.0  # match SVG component wiring stroke width
DEFAULT_CORNER_RADIUS = 8.0
SELECTION_COLOR = QColor(0, 120, 215)
# Selection is shown as a soft halo drawn BENEATH the wire (so the wire
# keeps its own color and stays fully visible) rather than by recoloring
# the wire itself. Light alpha so it reads as a glow, not a slab.
SELECTION_HALO_COLOR = QColor(0, 120, 215, 90)
# Extra width of the halo beyond the wire, in scene units — enough to
# show a ring of colour on each side even for thin wires.
SELECTION_HALO_EXTRA = 8.0
VERTEX_HANDLE_SIZE = 8.0
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
        self._closed: bool = False  # True for closed polygon (source == target)

        # Waypoints. Two parallel representations:
        #   ``_anchors`` is the canonical port-relative storage (Waypoint
        #   objects). It survives port motion: anchored waypoints follow
        #   their port through any transform.
        #   ``_waypoints`` is a scene-space cache derived from anchors,
        #   exposed for compat with the (large) body of code that reads
        #   ``conn._waypoints[i]`` as a QPointF. The two stay in sync
        #   through the helper methods on this class — direct writes to
        #   ``_waypoints`` will go stale on the next ``update_route()``,
        #   so call sites that mutate must use ``_set_waypoint_at`` /
        #   ``_set_waypoints_from_scene`` / ``_pop_waypoint_at`` instead.
        self._anchors: list[Waypoint] = []
        self._waypoints: list[QPointF] = []

        # Cached expanded route (rebuilt by update_route) -----------------
        self._expanded: list[QPointF] = []

        # Direction arrows (signal-flow indicators) ------------------------
        self._arrows: list[WireArrow] = []

        # Multi-waypoint selection (Shift+click to toggle) ----------------
        self._selected_waypoints: set[int] = set()

        # Hover state ------------------------------------------------------
        self._hovered: bool = False
        self._hover_segment: int | None = None  # index into _expanded

        # Dragging state --------------------------------------------------
        self._dragging_waypoint: int | None = None  # index into _waypoints
        self._dragging_group: bool = False           # True when dragging selected group
        self._dragging_segment: int | None = None    # index into _expanded
        self._dragging_arrow: int | None = None      # index into _arrows
        self._drag_start_pos: QPointF | None = None
        self._drag_start_expanded: list[QPointF] | None = None
        self._drag_start_waypoints: list[QPointF] | None = None  # for undo
        self._drag_start_arrows: list[WireArrow] | None = None   # for undo
        self._drag_start_junction_pos: dict[str, QPointF] = {}  # instance_id → start pos

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

    @property
    def closed(self) -> bool:
        """True when this connection forms a closed polygon (source == target)."""
        return self._closed

    @closed.setter
    def closed(self, value: bool) -> None:
        if value != self._closed:
            self._closed = value
            self.update_route()

    # -- Direction arrows --------------------------------------------------

    @property
    def arrows(self) -> list[WireArrow]:
        """Copies of this wire's direction arrows (safe to mutate)."""
        return [a.copy() for a in self._arrows]

    @arrows.setter
    def arrows(self, arrs: list[WireArrow]) -> None:
        # Entry point for ChangeStyleCommand: whole-list snapshots.
        self.prepareGeometryChange()
        self._arrows = [a.copy() for a in arrs]
        self.update()

    def _resolved_arrow(self, arrow: WireArrow) -> tuple[str, float, float]:
        """Fill an arrow's ``None`` fields from the app-wide defaults."""
        style, size, lw = arrow.style, arrow.size, arrow.line_width
        if style is None or size is None or lw is None:
            from diagrammer.panels.settings_dialog import app_settings
            if style is None:
                style = getattr(app_settings, "default_wire_arrow_style", ARROW_STYLE_FILLED)
            if size is None:
                size = getattr(app_settings, "default_wire_arrow_size", 14.0)
            if lw is None:
                lw = getattr(app_settings, "default_wire_arrow_line_width", 2.0)
        if style not in WIRE_ARROW_STYLES:
            style = ARROW_STYLE_FILLED
        return style, size, lw

    def _max_arrow_extent(self) -> float:
        """Largest half-extent any arrow adds beyond the wire centerline."""
        extent = 0.0
        for a in self._arrows:
            _style, size, lw = self._resolved_arrow(a)
            extent = max(extent, size * 0.6 + lw)
        return extent

    def _find_arrow_at(self, scene_pos: QPointF) -> int | None:
        """Return the index of the direction arrow near *scene_pos*.

        Tolerance is deliberately below the waypoint pick tolerance so an
        arrow sitting near a waypoint handle doesn't steal its clicks.
        """
        best_idx: int | None = None
        best_dist = float("inf")
        for i, a in enumerate(self._arrows):
            _style, size, _lw = self._resolved_arrow(a)
            pt, _tang = point_at_fraction(self._expanded, a.t)
            tol = max(self._pick_tolerance(10.0), size * 0.6)
            dist = point_distance(scene_pos, pt)
            if dist < tol and dist < best_dist:
                best_dist = dist
                best_idx = i
        return best_idx

    def _push_arrows_change(self, old: list[WireArrow], new: list[WireArrow]) -> None:
        """Apply + record an arrows-list change (undoable when on a scene)."""
        scene = self.scene()
        undo_stack = getattr(scene, 'undo_stack', None) if scene else None
        if undo_stack is None:
            self.arrows = new
            return
        from diagrammer.commands.style_command import ChangeStyleCommand
        undo_stack.push(ChangeStyleCommand(self, 'arrows', old, new))

    def add_arrow_at(self, scene_pos: QPointF) -> None:
        """Add a direction arrow at *scene_pos* projected onto the route."""
        if len(self._expanded) < 2:
            return
        t, _proj, _dist = fraction_at_point(self._expanded, scene_pos)
        old = self.arrows
        self._push_arrows_change(old, old + [WireArrow(t=t)])

    def _flip_arrow(self, index: int) -> None:
        """Reverse the direction of arrow *index* (undoable)."""
        if not (0 <= index < len(self._arrows)):
            return
        old = self.arrows
        new = self.arrows
        new[index].forward = not new[index].forward
        self._push_arrows_change(old, new)

    def _delete_arrow(self, index: int) -> None:
        """Remove arrow *index* (undoable)."""
        if not (0 <= index < len(self._arrows)):
            return
        old = self.arrows
        new = [a for i, a in enumerate(old) if i != index]
        self._push_arrows_change(old, new)

    def _set_arrow_fields(self, index: int, **fields) -> None:
        """Update style fields of arrow *index* (undoable).

        Accepts any WireArrow field; ``None`` values reset a field to
        "use the global default".
        """
        if not (0 <= index < len(self._arrows)):
            return
        old = self.arrows
        new = self.arrows
        new[index] = dataclasses.replace(new[index], **fields)
        self._push_arrows_change(old, new)

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
        self._set_waypoints_from_scene(verts)
        self.update_route()

    # ----- port-relative waypoint storage helpers ------------------------

    def _make_anchor(self, scene_pt: QPointF) -> "Waypoint":
        """Bind *scene_pt* to whichever endpoint port is closer (manhattan).

        The source port wins ties so anchor selection is deterministic.
        The offset is stored in the chosen port's *local* coordinate
        system so the waypoint rotates/flips with the parent component
        automatically. Returns a ``Waypoint`` whose ``to_scene()``
        reproduces the original point (modulo float precision).
        """
        sc_src = self._source_port.scene_center()
        sc_tgt = self._target_port.scene_center()
        d_src = abs(scene_pt.x() - sc_src.x()) + abs(scene_pt.y() - sc_src.y())
        d_tgt = abs(scene_pt.x() - sc_tgt.x()) + abs(scene_pt.y() - sc_tgt.y())
        anchor = self._source_port if d_src <= d_tgt else self._target_port
        local = anchor.mapFromScene(scene_pt)
        return Waypoint(anchor, local.x(), local.y())

    def _set_waypoints_from_scene(self, scene_pts) -> None:
        """Replace all waypoints by rebinding each scene point to a port."""
        self._anchors = [self._make_anchor(QPointF(p)) for p in scene_pts]
        self._waypoints = [a.to_scene() for a in self._anchors]

    def _set_waypoint_at(self, idx: int, scene_pt: QPointF) -> None:
        """Replace waypoint *idx*, rebinding to the now-closest port."""
        self._anchors[idx] = self._make_anchor(QPointF(scene_pt))
        self._waypoints[idx] = self._anchors[idx].to_scene()

    def _pop_waypoint_at(self, idx: int) -> QPointF:
        """Remove waypoint *idx* (in lockstep across both representations)."""
        anchor = self._anchors.pop(idx)
        self._waypoints.pop(idx)
        return anchor.to_scene()

    def _refresh_from_anchors(self) -> None:
        """Recompute the scene-space cache from anchors.

        Call before any logic that reads ``_waypoints`` if the endpoint
        ports may have moved (component drag/rotate/flip). Safe to call
        eagerly — does no work beyond ``port.scene_center()`` lookups.
        """
        if len(self._anchors) != len(self._waypoints):
            # Defensive: recover from any direct write that bypassed the
            # helpers (treat ``_waypoints`` as the truth in that case).
            self._anchors = [self._make_anchor(QPointF(p))
                             for p in self._waypoints]
        self._waypoints = [a.to_scene() for a in self._anchors]

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

        # For closed polygons, the waypoints ARE the vertices.
        # Skip the duplicate endpoint and approach segments, but still
        # respect the routing mode between consecutive vertices.
        if self._closed:
            pts = [QPointF(w) for w in self._waypoints]
            if not pts:
                return []
            if self._routing_mode == ROUTE_DIRECT:
                return pts
            # Apply routing between consecutive vertices (wrapping around)
            result: list[QPointF] = []
            for i in range(len(pts)):
                seg = self._expand_route(pts[i], pts[(i + 1) % len(pts)])
                if result:
                    result.extend(seg[1:])
                else:
                    result.extend(seg)
            return result

        result: list[QPointF] = []
        for i in range(len(kps) - 1):
            seg = self._expand_route(kps[i], kps[i + 1])
            if result:
                result.extend(seg[1:])
            else:
                result.extend(seg)

        # Add approach segments at ports to fill the gap left by shortened
        # leads — but only when this connection's endpoints are being dragged,
        # to avoid route corruption during multi-item moves.
        ext = self._corner_radius
        scene = self.scene()
        is_dragging_this = False
        if scene:
            views = scene.views()
            if views and hasattr(views[0], '_dragging_components'):
                dragging = views[0]._dragging_components
                if dragging:
                    drag_ids = set(id(c) for c in dragging)
                    src_comp = self._source_port.component if self._source_port else None
                    tgt_comp = self._target_port.component if self._target_port else None
                    is_dragging_this = (
                        (src_comp is not None and id(src_comp) in drag_ids)
                        or (tgt_comp is not None and id(tgt_comp) in drag_ids)
                    )
        if ext > 0 and len(result) >= 2 and not is_dragging_this:
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

        # Find the other connection on this junction via port-connection index
        other_conn = None
        count = 0
        if hasattr(scene, 'connections_on_port'):
            for item in scene.connections_on_port(port):
                if item is not self:
                    other_conn = item
                    count += 1
                    if count > 1:
                        return  # More than one other wire — T-junction, don't join
        else:
            for item in scene.items():
                if not isinstance(item, ConnectionItem) or item is self:
                    continue
                if item.source_port is port or item.target_port is port:
                    other_conn = item
                    count += 1
                    if count > 1:
                        return

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

    def rebuild_expanded(self) -> None:
        """Recompute the expanded route (without touching the painted path).

        Refreshes the scene-space waypoint cache from anchors first so the
        wire automatically follows ports that have moved (component drag,
        rotate, flip) since the last route build.
        """
        self._refresh_from_anchors()
        self._expanded = self._build_expanded()
        if self._expanded:
            xs = [p.x() for p in self._expanded]
            ys = [p.y() for p in self._expanded]
            rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        else:
            rect = QRectF()
        pad = self._hop_radius() + 1.0
        self._expanded_rect = rect.adjusted(-pad, -pad, pad, pad)

    def rebuild_path(self) -> None:
        """Rebuild the painted QPainterPath from the current expanded route.

        Hop (crossover) geometry lives ONLY in the painted path — never in
        ``_expanded`` or the waypoints — so segment dragging and waypoint
        storage can never pick up hop artifacts.
        """
        hops = self._compute_hops()
        path = build_rounded_path(
            self._expanded, self._corner_radius, closed=self._closed,
            hops=hops, hop_radius=self._hop_radius(),
        )
        self.setPath(path)

    def update_route(self) -> None:
        """Rebuild the expanded route and QPainterPath."""
        self.rebuild_expanded()
        self.rebuild_path()

    def _hop_radius(self) -> float:
        """Radius of crossover hop semicircles for this wire."""
        return max(5.0, self._line_width * 2.5)

    def _compute_hops(self) -> list[tuple[QPointF, int]]:
        """Crossing points where THIS wire arcs over another wire.

        Returns ``(point, sign)`` pairs; ``sign=-1`` mirrors the hop's
        bulge (per the pair's ``flip`` override), else ``+1``. Only the
        crossing's resolved hop owner returns points; the other wire
        renders straight through. Closed polygons never own hops (but
        their outline can be hopped over, including the closing segment).
        """
        scene = self.scene()
        if scene is None or not hasattr(scene, 'resolve_crossover'):
            return []
        if self._closed or len(self._expanded) < 2:
            return []
        if not scene.crossover_scan_needed():
            return []

        from diagrammer.utils.geometry import segment_intersection
        hops: list[tuple[QPointF, int]] = []
        my_pts = self._expanded
        my_rect = self._expanded_rect
        for other in scene.items():
            if not isinstance(other, ConnectionItem) or other is self:
                continue
            style, owner_id, flip = scene.resolve_crossover(self, other)
            if style != "hop" or owner_id != self._id:
                continue
            other_rect = getattr(other, '_expanded_rect', None)
            if other_rect is None or not my_rect.intersects(other_rect):
                continue
            opts = other._expanded
            m = len(opts)
            if m < 2:
                continue
            sign = -1 if flip else 1
            num_other_segs = m if other._closed else m - 1
            for i in range(len(my_pts) - 1):
                for j in range(num_other_segs):
                    hit = segment_intersection(
                        my_pts[i], my_pts[i + 1],
                        opts[j], opts[(j + 1) % m],
                        endpoint_exclusion=1.0,
                    )
                    if hit is not None:
                        hops.append((hit, sign))
        return hops

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

        For open paths we strip the first and last point (port endpoints)
        and store everything in between.  For closed polygons all expanded
        points are waypoints — none should be stripped.

        JunctionItem endpoints are a special case: the junction is invisible,
        so the endpoint waypoint IS the user's only grab handle.  We keep
        it instead of stripping.
        """
        if self._closed:
            self._set_waypoints_from_scene([QPointF(p) for p in expanded])
            self.update_route()
            return

        from diagrammer.items.junction_item import JunctionItem
        src_comp = self._source_port.component if self._source_port else None
        tgt_comp = self._target_port.component if self._target_port else None
        src_is_junction = isinstance(src_comp, JunctionItem)
        tgt_is_junction = isinstance(tgt_comp, JunctionItem)
        src = self._source_port.scene_center() if self._source_port else QPointF()
        tgt = self._target_port.scene_center() if self._target_port else QPointF()

        if len(expanded) <= 2:
            # No interior bends.  Floating (junction) endpoints still need
            # a waypoint each as their visible grab handle.
            wps: list[QPointF] = []
            if src_is_junction:
                wps.append(QPointF(src))
            if tgt_is_junction:
                wps.append(QPointF(tgt))
        else:
            wps = [QPointF(p) for p in expanded[1:-1]]
            # Port-attached endpoints: strip waypoints that coincide with
            # the port — they are approach-segment artifacts and create
            # trailing stubs when the component is later moved.
            # Junction endpoints: ensure the endpoint waypoint is present
            # as the user's grab handle.
            if src_is_junction:
                if not wps or point_distance(wps[0], src) > 1.0:
                    wps.insert(0, QPointF(src))
            else:
                while wps and point_distance(wps[0], src) < 1.0:
                    wps.pop(0)
            if tgt_is_junction:
                if not wps or point_distance(wps[-1], tgt) > 1.0:
                    wps.append(QPointF(tgt))
            else:
                while wps and point_distance(wps[-1], tgt) < 1.0:
                    wps.pop()
        self._set_waypoints_from_scene(wps)
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
        # Selection / hover are shown as a halo drawn BENEATH the wire, so
        # the wire keeps its own colour and stays clearly visible on top.
        selected = self.isSelected() and not self._group_id
        hovered = self._hovered and not self._group_id
        if selected or hovered:
            halo_col = QColor(SELECTION_HALO_COLOR) if selected else QColor(SEGMENT_HOVER_COLOR)
            halo = QPen(halo_col, self._line_width + SELECTION_HALO_EXTRA)
            halo.setCapStyle(Qt.PenCapStyle.RoundCap)
            halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(halo)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())
        # When a selected wire is hovered, emphasise the segment a drag
        # would move — also beneath the wire, a touch stronger than the halo.
        if self._hover_segment is not None and selected:
            pts = self._expanded
            n = len(pts)
            num_segs = n if self._closed else n - 1
            if 0 <= self._hover_segment < num_segs:
                seg_color = QColor(SELECTION_COLOR)
                seg_color.setAlpha(150)
                seg_pen = QPen(seg_color, self._line_width + SELECTION_HALO_EXTRA)
                seg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(seg_pen)
                painter.drawLine(pts[self._hover_segment],
                                 pts[(self._hover_segment + 1) % n])

        # The wire itself, always in its own colour — selection never
        # hides it.
        pen = QPen(self._line_color, self._line_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

        # Direction arrows — never gated on selection: exports render
        # through paint() with selection chrome hidden, arrows must stay.
        if self._arrows:
            self._draw_direction_arrows(painter)

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

    def _draw_direction_arrows(self, painter: QPainter) -> None:
        """Paint the signal-flow arrows riding on this wire.

        Arrows sit on the raw expanded polyline (not the rounded/hopped
        painted path) — deviation at corners is a couple of px at the
        default corner radius.
        """
        pts = self._expanded
        if len(pts) < 2:
            return
        for a in self._arrows:
            style, size, lw = self._resolved_arrow(a)
            pt, tang = point_at_fraction(pts, a.t)
            ux, uy = tang.x(), tang.y()
            if not a.forward:
                ux, uy = -ux, -uy
            px, py = -uy, ux  # perpendicular
            half = size / 2.0
            half_w = size * 0.4
            tip = QPointF(pt.x() + ux * half, pt.y() + uy * half)
            base = QPointF(pt.x() - ux * half, pt.y() - uy * half)
            left = QPointF(base.x() + px * half_w, base.y() + py * half_w)
            right = QPointF(base.x() - px * half_w, base.y() - py * half_w)
            tri = QPolygonF([tip, left, right])

            if style == ARROW_STYLE_OPEN:
                # Hollow triangle: background fill masks the wire so the
                # outline reads cleanly (junction "open" marker pattern).
                from diagrammer.panels.settings_dialog import app_settings
                pen = QPen(self._line_color, lw)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(app_settings.current_background_color())
            else:
                painter.setPen(QPen(self._line_color, 1.0))
                painter.setBrush(self._line_color)
            painter.drawPolygon(tri)

    def boundingRect(self) -> QRectF:
        # Must cover the hover halo (line_width + HIT_TOLERANCE wide)
        # and the largest direction arrow.
        extra = max(self._line_width + HIT_TOLERANCE, VERTEX_HANDLE_SIZE) / 2 + 2
        extra = max(extra, self._max_arrow_extent())
        return self.path().boundingRect().adjusted(-extra, -extra, extra, extra)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self._line_width + HIT_TOLERANCE, 12.0))
        stroke = stroker.createStroke(self.path())
        # Arrows may extend beyond the stroke — add a clickable disc per
        # arrow so they can be grabbed on unselected wires too.
        if self._arrows and len(self._expanded) >= 2:
            for a in self._arrows:
                _style, size, _lw = self._resolved_arrow(a)
                pt, _tang = point_at_fraction(self._expanded, a.t)
                r = size * 0.6
                stroke.addEllipse(pt, r, r)
        return stroke

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

    def _record_endpoint_junction_starts(self) -> None:
        """Record starting positions of endpoint junctions before a waypoint drag."""
        from diagrammer.items.junction_item import JunctionItem
        self._drag_start_junction_pos = {}
        if not self._waypoints:
            return
        src_comp = self._source_port.component if self._source_port else None
        if isinstance(src_comp, JunctionItem):
            self._drag_start_junction_pos[src_comp.instance_id] = QPointF(src_comp.pos())
        tgt_comp = self._target_port.component if self._target_port else None
        if isinstance(tgt_comp, JunctionItem):
            self._drag_start_junction_pos[tgt_comp.instance_id] = QPointF(tgt_comp.pos())

    def _move_endpoint_junctions(self) -> None:
        """Move JunctionItems that sit at the wire endpoints when the adjacent waypoint moves.

        If the first waypoint is being dragged and the source port belongs to a
        JunctionItem, move that junction to follow the waypoint.  Same for the
        last waypoint and the target port.
        """
        from diagrammer.items.junction_item import JunctionItem
        if not self._waypoints:
            return
        selected = self._selected_waypoints if self._dragging_group else {self._dragging_waypoint}
        # First waypoint → source junction
        if 0 in selected:
            comp = self._source_port.component if self._source_port else None
            if isinstance(comp, JunctionItem):
                comp._skip_snap = True
                comp.setPos(self._waypoints[0])
                comp._skip_snap = False
        # Last waypoint → target junction
        last_idx = len(self._waypoints) - 1
        if last_idx in selected:
            comp = self._target_port.component if self._target_port else None
            if isinstance(comp, JunctionItem):
                comp._skip_snap = True
                comp.setPos(self._waypoints[last_idx])
                comp._skip_snap = False

    # =====================================================================
    # Hit-testing helpers
    # =====================================================================

    def _pick_tolerance(self, pixels: float) -> float:
        """Convert a screen-pixel hit tolerance to scene units at the current zoom."""
        scene = self.scene()
        if scene is not None:
            views = scene.views()
            if views and hasattr(views[0], 'pick_tolerance'):
                return views[0].pick_tolerance(pixels)
        return pixels

    def _find_waypoint_at(self, scene_pos: QPointF) -> int | None:
        """Return the index into ``_waypoints`` of the handle near *scene_pos*."""
        tolerance = self._pick_tolerance(VERTEX_HANDLE_SIZE * 2)
        for i, w in enumerate(self._waypoints):
            if point_distance(scene_pos, w) < tolerance:
                return i
        return None

    def _find_segment_at(self, scene_pos: QPointF) -> int | None:
        """Return the segment index in the expanded route nearest to *scene_pos*.

        A segment ``i`` connects ``_expanded[i]`` to ``_expanded[i+1]``
        (wrapping for closed polygons).
        Returns ``None`` if nothing is within ``HIT_TOLERANCE * 2`` screen px.
        """
        pts = self._expanded
        if len(pts) < 2:
            return None

        n = len(pts)
        num_segs = n if self._closed else n - 1
        best_dist = self._pick_tolerance(HIT_TOLERANCE * 2)
        best_idx: int | None = None
        for i in range(num_segs):
            _, dist = closest_point_on_segment(scene_pos, pts[i], pts[(i + 1) % n])
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
        max_seg = n if self._closed else n - 1
        if seg_idx < 0 or seg_idx >= max_seg:
            return pts

        p1 = base_expanded[seg_idx]
        p2 = base_expanded[(seg_idx + 1) % n]
        is_h = self._seg_is_horizontal(p1, p2)

        # Indices of the two endpoints of the dragged segment
        ia = seg_idx
        ib = (seg_idx + 1) % n

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
            if self._closed or ia > 0:
                pi = (ia - 1) % n
                prev = base_expanded[pi]
                prev_orient = segment_orientation(prev, base_expanded[ia])
                if prev_orient == "h":
                    pts[pi] = QPointF(prev.x(), base_expanded[ia].y() + dy)
            # -- next neighbour --
            if self._closed or ib < n - 1:
                ni = (ib + 1) % n
                nxt = base_expanded[ni]
                nxt_orient = segment_orientation(base_expanded[ib], nxt)
                if nxt_orient == "h":
                    pts[ni] = QPointF(nxt.x(), base_expanded[ib].y() + dy)

        elif is_h is False:
            # Vertical segment -- move it horizontally
            dx = delta.x()
            pts[ia] = QPointF(base_expanded[ia].x() + dx, pts[ia].y())
            pts[ib] = QPointF(base_expanded[ib].x() + dx, pts[ib].y())
            if self._closed or ia > 0:
                pi = (ia - 1) % n
                prev = base_expanded[pi]
                prev_orient = segment_orientation(prev, base_expanded[ia])
                if prev_orient == "v":
                    pts[pi] = QPointF(base_expanded[ia].x() + dx, prev.y())
            if self._closed or ib < n - 1:
                ni = (ib + 1) % n
                nxt = base_expanded[ni]
                nxt_orient = segment_orientation(base_expanded[ib], nxt)
                if nxt_orient == "v":
                    pts[ni] = QPointF(base_expanded[ib].x() + dx, nxt.y())

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

        # Closed polygons: all points are waypoints, no port clamping needed.
        if self._closed:
            return pts

        # Clamp: never move the port endpoints (first / last point) —
        # unless the endpoint is a JunctionItem (free anchor), which should
        # follow the segment drag.
        from diagrammer.items.junction_item import JunctionItem
        src_comp = self._source_port.component if self._source_port else None
        tgt_comp = self._target_port.component if self._target_port else None
        if not isinstance(src_comp, JunctionItem):
            pts[0] = QPointF(base_expanded[0])
        else:
            # Collapse duplicate/zero-length points at the route head so
            # the junction follows the drag.  Without this, a duplicate
            # point at index 0 stays static while the dragged segment
            # moves, creating a trailing stub.
            for j in range(n - 1):
                if point_distance(base_expanded[j], base_expanded[j + 1]) < 0.5:
                    pts[j] = QPointF(pts[j + 1])
                else:
                    break
        if not isinstance(tgt_comp, JunctionItem):
            pts[-1] = QPointF(base_expanded[-1])
        else:
            for j in range(n - 1, 0, -1):
                if point_distance(base_expanded[j], base_expanded[j - 1]) < 0.5:
                    pts[j] = QPointF(pts[j - 1])
                else:
                    break

        return pts

    # =====================================================================
    # Mouse interaction
    # =====================================================================

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        # Don't allow individual wire manipulation when in a group
        if self._group_id:
            super().mousePressEvent(event)
            return
        # Direction arrows respond regardless of selection state (they are
        # always visible, unlike waypoint handles).
        if event.button() == Qt.MouseButton.LeftButton and self._arrows:
            mods = event.modifiers()
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            alt = bool(mods & Qt.KeyboardModifier.AltModifier)
            ai = self._find_arrow_at(event.scenePos())
            if ai is not None:
                if ctrl and shift:
                    # Deletion parity with Ctrl+Shift+click on waypoints
                    self._delete_arrow(ai)
                    event.accept()
                    return
                if not (ctrl or shift or alt):
                    # Plain press → begin dragging the arrow along the wire
                    self._dragging_arrow = ai
                    self._drag_start_arrows = self.arrows
                    event.accept()
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
                self._record_endpoint_junction_starts()
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
                self._record_endpoint_junction_starts()
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
                # Record junction endpoint positions for undo.
                from diagrammer.items.junction_item import JunctionItem
                self._drag_start_junction_pos = {}
                src_comp = self._source_port.component if self._source_port else None
                if isinstance(src_comp, JunctionItem):
                    self._drag_start_junction_pos[src_comp.instance_id] = QPointF(src_comp.pos())
                tgt_comp = self._target_port.component if self._target_port else None
                if isinstance(tgt_comp, JunctionItem):
                    self._drag_start_junction_pos[tgt_comp.instance_id] = QPointF(tgt_comp.pos())
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        pos = event.scenePos()

        # --- Direction-arrow drag (constrained to the wire path) ---
        if self._dragging_arrow is not None:
            if 0 <= self._dragging_arrow < len(self._arrows) and len(self._expanded) >= 2:
                t, _proj, _dist = fraction_at_point(self._expanded, pos)
                self._arrows[self._dragging_arrow].t = t
                self.update()
            event.accept()
            return

        # --- Waypoint drag (single or group, snaps to grid) ---
        if self._dragging_waypoint is not None:
            if self._dragging_group and self._selected_waypoints and self._drag_start_pos:
                # Group drag: snap the GRABBED handle to grid (not the
                # mouse), so a wire whose handles sit off-grid gets
                # pulled onto the grid by the drag instead of sliding in
                # rigid grid-sized increments.
                raw_delta = pos - self._drag_start_pos
                anchor_idx = self._dragging_waypoint
                if (self._drag_start_waypoints
                        and anchor_idx is not None
                        and 0 <= anchor_idx < len(self._drag_start_waypoints)):
                    anchor_orig = self._drag_start_waypoints[anchor_idx]
                    anchor_new = self._snap_to_grid(
                        QPointF(anchor_orig.x() + raw_delta.x(),
                                anchor_orig.y() + raw_delta.y())
                    )
                    delta = anchor_new - anchor_orig
                else:
                    delta = self._snap_to_grid(pos) - self._snap_to_grid(self._drag_start_pos)
                if self._drag_start_waypoints:
                    for si in self._selected_waypoints:
                        if 0 <= si < len(self._waypoints) and si < len(self._drag_start_waypoints):
                            orig = self._drag_start_waypoints[si]
                            self._set_waypoint_at(
                                si, QPointF(orig.x() + delta.x(), orig.y() + delta.y()),
                            )
                    # Move adjacent junctions when endpoint waypoints are dragged
                    self._move_endpoint_junctions()
                self.update_route()
            else:
                # Single waypoint drag
                wi = self._dragging_waypoint
                if 0 <= wi < len(self._waypoints):
                    self._set_waypoint_at(wi, self._snap_to_grid(pos))
                    # Move adjacent junction when dragging the first or last waypoint
                    self._move_endpoint_junctions()
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
            # Sync junction endpoints to their corresponding positions
            # in the new expanded route.  _apply_segment_drag now
            # collapses duplicate head/tail points, so [0] and [-1] are
            # the correct junction positions.  Must happen BEFORE
            # _store_expanded_as_waypoints so that update_route sees the
            # new port positions.
            from diagrammer.items.junction_item import JunctionItem
            src_comp = self._source_port.component if self._source_port else None
            tgt_comp = self._target_port.component if self._target_port else None
            junctions_moved = False
            if isinstance(src_comp, JunctionItem) and new_expanded:
                src_comp._skip_snap = True
                src_comp.setPos(new_expanded[0])
                src_comp._skip_snap = False
                junctions_moved = True
            if isinstance(tgt_comp, JunctionItem) and new_expanded:
                tgt_comp._skip_snap = True
                tgt_comp.setPos(new_expanded[-1])
                tgt_comp._skip_snap = False
                junctions_moved = True
            self._store_expanded_as_waypoints(new_expanded)
            # Update other connections attached to moved junctions
            if junctions_moved:
                scene = self.scene()
                if scene:
                    for item in scene.items():
                        if isinstance(item, ConnectionItem) and item is not self:
                            item.update_route()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def hoverEnterEvent(self, event) -> None:  # noqa: ANN001
        if not self._group_id:
            self._hovered = True
            self.update()
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event) -> None:  # noqa: ANN001
        """Highlight the segment under the cursor and show what a drag would do."""
        if self._group_id:
            super().hoverMoveEvent(event)
            return
        pos = event.scenePos()
        seg: int | None = None
        cursor = None
        if self._arrows and self._find_arrow_at(pos) is not None:
            cursor = Qt.CursorShape.PointingHandCursor
        elif self.isSelected():
            # Segment drag / waypoint drag only work on a selected wire —
            # advertise them with directional cursors.
            if self._find_waypoint_at(pos) is not None:
                cursor = Qt.CursorShape.SizeAllCursor
            else:
                seg = self._find_segment_at(pos)
                if seg is not None:
                    pts = self._expanded
                    is_h = self._seg_is_horizontal(
                        pts[seg], pts[(seg + 1) % len(pts)]
                    )
                    if is_h is True:
                        cursor = Qt.CursorShape.SizeVerCursor
                    elif is_h is False:
                        cursor = Qt.CursorShape.SizeHorCursor
                    else:
                        cursor = Qt.CursorShape.SizeAllCursor
        if cursor is not None:
            self.setCursor(cursor)
        else:
            self.unsetCursor()
        if seg != self._hover_segment:
            self._hover_segment = seg
            self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: ANN001
        if self._hovered or self._hover_segment is not None:
            self._hovered = False
            self._hover_segment = None
            self.update()
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._dragging_arrow is not None:
            old = self._drag_start_arrows
            self._dragging_arrow = None
            self._drag_start_arrows = None
            new = self.arrows
            if old is not None and new != old:
                # State is already live; record the change for undo/redo.
                self._push_arrows_change(old, new)
            event.accept()
            return
        if self._dragging_waypoint is not None or self._dragging_segment is not None:
            # Push undo command for the waypoint change (and any junction moves)
            if self._drag_start_waypoints is not None:
                new_waypoints = [QPointF(w) for w in self._waypoints]
                if new_waypoints != self._drag_start_waypoints:
                    from diagrammer.commands.connect_command import EditWaypointsCommand
                    scene = self.scene()
                    if scene and hasattr(scene, 'undo_stack'):
                        # Bundle waypoint edit + junction moves in a macro
                        has_junction_moves = bool(self._drag_start_junction_pos)
                        if has_junction_moves:
                            scene.undo_stack.beginMacro("Move waypoints")
                        cmd = EditWaypointsCommand(
                            self, self._drag_start_waypoints, new_waypoints
                        )
                        scene.undo_stack.push(cmd)
                        # Record junction position changes
                        if has_junction_moves:
                            from diagrammer.items.junction_item import JunctionItem
                            from diagrammer.commands.add_command import MoveComponentCommand
                            for port in (self._source_port, self._target_port):
                                if port is None:
                                    continue
                                comp = port.component
                                if not isinstance(comp, JunctionItem):
                                    continue
                                old_pos = self._drag_start_junction_pos.get(comp.instance_id)
                                if old_pos is not None and old_pos != comp.pos():
                                    jcmd = MoveComponentCommand(comp, old_pos, QPointF(comp.pos()))
                                    scene.undo_stack.push(jcmd)
                            scene.undo_stack.endMacro()
            self._dragging_waypoint = None
            self._dragging_group = False
            self._dragging_segment = None
            self._drag_start_pos = None
            self._drag_start_expanded = None
            self._drag_start_waypoints = None
            self._drag_start_junction_pos = {}
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
        """Remove the waypoint at the given index and rebuild the route (undoable).

        When the deleted waypoint is adjacent to a free-end JunctionItem,
        collapse the junction onto the next remaining waypoint (or the
        opposite port) so the segment doesn't become orphaned. The edit
        (and any junction move) is pushed onto the undo stack using the
        same macro pattern as a waypoint drag, so Ctrl+Z restores it.
        """
        from diagrammer.items.junction_item import JunctionItem

        if not (0 <= index < len(self._waypoints)):
            return

        old_wps = [QPointF(w) for w in self._waypoints]
        new_wps = [QPointF(w) for i, w in enumerate(old_wps) if i != index]
        n = len(old_wps)

        # An adjacent free-end junction must follow the delete so its
        # segment isn't left dangling.
        junction = None
        junction_new_pos = None
        if index == 0 and self._source_port:
            comp = self._source_port.component
            if isinstance(comp, JunctionItem):
                junction = comp
                junction_new_pos = (QPointF(old_wps[1]) if n > 1
                                    else self._target_port.scene_center())
        if index == n - 1 and self._target_port:
            comp = self._target_port.component
            if isinstance(comp, JunctionItem):
                junction = comp
                junction_new_pos = (QPointF(old_wps[n - 2]) if n > 1
                                    else self._source_port.scene_center())

        scene = self.scene()
        undo_stack = getattr(scene, 'undo_stack', None) if scene else None

        def _apply_junction_move():
            if junction is not None:
                junction._skip_snap = True
                junction.setPos(junction_new_pos)
                junction._skip_snap = False

        if undo_stack is None:
            # No undo stack (e.g. detached item) — mutate directly.
            _apply_junction_move()
            self._set_waypoints_from_scene(new_wps)
            self.update_route()
            return

        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand

        junction_old_pos = QPointF(junction.pos()) if junction is not None else None
        has_junction_move = (junction is not None
                             and junction_new_pos != junction_old_pos)

        # Apply the changes live, then record them. Same push order as the
        # waypoint-drag release (EditWaypoints first, then the junction
        # move) so undo/redo restore in the correct sequence.
        _apply_junction_move()
        self._set_waypoints_from_scene(new_wps)
        self.update_route()

        if has_junction_move:
            undo_stack.beginMacro("Delete waypoint")
        undo_stack.push(EditWaypointsCommand(self, old_wps, new_wps))
        if has_junction_move:
            undo_stack.push(
                MoveComponentCommand(junction, junction_old_pos,
                                     QPointF(junction_new_pos)))
            undo_stack.endMacro()

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

        if best_dist < self._pick_tolerance(HIT_TOLERANCE * 3):
            kps = self._key_points()
            kp_exp_indices = self._key_point_expanded_indices(kps)
            insert_wp_idx = self._wp_insert_index_for_expanded_segment(
                best_idx, kp_exp_indices
            )
            self._waypoints.insert(insert_wp_idx, self._snap_to_grid(best_proj))
            self.update_route()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        """Double-click on a connection segment → select all waypoints for group drag."""
        if self._group_id:
            super().mouseDoubleClickEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._arrows:
            ai = self._find_arrow_at(event.scenePos())
            if ai is not None:
                # Cancel the drag the first click of the double-click started
                self._dragging_arrow = None
                self._drag_start_arrows = None
                self._flip_arrow(ai)
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            pos = event.scenePos()
            # Only select-all when double-clicking a segment, not a waypoint
            wi = self._find_waypoint_at(pos)
            if wi is None and self._waypoints:
                # Cancel any drag that the first click may have started
                self._dragging_waypoint = None
                self._dragging_segment = None
                self._dragging_group = False
                # Select all waypoints
                self._selected_waypoints = set(range(len(self._waypoints)))
                self.update()
                event.accept()
                return
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
