"""Regression tests for interaction-UX fixes.

Covers:
- No-op clicks must not touch the undo stack (empty-macro / redo-wipe bug).
- Port auto-join fires after a real move.
- Deleting items cascades to attached wires and stranded junctions.
- Junctions ride the undo stack and are cleaned up on cancel.
- T-junctions split the host wire into real topology.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt


def _make_view(scene):
    """Create an offscreen DiagramView for the scene."""
    from diagrammer.canvas.view import DiagramView

    view = DiagramView(scene)
    view.resize(400, 400)
    return view


def _release_event(view, scene_pos: QPointF):
    """Synthesize a left-button mouse release at a scene position."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    vp = view.mapFromScene(scene_pos)
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(vp),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _add_junction(scene, x, y, visible=True):
    from diagrammer.items.junction_item import JunctionItem

    j = JunctionItem()
    j.setPos(QPointF(x, y))
    j.setVisible(visible)
    scene.addItem(j)
    return j


def _connect(scene, port_a, port_b):
    from diagrammer.commands.connect_command import CreateConnectionCommand

    cmd = CreateConnectionCommand(scene, port_a, port_b)
    scene.undo_stack.push(cmd)
    return cmd.connection


class TestNoOpClickUndoSafety:
    def test_plain_click_release_keeps_redo_history(self, scene):
        """A click on an item with zero movement must not wipe redo."""
        from diagrammer.items.shape_item import RectangleItem
        from diagrammer.commands.shape_command import AddShapeCommand

        rect = RectangleItem(width=100, height=60)
        cmd = AddShapeCommand(scene, rect, QPointF(40, 40))
        scene.undo_stack.push(cmd)
        rect2 = RectangleItem(width=50, height=50)
        cmd2 = AddShapeCommand(scene, rect2, QPointF(200, 200))
        scene.undo_stack.push(cmd2)

        scene.undo_stack.undo()  # make redo available
        assert scene.undo_stack.canRedo()
        count_before = scene.undo_stack.count()

        view = _make_view(scene)
        # Prime the drag state exactly as mousePressEvent does for a
        # click on a movable item, then release without any movement.
        view._dragging_components = [rect]
        view._drag_anchor_item = rect
        view._drag_anchor_start_pos = QPointF(rect.pos())
        view._drag_mouse_start = QPointF(rect.pos())
        scene.record_move_start(rect.instance_id, rect.pos())

        view.mouseReleaseEvent(_release_event(view, rect.pos()))

        assert scene.undo_stack.canRedo(), "no-op click wiped redo history"
        assert scene.undo_stack.count() == count_before, (
            "no-op click added an undo entry"
        )
        # Drag state must still be cleared
        assert view._dragging_components == []

    def test_real_move_still_recorded(self, scene):
        from diagrammer.items.shape_item import RectangleItem
        from diagrammer.commands.shape_command import AddShapeCommand

        rect = RectangleItem(width=100, height=60)
        cmd = AddShapeCommand(scene, rect, QPointF(40, 40))
        scene.undo_stack.push(cmd)
        count_before = scene.undo_stack.count()

        view = _make_view(scene)
        view._dragging_components = [rect]
        view._drag_anchor_item = rect
        view._drag_anchor_start_pos = QPointF(rect.pos())
        view._drag_mouse_start = QPointF(rect.pos())
        scene.record_move_start(rect.instance_id, rect.pos())
        rect.setPos(QPointF(140, 140))  # simulate the drag

        view.mouseReleaseEvent(_release_event(view, rect.pos()))

        assert scene.undo_stack.count() == count_before + 1
        scene.undo_stack.undo()
        assert rect.pos() == QPointF(40, 40)


class TestAutoJoinOnMove:
    def test_ports_dropped_onto_each_other_connect(self, scene, library):
        """Dragging a component so a port lands on another port auto-joins."""
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.commands.add_command import AddComponentCommand

        defs = list(library.all_defs())
        if not defs:
            pytest.skip("no built-in components available")
        comp_def = defs[0]

        cmd_a = AddComponentCommand(scene, comp_def, QPointF(0, 0))
        scene.undo_stack.push(cmd_a)
        cmd_b = AddComponentCommand(scene, comp_def, QPointF(400, 400))
        scene.undo_stack.push(cmd_b)
        a, b = cmd_a.item, cmd_b.item
        if not a.ports or not b.ports:
            pytest.skip("component has no ports")

        view = _make_view(scene)
        # Move B so its first port lands exactly on A's first port
        target = a.ports[0].scene_center()
        current = b.ports[0].scene_center()
        delta = target - current

        view._dragging_components = [b]
        view._drag_anchor_item = b
        view._drag_anchor_start_pos = QPointF(b.pos())
        view._drag_mouse_start = QPointF(b.pos())
        scene.record_move_start(b.instance_id, b.pos())
        b._skip_snap = True
        b.setPos(b.pos() + delta)
        b._skip_snap = False

        view.mouseReleaseEvent(_release_event(view, b.pos()))

        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 1, "overlapping ports did not auto-join"
        # The move and the join undo together
        scene.undo_stack.undo()
        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 0
        assert b.pos() == QPointF(400, 400)


