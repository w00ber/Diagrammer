"""The wire's own drag goes through ``DragSession`` like every other drag.

Until this change ``ConnectionItem`` ran a second, parallel drag
pipeline for its waypoint and segment gestures: its own start
snapshots, its own ``beginMacro("Move waypoints")``, its own junction
bookkeeping, and its own grid snapping. That is exactly what invariant
I5 in ``docs/wiring-interaction-redesign.md`` exists to prevent, and it
is where the three disagreeing snap rules lived — which is why an
off-grid run could refuse to straighten.

These tests pin the three properties the shared pipeline is supposed to
buy: one undo entry per gesture, exact round-trip on undo, and an
idempotent move step.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.commands.connect_command import CreateConnectionCommand
from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.junction_item import JunctionItem

GRID = 20.0


@pytest.fixture
def view(scene):
    """A view on the scene, so the wire can find the grid and snap state."""
    from diagrammer.canvas.view import DiagramView

    v = DiagramView(scene)
    assert v.grid_spacing == GRID and v._snap_enabled
    yield v
    import shiboken6
    v.setScene(None)
    shiboken6.delete(v)


def _junction(scene, x, y) -> JunctionItem:
    j = JunctionItem()
    j.setPos(QPointF(x, y))
    scene.addItem(j)
    return j


def _connect(scene, a, b) -> ConnectionItem:
    cmd = CreateConnectionCommand(scene, a.port, b.port)
    scene.undo_stack.push(cmd)
    return cmd.connection


def _xy(points) -> list[tuple[float, float]]:
    return [(round(p.x(), 3), round(p.y(), 3)) for p in points]


def _wire_with_bend(scene):
    """A wire from (0,0) to (400,200) bending at (200,0) then (200,200)."""
    a = _junction(scene, 0, 0)
    b = _junction(scene, 400, 200)
    w = _connect(scene, a, b)
    w.vertices = [QPointF(200, 0), QPointF(200, 200)]
    w.update_route()
    return a, b, w


class TestOneUndoEntryPerGesture:
    def test_waypoint_drag_is_a_single_undo_entry(self, scene, view):
        _a, _b, w = _wire_with_bend(scene)
        before = _xy(w.vertices)
        depth = scene.undo_stack.count()

        w.begin_waypoint_drag(0, QPointF(200, 0))
        w._advance_wire_drag(QPointF(260, 0))
        assert w._end_wire_drag() is True

        assert scene.undo_stack.count() == depth + 1
        assert _xy(w.vertices) != before
        scene.undo_stack.undo()
        assert _xy(w.vertices) == before

    def test_segment_drag_is_a_single_undo_entry(self, scene, view):
        _a, _b, w = _wire_with_bend(scene)
        before = _xy(w.vertices)
        depth = scene.undo_stack.count()

        seg = w._find_segment_at(QPointF(200, 100))
        assert seg is not None
        w.begin_segment_drag(seg, QPointF(200, 100))
        w._advance_wire_drag(QPointF(260, 100))
        assert w._end_wire_drag() is True

        assert scene.undo_stack.count() == depth + 1
        assert _xy(w.vertices) != before
        scene.undo_stack.undo()
        assert _xy(w.vertices) == before

    def test_a_gesture_that_moved_nothing_pushes_nothing(self, scene, view):
        """Qt keeps empty macros and ``beginMacro`` wipes the redo stack,
        so a click that only grabbed a handle must not reach the stack."""
        _a, _b, w = _wire_with_bend(scene)
        depth = scene.undo_stack.count()

        w.begin_waypoint_drag(0, QPointF(200, 0))
        assert w._end_wire_drag() is False
        assert scene.undo_stack.count() == depth

    def test_redo_history_survives_a_no_op_gesture(self, scene, view):
        _a, _b, w = _wire_with_bend(scene)
        w.begin_waypoint_drag(0, QPointF(200, 0))
        w._advance_wire_drag(QPointF(260, 0))
        w._end_wire_drag()
        scene.undo_stack.undo()
        assert scene.undo_stack.canRedo()

        w.begin_waypoint_drag(0, QPointF(200, 0))
        w._end_wire_drag()
        assert scene.undo_stack.canRedo()


class TestJunctionEndpointsRideAlong:
    """A segment drag carries a free end. The session snapshots it, so the
    junction move and the waypoint edit undo together — this used to be a
    hand-assembled macro inside ``mouseReleaseEvent``."""

    def test_free_end_follows_the_route_and_undoes_with_it(self, scene, view):
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a, b)
        w.update_route()
        a_before, b_before = QPointF(a.pos()), QPointF(b.pos())
        depth = scene.undo_stack.count()

        seg = w._find_segment_at(QPointF(200, 0))
        assert seg is not None
        w.begin_segment_drag(seg, QPointF(200, 0))
        w._advance_wire_drag(QPointF(200, 60))
        assert w._end_wire_drag() is True

        # A horizontal run dragged downward takes both free ends with it.
        assert a.pos().y() > a_before.y()
        assert b.pos().y() > b_before.y()
        assert scene.undo_stack.count() == depth + 1

        scene.undo_stack.undo()
        assert _xy([a.pos(), b.pos()]) == _xy([a_before, b_before])


class TestIdempotentMoveStep:
    """``move_to`` applies the delta against the snapshot, never against
    the running shape, so replaying a delta is a no-op."""

    def test_the_same_delta_twice_is_the_same_result(self, scene, view):
        _a, _b, w = _wire_with_bend(scene)
        seg = w._find_segment_at(QPointF(200, 100))
        w.begin_segment_drag(seg, QPointF(200, 100))

        w._advance_wire_drag(QPointF(260, 100))
        once = _xy(w.vertices)
        w._advance_wire_drag(QPointF(260, 100))
        assert _xy(w.vertices) == once
        w._end_wire_drag()

    def test_dragging_back_to_the_start_restores_the_shape(self, scene, view):
        _a, _b, w = _wire_with_bend(scene)
        before = _xy(w.vertices)
        w.begin_waypoint_drag(0, QPointF(200, 0))
        w._advance_wire_drag(QPointF(300, 80))
        w._advance_wire_drag(QPointF(200, 0))
        assert _xy(w.vertices) == before
        assert w._end_wire_drag() is False


class TestOneSnapRule:
    """Single-waypoint, group-waypoint and segment drags all anchor the
    snap on the thing the user grabbed, so an off-grid wire is pulled
    onto the grid instead of sliding in rigid grid-sized steps."""

    def test_a_single_off_grid_waypoint_lands_on_the_grid(self, scene, view):
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a, b)
        w.vertices = [QPointF(207, 33), QPointF(207, 0)]
        w.update_route()

        w.begin_waypoint_drag(0, QPointF(207, 33))
        w._advance_wire_drag(QPointF(211, 37))   # a 4 pt nudge
        w._end_wire_drag()

        x, y = w.vertices[0].x(), w.vertices[0].y()
        assert x % GRID == 0 and y % GRID == 0

    def test_a_grabbed_handle_keeps_its_offset_from_the_cursor(self, scene, view):
        """The waypoint moves by the delta that lands the *handle* on the
        grid, rather than teleporting to the snapped cursor.

        The handle is at x=207 and the press lands 7 pt to its right, so
        the two rules disagree: anchoring on the handle gives
        ``snap(207+40) = 240``, while snapping the cursor — what the
        single-waypoint branch used to do — gives ``snap(254) = 260``
        and throws the grab offset away.
        """
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a, b)
        w.vertices = [QPointF(207, 33), QPointF(207, 0)]
        w.update_route()

        w.begin_waypoint_drag(0, QPointF(214, 40))
        w._advance_wire_drag(QPointF(254, 40))
        w._end_wire_drag()
        assert _xy([w.vertices[0]])[0] == (240.0, 40.0)

    def test_an_off_grid_segment_straightens_onto_the_grid(self, scene, view):
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a, b)
        w.vertices = [QPointF(100, 13), QPointF(300, 13)]
        w.update_route()

        seg = w._find_segment_at(QPointF(200, 13))
        assert seg is not None
        w.begin_segment_drag(seg, QPointF(200, 13))
        w._advance_wire_drag(QPointF(204, 17))   # a 4 pt nudge
        w._end_wire_drag()

        ys = {round(p.y(), 3) for p in w.vertices}
        assert ys and all(y % GRID == 0 for y in ys)

    def test_snapping_off_applies_the_raw_delta(self, scene, view):
        view._snap_enabled = False
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a, b)
        w.vertices = [QPointF(207, 33), QPointF(207, 0)]
        w.update_route()

        w.begin_waypoint_drag(0, QPointF(207, 33))
        w._advance_wire_drag(QPointF(210, 36))
        w._end_wire_drag()
        assert _xy([w.vertices[0]])[0] == (210.0, 36.0)


class TestSegmentCount:
    """One place knows a closed polygon has a closing segment."""

    def test_an_open_wire_has_one_fewer_segment_than_points(self, scene):
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a, b)
        w.vertices = [QPointF(200, 0)]
        w.update_route()
        assert w.segment_count() == len(w.all_points()) - 1

    def test_a_closed_polygon_counts_the_closing_segment(self, scene):
        a = _junction(scene, 0, 0)
        w = _connect(scene, a, a)
        w.closed = True
        w.vertices = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100),
                      QPointF(0, 100)]
        w.update_route()
        pts = w.all_points()
        assert w.segment_count() == len(pts)
        # The closing segment is hit-testable, which is what a
        # re-derived ``n - 1`` silently dropped.
        first, last = w.segment_endpoints(0), w.segment_endpoints(len(pts) - 1)
        assert last[1] == first[0]

    def test_a_degenerate_route_has_no_segments(self, scene):
        a = _junction(scene, 0, 0)
        b = _junction(scene, 0, 0)
        w = _connect(scene, a, b)
        assert w.segment_count([QPointF(0, 0)]) == 0
        assert w.segment_count([]) == 0

    def test_a_snapshot_overrides_the_live_route(self, scene):
        a = _junction(scene, 0, 0)
        b = _junction(scene, 400, 0)
        w = _connect(scene, a, b)
        w.update_route()
        snapshot = [QPointF(0, 0), QPointF(100, 0), QPointF(200, 0)]
        assert w.segment_count(snapshot) == 2
        assert w.segment_endpoints(1, snapshot) == (snapshot[1], snapshot[2])


class TestThroughTheRealHandlers:
    """The unit tests above call ``begin_*_drag`` directly. These drive
    the actual press/move/release handlers, so the hit-testing and the
    modifier precedence in ``mousePressEvent`` are covered too."""

    @staticmethod
    def _press(conn, pos, modifiers=None):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        ev = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMousePress)
        ev.setScenePos(pos)
        ev.setButton(Qt.MouseButton.LeftButton)
        ev.setModifiers(modifiers or Qt.KeyboardModifier.NoModifier)
        conn.mousePressEvent(ev)

    @staticmethod
    def _move(conn, pos):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        ev = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMouseMove)
        ev.setScenePos(pos)
        ev.setModifiers(Qt.KeyboardModifier.NoModifier)
        conn.mouseMoveEvent(ev)

    @staticmethod
    def _release(conn, pos):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        ev = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMouseRelease)
        ev.setScenePos(pos)
        ev.setButton(Qt.MouseButton.LeftButton)
        ev.setModifiers(Qt.KeyboardModifier.NoModifier)
        conn.mouseReleaseEvent(ev)

    def test_press_drag_release_on_a_handle(self, scene, view):
        _a, _b, w = _wire_with_bend(scene)
        w.setSelected(True)
        before = _xy(w.vertices)
        depth = scene.undo_stack.count()

        self._press(w, QPointF(200, 0))
        assert w._drag is not None, "the press started a session"
        self._move(w, QPointF(260, 0))
        self._release(w, QPointF(260, 0))

        assert w._drag is None
        assert scene.undo_stack.count() == depth + 1
        scene.undo_stack.undo()
        assert _xy(w.vertices) == before

    def test_press_drag_release_on_a_segment(self, scene, view):
        _a, _b, w = _wire_with_bend(scene)
        w.setSelected(True)
        before = _xy(w.vertices)
        depth = scene.undo_stack.count()

        self._press(w, QPointF(200, 100))
        assert w._drag is not None
        self._move(w, QPointF(260, 100))
        self._release(w, QPointF(260, 100))

        assert scene.undo_stack.count() == depth + 1
        assert _xy(w.vertices) != before
        scene.undo_stack.undo()
        assert _xy(w.vertices) == before

    def test_a_press_on_an_unselected_wire_starts_no_drag(self, scene, view):
        """Only a selected wire exposes handles; the first click selects."""
        _a, _b, w = _wire_with_bend(scene)
        w.setSelected(False)
        self._press(w, QPointF(200, 0))
        assert w._drag is None

    def test_ctrl_shift_click_deletes_instead_of_dragging(self, scene, view):
        from PySide6.QtCore import Qt

        _a, _b, w = _wire_with_bend(scene)
        w.setSelected(True)
        n = len(w.vertices)
        self._press(w, QPointF(200, 0),
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.ShiftModifier)
        assert w._drag is None
        assert len(w.vertices) == n - 1

    def test_double_click_cancels_the_drag_the_first_click_started(self, scene, view):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        _a, _b, w = _wire_with_bend(scene)
        w.setSelected(True)
        self._press(w, QPointF(200, 100))
        assert w._drag is not None

        ev = QGraphicsSceneMouseEvent(QEvent.Type.GraphicsSceneMouseDoubleClick)
        ev.setScenePos(QPointF(200, 100))
        ev.setButton(Qt.MouseButton.LeftButton)
        ev.setModifiers(Qt.KeyboardModifier.NoModifier)
        w.mouseDoubleClickEvent(ev)

        assert w._drag is None
        assert w._selected_waypoints == set(range(len(w.vertices)))
