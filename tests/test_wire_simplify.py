"""Regression tests for wire waypoint healing.

User-reported symptoms, all three the same underlying defect:

1. Dragging a rubber-band selection "leaves vertices behind" — the wire
   shears into a staircase instead of following the move.
2. Rerouting a wire mints extra vertices that are never removed when the
   corner they anchored straightens out, so wiring gets progressively
   more convoluted the more it is edited.
3. Grid snapping appears to stop working on such a wire — a segment
   won't straighten because a stranded waypoint pins part of the run.

Root cause: ``_adopt_route`` wrote the *whole* expanded
route back as explicit waypoints after every segment drag, with no
pruning pass. Those strays split one draggable run into several, pin
points the snap cannot move, and — because waypoints bind to whichever
endpoint port is nearer — teleport in half-sets when one end of the wire
moves. :meth:`ConnectionItem.simplify_waypoints` removes any waypoint
that contributes no bend; the view holds boundary-crossing wires'
waypoints in scene space for the duration of a drag.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF

from diagrammer.commands.connect_command import CreateConnectionCommand
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.junction_item import JunctionItem


def _junction(scene, x, y) -> JunctionItem:
    j = JunctionItem()
    j.setPos(QPointF(x, y))
    scene.addItem(j)
    return j


def _connect(scene, port_a, port_b) -> ConnectionItem:
    cmd = CreateConnectionCommand(scene, port_a, port_b)
    scene.undo_stack.push(cmd)
    return cmd.connection


def _xy(points) -> list[tuple[float, float]]:
    return [(round(p.x(), 3), round(p.y(), 3)) for p in points]


def _segment_drag(conn: ConnectionItem, seg_idx: int, dx: float, dy: float) -> None:
    """Replay what ``mouseMoveEvent`` does for a segment drag."""
    base = [QPointF(p) for p in conn.all_points()]
    new = conn._apply_segment_drag(seg_idx, QPointF(dx, dy), base)
    for comp, pt in ((conn.source_port.component, new[0]),
                     (conn.target_port.component, new[-1])):
        if isinstance(comp, JunctionItem):
            comp.setPos(pt)
    conn._adopt_route(new)


class TestSimplifyWaypoints:
    def test_collinear_waypoints_are_pruned(self, scene):
        """Waypoints sitting mid-run add no bend, so they must go."""
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(0, 0), QPointF(150, 0), QPointF(250, 0),
                      QPointF(400, 0)]

        assert w.simplify_waypoints() is True
        # The two strays go, and so do the endpoint-coincident pair: a
        # junction is its own item and its own grab handle.
        assert _xy(w.vertices) == []

    def test_real_bends_survive(self, scene):
        a = _junction(scene, 0, 0)
        b = _junction(scene, 600, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(200, -100), QPointF(400, -100)]

        assert w.simplify_waypoints() is False
        assert len(w.vertices) == 2

    def test_spur_that_doubles_back_is_pruned(self, scene):
        """A -> B -> A is a stub, not a bend (the image-3 symptom)."""
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(200, 0), QPointF(200, 60), QPointF(200, 0)]

        assert w.simplify_waypoints() is True
        assert _xy(w.vertices) == []

    def test_legacy_junction_handles_are_pruned(self, scene):
        """Older files carried a waypoint on every junction endpoint as
        the free end's grab handle. The junction is the handle now."""
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(0, 0), QPointF(400, 0)]

        assert w.simplify_waypoints() is True
        assert _xy(w.vertices) == []

    def test_port_coincident_waypoint_is_pruned(self, scene, library):
        """A waypoint on a real port is an approach artifact, not a bend."""
        cdef = library.get("CRYO_WIRING_6pt/passive/RESISTOR")
        assert cdef is not None
        comp = ComponentItem(cdef)
        comp.setPos(QPointF(0, 0))
        scene.addItem(comp)
        jct = _junction(scene, 400, 20)
        w = _connect(scene, comp.port_by_name("right"), jct.port)

        port = comp.port_by_name("right").scene_center()
        w.vertices = [QPointF(port), QPointF(400, 20)]
        w.simplify_waypoints()
        assert _xy(w.vertices) == []

    def test_closed_polygon_keeps_at_least_three_vertices(self, scene):
        a = _junction(scene, 0, 0)
        w = _connect(scene, a.port, a.port)
        w.closed = True
        w.vertices = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100),
                      QPointF(0, 100)]
        w.update_route()

        assert w.simplify_waypoints() is False
        assert len(w.vertices) == 4