class TestDeleteCascade:
    def test_delete_component_removes_attached_wires(self, scene, library):
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.commands.add_command import AddComponentCommand

        defs = list(library.all_defs())
        if not defs:
            pytest.skip("no built-in components available")
        comp_def = defs[0]
        cmd_a = AddComponentCommand(scene, comp_def, QPointF(0, 0))
        scene.undo_stack.push(cmd_a)
        cmd_b = AddComponentCommand(scene, comp_def, QPointF(300, 0))
        scene.undo_stack.push(cmd_b)
        a, b = cmd_a.item, cmd_b.item
        if not a.ports or not b.ports:
            pytest.skip("component has no ports")
        _connect(scene, a.ports[0], b.ports[0])

        scene.delete_items_with_dependents([a])

        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert conns == [], "deleting a component left its wire dangling"
        assert a.scene() is None
        assert b.scene() is not None

        # One undo restores component AND wire
        scene.undo_stack.undo()
        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 1
        assert a.scene() is scene

    def test_delete_wire_removes_stranded_free_junctions(self, scene):
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        j1 = _add_junction(scene, 0, 0, visible=False)
        j2 = _add_junction(scene, 100, 0, visible=False)
        conn = _connect(scene, j1.port, j2.port)

        scene.delete_items_with_dependents([conn])

        assert [i for i in scene.items() if isinstance(i, ConnectionItem)] == []
        assert [i for i in scene.items() if isinstance(i, JunctionItem)] == [], (
            "deleting a free wire left orphan junctions behind"
        )
        scene.undo_stack.undo()
        assert len([i for i in scene.items() if isinstance(i, JunctionItem)]) == 2
        assert len([i for i in scene.items() if isinstance(i, ConnectionItem)]) == 1

    def test_shared_junction_survives_partial_delete(self, scene):
        """A junction still used by a remaining wire must not be deleted."""
        from diagrammer.items.junction_item import JunctionItem

        hub = _add_junction(scene, 0, 0)
        j_a = _add_junction(scene, 100, 0)
        j_b = _add_junction(scene, 0, 100)
        conn_a = _connect(scene, hub.port, j_a.port)
        _connect(scene, hub.port, j_b.port)

        scene.delete_items_with_dependents([conn_a])

        junctions = [i for i in scene.items() if isinstance(i, JunctionItem)]
        assert hub in junctions, "shared junction was deleted while still in use"
        assert j_a not in junctions, "stranded junction was left behind"
        assert j_b in junctions


class TestJunctionUndoLifecycle:
    def test_cancelled_trace_removes_pending_junction(self, scene):
        from diagrammer.items.junction_item import JunctionItem

        junc = _add_junction(scene, 40, 40, visible=False)
        scene.adopt_pending_junction(junc)
        scene.begin_connection(junc.port)
        scene._cancel_connection()

        assert [i for i in scene.items() if isinstance(i, JunctionItem)] == [], (
            "cancelled trace leaked its start junction"
        )

    def test_undo_removes_trace_start_junction(self, scene):
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        # An existing target junction (pre-existing, e.g. loaded from file)
        target = _add_junction(scene, 200, 0, visible=False)
        _connect(scene, target.port, _add_junction(scene, 300, 0, visible=False).port)
        baseline_juncs = len([i for i in scene.items() if isinstance(i, JunctionItem)])

        # Trace started from empty space: junction created + adopted
        start = _add_junction(scene, 0, 0, visible=False)
        scene.adopt_pending_junction(start)
        scene.begin_connection(start.port)
        scene._current_target_port = target.port
        ok = scene.finish_connection_with_vertices([QPointF(100, 0)])
        assert ok

        scene.undo_stack.undo()

        juncs = [i for i in scene.items() if isinstance(i, JunctionItem)]
        assert len(juncs) == baseline_juncs, (
            "undoing the wire left its trace-start junction behind"
        )
        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 1  # only the pre-existing wire


class TestTJunctionTopology:
    def _t_setup(self, scene):
        """Host wire between two free junctions; branch source junction."""
        j1 = _add_junction(scene, 0, 0, visible=False)
        j2 = _add_junction(scene, 200, 0, visible=False)
        host = _connect(scene, j1.port, j2.port)
        src = _add_junction(scene, 100, 100, visible=False)
        return j1, j2, host, src

    def test_finish_on_wire_splits_host(self, scene):
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        j1, j2, host, src = self._t_setup(scene)
        scene.adopt_pending_junction(src)
        scene.begin_connection(src.port)
        ok = scene.finish_connection(cursor_pos=QPointF(100, 2))
        assert ok

        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 3, "host wire was not split at the T-junction"
        assert host not in conns, "original host wire should be replaced"

        # All three wires must share one junction port
        juncs = [
            i for i in scene.items()
            if isinstance(i, JunctionItem) and i not in (j1, j2, src)
        ]
        assert len(juncs) == 1
        t_port = juncs[0].port
        sharing = [
            c for c in conns
            if c.source_port is t_port or c.target_port is t_port
        ]
        assert len(sharing) == 3

    def test_undo_restores_single_host_wire(self, scene):
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        j1, j2, host, src = self._t_setup(scene)
        n_junc_before = len([i for i in scene.items() if isinstance(i, JunctionItem)])
        scene.adopt_pending_junction(src)
        scene.begin_connection(src.port)
        assert scene.finish_connection(cursor_pos=QPointF(100, 2))

        scene.undo_stack.undo()

        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 1, "undo did not restore the single host wire"
        assert conns[0].source_port is j1.port
        assert conns[0].target_port is j2.port
        # T-junction gone; branch-source junction (pending, unconnected) too
        juncs = [i for i in scene.items() if isinstance(i, JunctionItem)]
        assert len(juncs) == n_junc_before - 1  # src was consumed/removed

    def test_existing_junction_is_a_snap_target(self, scene):
        """A third wire near an existing junction reuses it instead of
        stacking a second junction on top."""
        j1 = _add_junction(scene, 0, 0, visible=False)
        j2 = _add_junction(scene, 200, 0, visible=False)
        _connect(scene, j1.port, j2.port)

        src = _add_junction(scene, 100, 100, visible=False)
        scene.begin_connection(src.port)
        found = scene._find_nearest_target_port(QPointF(198, 3))
        scene._cancel_connection()
        assert found is j2.port, "existing junction port was not offered as target"
