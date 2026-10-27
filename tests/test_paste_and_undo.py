"""Regression tests for paste placement and for undo after a move.

Three user-reported defects, all about an edit that only half comes
back:

1. A paste landed a fixed 40 px from the originals, so after scrolling
   somewhere else in a drawing the copy appeared off screen.
2. Undoing a paste left the pasted annotations (and junctions, shapes
   and images) on the canvas — they had been added with a bare
   ``scene.addItem`` instead of through a command.
3. The first undo after a drag produced a wire shooting off to one
   side. ``EditWaypointsCommand`` stored absolute scene coordinates and
   re-bound them to the endpoint ports as they stood when it ran; inside
   a "Move group" macro Qt replays children in reverse push order, so
   the wire was restored while the components were still at their
   dragged positions and came back displaced by the drag delta.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.canvas.scene import DiagramScene
from diagrammer.canvas.view import DiagramView
from diagrammer.clipboard_ops import ClipboardMixin
from diagrammer.commands.connect_command import CreateConnectionCommand
from diagrammer.items.annotation_item import AnnotationItem
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.junction_item import JunctionItem


class _Host(ClipboardMixin):
    """Minimal stand-in for MainWindow: just what the mixin touches."""

    def __init__(self, scene, library, view=None):
        self._scene = scene
        self._view = view
        self._library = library
        self._clipboard: list = []

    def _try_paste_external_svg(self) -> bool:
        return False


def _any_def(library):
    defs = library.all_defs()
    if not defs:
        pytest.skip("component library is empty")
    return defs[0]


def _count(scene, cls) -> int:
    return sum(1 for i in scene.items() if isinstance(i, cls))


@pytest.fixture
def host(library):
    return _Host(DiagramScene(library=library), library)


class TestPasteIsUndoable:
    def test_one_undo_removes_everything_a_paste_added(self, host, library):
        scene = host._scene
        comp = ComponentItem(_any_def(library))
        comp.setPos(QPointF(0, 0))
        scene.addItem(comp)
        jct = JunctionItem()
        jct.setPos(QPointF(300, 20))
        scene.addItem(jct)
        annot = AnnotationItem("$Z_0$")
        annot.setPos(QPointF(50, -60))
        scene.addItem(annot)
        wire = CreateConnectionCommand(scene, comp.ports[-1], jct.port)
        scene.undo_stack.push(wire)
        for item in (comp, jct, annot, wire.connection):
            item.setSelected(True)

        host._copy()
        host._paste()
        assert _count(scene, AnnotationItem) == 2
        assert _count(scene, JunctionItem) == 2

        scene.undo_stack.undo()

        assert _count(scene, ComponentItem) == 1
        assert _count(scene, AnnotationItem) == 1, "pasted annotation survived undo"
        assert _count(scene, JunctionItem) == 1, "pasted junction survived undo"

    def test_redo_puts_them_back(self, host, library):
        scene = host._scene
        annot = AnnotationItem("label")
        annot.setPos(QPointF(10, 10))
        scene.addItem(annot)
        annot.setSelected(True)

        host._copy()
        host._paste()
        scene.undo_stack.undo()
        scene.undo_stack.redo()

        assert _count(scene, AnnotationItem) == 2


class TestPastePlacement:
    def test_paste_centres_on_the_visible_canvas(self, library):
        scene = DiagramScene(library=library)
        view = DiagramView(scene)
        view.resize(800, 600)
        host = _Host(scene, library, view)

        comp = ComponentItem(_any_def(library))
        comp.setPos(QPointF(0, 0))
        scene.addItem(comp)
        comp.setSelected(True)
        host._copy()

        # Scroll somewhere far away, then paste.
        view.centerOn(QPointF(2000, 1500))
        target = view.mapToScene(view.viewport().rect().center())
        host._paste()

        pasted = [c for c in scene.items()
                  if isinstance(c, ComponentItem) and c is not comp]
        assert len(pasted) == 1
        # Within one grid step of the view centre, and nowhere near the
        # original.
        assert abs(pasted[0].pos().x() - target.x()) <= view.grid_spacing
        assert abs(pasted[0].pos().y() - target.y()) <= view.grid_spacing

    def test_paste_without_a_view_falls_back_to_a_nudge(self, host, library):
        comp = ComponentItem(_any_def(library))
        comp.setPos(QPointF(0, 0))
        host._scene.addItem(comp)
        comp.setSelected(True)
        host._copy()
        host._paste()

        pasted = [c for c in host._scene.items()
                  if isinstance(c, ComponentItem) and c is not comp]
        assert len(pasted) == 1
        assert pasted[0].pos() == QPointF(40, 40)


class TestUndoAfterMoveRestoresWireShape:
    def test_wire_crossing_the_drag_boundary_comes_back_unchanged(self, library):
        """One undo after a group drag must restore the route exactly.

        The failing case put the rail's route back while the dragged
        items were still displaced, so the restored waypoints were
        re-anchored to the moved ports and then carried a second time by
        the move's own undo — leaving a long stub running off past the
        wire's own endpoint.
        """
        scene = DiagramScene(library=library)
        view = DiagramView(scene)
        view.resize(900, 700)
        cdef = library.get("CRYO_WIRING_6pt/passive/RESISTOR")
        assert cdef is not None

        res = ComponentItem(cdef)
        res.setPos(QPointF(0, 100))
        scene.addItem(res)
        j_left = JunctionItem()
        j_left.setPos(QPointF(0, 0))
        scene.addItem(j_left)
        j_right = JunctionItem()
        j_right.setPos(QPointF(600, 0))
        scene.addItem(j_right)

        cmd = CreateConnectionCommand(scene, j_left.port, j_right.port)
        scene.undo_stack.push(cmd)
        rail = cmd.connection
        cmd = CreateConnectionCommand(scene, res.port_by_name("left"), j_left.port)
        scene.undo_stack.push(cmd)
        rail.vertices = [QPointF(0, 0), QPointF(300, -120), QPointF(600, 0)]
        scene.update_connections()
        before = [(round(p.x(), 1), round(p.y(), 1)) for p in rail.all_points()]

        # Prime the drag the way mousePressEvent does for res + j_left,
        # then move and release.
        dragged = [res, j_left]
        j_left._skip_snap = True
        view._dragging_components = dragged
        view._drag_anchor_item = res
        view._drag_anchor_start_pos = QPointF(res.pos())
        view._drag_mouse_start = QPointF(0, 120)
        for item in dragged:
            scene.record_move_start(item.instance_id, item.pos())
        comp_ids = {id(i) for i in dragged}
        view._drag_internal_conns = []
        view._drag_conn_waypoints = {}
        view._trim_external_conn_endpoint_wps(comp_ids, set())
        for item in dragged:
            item._skip_snap = True
            item.setPos(item.pos() + QPointF(200, 0))
            item._skip_snap = False
        view._hold_external_conn_waypoints()
        scene.update_connections()

        view.mouseReleaseEvent(_release(view, QPointF(200, 120)))
        assert rail.all_points()[0].x() == pytest.approx(200.0)

        scene.undo_stack.undo()

        after = [(round(p.x(), 1), round(p.y(), 1)) for p in rail.all_points()]
        assert after == before
        assert min(x for x, _ in after) >= 0.0, "wire runs off past its endpoint"


def _release(view, scene_pos: QPointF):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(view.mapFromScene(scene_pos)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


class TestAnchorSnapshotRoundTrip:
    def test_snapshot_survives_a_port_move(self, library):
        """The property the undo entries rely on."""
        scene = DiagramScene(library=library)
        a = JunctionItem()
        a.setPos(QPointF(0, 0))
        scene.addItem(a)
        b = JunctionItem()
        b.setPos(QPointF(400, 0))
        scene.addItem(b)
        cmd = CreateConnectionCommand(scene, a.port, b.port)
        scene.undo_stack.push(cmd)
        wire: ConnectionItem = cmd.connection
        wire.vertices = [QPointF(0, 0), QPointF(200, -80), QPointF(400, 0)]
        snapshot = wire.waypoint_anchor_snapshot()

        # Move a port, restore the snapshot, move the port back.
        a.setPos(QPointF(0, 250))
        wire.apply_waypoint_anchor_snapshot(snapshot)
        a.setPos(QPointF(0, 0))
        wire.update_route()

        assert [(round(w.x(), 3), round(w.y(), 3)) for w in wire.vertices] == [
            (0.0, 0.0), (200.0, -80.0), (400.0, 0.0),
        ]