class TestSegmentDragDoesNotAccumulate:
    def test_repeated_drags_do_not_grow_the_waypoint_list(self, scene):
        """Nudging a segment back and forth must not mint a vertex a nudge."""
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 200)
        w = _connect(scene, a.port, b.port)

        _segment_drag(w, 1, 40, 0)
        after_first = len(w.vertices)
        for _ in range(8):
            _segment_drag(w, 1, 40, 0)
            _segment_drag(w, 1, -40, 0)

        assert len(w.vertices) == after_first
        # And the route is back where it started, with no leftover bends.
        assert _xy(w.all_points()) == [(0.0, 0.0), (240.0, 0.0),
                                       (240.0, 200.0), (400.0, 200.0)]

    def test_straightened_corner_leaves_no_waypoint(self, scene):
        """Drag a bend flat and the waypoint that held it must disappear."""
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(0, 0), QPointF(200, -80), QPointF(400, 0)]
        w.update_route()
        bent = len(w.vertices)

        # Push the raised run back down onto the straight line.
        seg = next(i for i, p in enumerate(w.all_points()) if p.y() == -80)
        _segment_drag(w, seg, 0, 80)

        assert len(w.vertices) < bent
        assert all(abs(p.y()) < 0.5 for p in w.all_points())


class TestExpandedRouteHasNoZeroLengthSegments:
    def test_junction_endpoints_do_not_duplicate_route_points(self, scene):
        """Duplicate points shift every segment index hit testing uses."""
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(0, 0), QPointF(400, 0)]
        w.update_route()

        pts = w.all_points()
        assert len(pts) >= 2
        for p, q in zip(pts, pts[1:]):
            assert (p.x(), p.y()) != (q.x(), q.y())


class TestBoundaryWireDoesNotShear:
    def test_moving_one_endpoint_leaves_the_far_bends_alone(self, scene):
        """The reported "vertices left behind" staircase.

        Waypoints used to bind to the nearer endpoint port, so moving one
        end carried the near half of the bends along. They are scene
        coordinates now: an endpoint move touches nothing but the last
        leg, which the router re-draws.
        """
        a = _junction(scene, 0, 0)
        b = _junction(scene, 600, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(200, -100), QPointF(400, -100)]

        a.setPos(QPointF(0, 300))
        w.update_route()

        assert _xy(w.vertices) == [(200.0, -100.0), (400.0, -100.0)]
        assert w.routed_points()[0] == QPointF(0, 300)


class TestHeldWaypointsSurvive:
    def test_freshly_inserted_waypoint_is_not_pruned(self, scene):
        """Ctrl+click drops a handle *on* the line — it must stay there.

        A waypoint inserted onto a segment is collinear by construction,
        so a pruning pass that ignored selection would delete it before
        the user could drag it anywhere.
        """
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(0, 0), QPointF(400, 0)]
        w.update_route()

        w._insert_waypoint_at(QPointF(200, 0))
        assert (200.0, 0.0) in _xy(w.vertices)

        w.simplify_waypoints()
        assert (200.0, 0.0) in _xy(w.vertices)

    def test_deselecting_lets_the_stray_go(self, scene):
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(0, 0), QPointF(400, 0)]
        w.update_route()
        w._insert_waypoint_at(QPointF(200, 0))

        w._selected_waypoints.clear()
        assert w.simplify_waypoints() is True
        assert (200.0, 0.0) not in _xy(w.vertices)

    def test_prune_renumbers_the_selection(self, scene):
        """Stale indices would move the wrong handle on the next drag."""
        a = _junction(scene, 0, 0)
        b = _junction(scene, 600, 0)
        w = _connect(scene, a.port, b.port)
        # index:        0               1                 2
        w.vertices = [QPointF(200, 0), QPointF(400, 0), QPointF(400, -80),
                      QPointF(500, -80)]
        #             3
        w.update_route()
        w._selected_waypoints = {3}

        assert w.simplify_waypoints() is True  # index 0 is collinear
        assert _xy(w.vertices) == [(400.0, 0.0), (400.0, -80.0), (500.0, -80.0)]
        assert w._selected_waypoints == {2}


class TestWaypointInsertionSlot:
    def test_insert_lands_in_route_order(self, scene):
        """A Ctrl+click insertion must not jump ahead of an existing handle.

        ``_key_point_expanded_indices`` used to *predict* where each key
        point lands in the expanded route by re-adding segment lengths.
        The real route also carries lead-approach stubs and has
        coincident points collapsed, so the prediction drifted and the
        new waypoint was spliced in at the wrong index — the wire then
        doubled back on itself.
        """
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a.port, b.port)
        w.vertices = [QPointF(0, 0), QPointF(400, 0)]
        w.update_route()

        w._insert_waypoint_at(QPointF(200, 0))

        assert _xy(w.vertices) == [(0.0, 0.0), (200.0, 0.0), (400.0, 0.0)]
        # The route stays monotonic: no doubling back.
        xs = [p.x() for p in w.all_points()]
        assert xs == sorted(xs)


class TestRubberBandSelectsJunctions:
    """A junction is an item: a band that covers it selects it, and the
    wire passing through keeps only the bends the band covers."""

    def test_boxed_junction_is_selected_and_its_wire_keeps_real_bends(self, scene):
        from diagrammer.canvas.view import DiagramView
        from PySide6.QtCore import QEvent, QRectF, Qt
        from PySide6.QtGui import QMouseEvent

        view = DiagramView(scene)
        view.resize(800, 600)
        left = _junction(scene, 100, 100)
        right = _junction(scene, 700, 100)
        rail = _connect(scene, left.port, right.port)
        rail.vertices = [QPointF(300, 40)]
        rail.update_route()

        band = QRectF(50, 20, 300, 200)
        for it in scene.items(band):
            it.setSelected(True)
        view._rubber_band_rect = band
        view.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(view.mapFromScene(QPointF(350, 220))),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ))

        assert left.isSelected(), "the band covers the junction"
        assert not right.isSelected()
        assert view._selected_waypoint_set.get(rail.instance_id) == {0}
        view.setScene(None)

    def test_free_end_is_picked_through_its_wire(self, scene):
        """Clicking where a wire ends must hit the junction, not the wire.

        Wires stack just above junctions and have a wide hit halo; by
        z-order alone the free end's own wire would always win the
        click and the end could never be grabbed.
        """
        from diagrammer.canvas.view import DiagramView

        view = DiagramView(scene)
        view.resize(800, 600)
        end = _junction(scene, 300, 100)
        _connect(scene, _junction(scene, 0, 100).port, end.port)
        scene.update_connections()
        view.centerOn(QPointF(150, 100))

        hit = view._item_at_pos(view.mapFromScene(QPointF(300, 100)))
        # The raw hit may be the junction's own port (a child item);
        # the press handler resolves a port to its parent.
        assert getattr(hit, "component", hit) is end
        # Mid-wire, the wire still wins.
        mid = view._item_at_pos(view.mapFromScene(QPointF(150, 100)))
        assert isinstance(mid, ConnectionItem)
        view.setScene(None)


class TestSegmentDragStoresOnlyIntent:
    def test_only_corners_the_router_would_not_place_survive(self, scene):
        """Drag the vertical leg of an L sideways: the route needs two
        bends the router can't derive, so exactly those are stored. Drag
        it back to where the router would put it: nothing is stored."""
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 200)
        w = _connect(scene, a.port, b.port)
        router_route = _xy(w.all_points())
        assert w.vertices == []

        _segment_drag(w, 1, 40, 0)
        assert len(w.vertices) <= 2, _xy(w.vertices)
        # And the drawn route is the dragged one, not re-derived.
        assert (240.0, 0.0) in _xy(w.all_points())

        _segment_drag(w, 1, -40, 0)
        assert w.vertices == [], "router reproduces this shape unaided"
        assert _xy(w.all_points()) == router_route

    def test_route_carries_no_render_stubs(self, scene, library):
        """all_points() runs port to port; approach stubs are paint only."""
        cdef = library.get("CRYO_WIRING_6pt/passive/RESISTOR")
        comp = ComponentItem(cdef)
        comp.setPos(QPointF(0, 0))
        scene.addItem(comp)
        jct = _junction(scene, 400, 120)
        w = _connect(scene, comp.port_by_name("right"), jct.port)
        scene.update_connections()
        pts = w.all_points()
        assert pts[0] == comp.port_by_name("right").scene_center()
        assert pts[-1] == QPointF(400, 120)
        assert w.routed_points() == pts
