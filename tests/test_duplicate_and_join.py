"""Stacked copies must never arise, and never multiply wires.

A diagram came in with identical capacitors stacked sixteen deep and
C(16, 2) = 120 wires per geometry joining them pairwise. Two things
conspired: undoing an Alt-drag duplicate only reverted the move, so the
clones snapped back onto their originals as an invisible stack; and the
port auto-join then wired every coincident copy to every other on the
next drag of the stack.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from diagrammer.canvas.view import DiagramView
from diagrammer.commands.add_command import AddComponentCommand
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.connection_item import ConnectionItem


def _def_with_ports(library):
    for d in library.all_defs():
        if d.ports:
            return d
    raise AssertionError("library has no component with ports")


def _count(scene, cls) -> int:
    return sum(1 for i in scene.items() if isinstance(i, cls))


def _ev(view, kind, pos, mods=Qt.KeyboardModifier.NoModifier):
    buttons = (Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease
               else Qt.MouseButton.LeftButton)
    return QMouseEvent(kind, QPointF(view.mapFromScene(pos)),
                       Qt.MouseButton.LeftButton, buttons, mods)


def _place(scene, library, pos):
    cmd = AddComponentCommand(scene, _def_with_ports(library), pos)
    scene.undo_stack.push(cmd)
    return cmd.item


class TestAltDragDuplicate:
    def test_one_undo_removes_the_clone_and_its_move(self, scene, library):
        view = DiagramView(scene)
        view.resize(800, 600)
        comp = _place(scene, library, QPointF(100, 100))
        at = comp.sceneBoundingRect().center()
        view.mousePressEvent(_ev(view, QEvent.Type.MouseButtonPress, at,
                                 Qt.KeyboardModifier.AltModifier))
        view.mouseMoveEvent(_ev(view, QEvent.Type.MouseMove, at + QPointF(200, 0)))
        view.mouseReleaseEvent(_ev(view, QEvent.Type.MouseButtonRelease, at + QPointF(200, 0)))
        assert _count(scene, ComponentItem) == 2

        scene.undo_stack.undo()

        assert _count(scene, ComponentItem) == 1, "clone survived the undo"
        view.setScene(None)

    def test_alt_click_gives_an_offset_copy_not_a_stack(self, scene, library):
        view = DiagramView(scene)
        view.resize(800, 600)
        view.grid_spacing = 20
        comp = _place(scene, library, QPointF(100, 100))
        at = comp.sceneBoundingRect().center()
        view.mousePressEvent(_ev(view, QEvent.Type.MouseButtonPress, at,
                                 Qt.KeyboardModifier.AltModifier))
        view.mouseReleaseEvent(_ev(view, QEvent.Type.MouseButtonRelease, at))

        comps = [i for i in scene.items() if isinstance(i, ComponentItem)]
        assert len(comps) == 2
        clone = next(c for c in comps if c is not comp)
        assert clone.pos() != comp.pos(), "copy stacked on the original"
        assert _count(scene, ConnectionItem) == 0
        view.setScene(None)


class TestAutoJoinOnlyForNewContact:
    def test_dragging_a_stack_does_not_wire_it_together(self, scene, library):
        """Two coincident copies moved together: no wire between them."""
        view = DiagramView(scene)
        view.resize(800, 600)
        a = _place(scene, library, QPointF(100, 100))
        b = _place(scene, library, QPointF(100, 100))
        view._begin_item_drag(a, [a, b], QPointF(100, 100))
        view._drag.move_to(QPointF(60, 0))
        view._drag.commit("Move group")
        view._drag = None
        assert _count(scene, ConnectionItem) == 0
        view.setScene(None)

    def test_ports_that_already_touched_are_left_alone(self, scene, library):
        """Moving one copy off a stack must not join it to what it left."""
        view = DiagramView(scene)
        view.resize(800, 600)
        a = _place(scene, library, QPointF(100, 100))
        _place(scene, library, QPointF(100, 100))
        view._begin_item_drag(a, [a], QPointF(100, 100))
        view._drag.move_to(QPointF(0.0, 0.0))
        view._drag.commit("Move group")
        view._drag = None
        assert _count(scene, ConnectionItem) == 0
        view.setScene(None)

    def test_dropping_onto_a_port_still_joins(self, scene, library):
        view = DiagramView(scene)
        view.resize(800, 600)
        a = _place(scene, library, QPointF(0, 0))
        b = _place(scene, library, QPointF(400, 400))
        delta = a.ports[0].scene_center() - b.ports[0].scene_center()
        view._begin_item_drag(b, [b], b.pos())
        view._drag.move_to(delta)
        view._drag.commit("Move group")
        view._drag = None
        assert _count(scene, ConnectionItem) == 1
        view.setScene(None)


class TestAltDragLeavesTheOriginalAlone:
    def test_original_wire_waypoints_do_not_travel_with_the_clone(self, scene, library):
        """Rubber-band a wired pair (which selects the wire's bend too),
        then Alt-drag it: the copy moves, the original's bend must not."""
        from PySide6.QtCore import QRectF
        from diagrammer.commands.connect_command import CreateConnectionCommand

        view = DiagramView(scene)
        view.resize(800, 600)
        a = _place(scene, library, QPointF(0, 0))
        b = _place(scene, library, QPointF(300, 0))
        cmd = CreateConnectionCommand(scene, a.ports[-1], b.ports[0])
        scene.undo_stack.push(cmd)
        wire = cmd.connection
        wire.vertices = [QPointF(150, -80)]
        scene.update_connections()

        band = QRectF(-50, -150, 500, 300)
        for it in scene.items(band):
            it.setSelected(True)
        view._rubber_band_rect = band
        view.mouseReleaseEvent(_ev(view, QEvent.Type.MouseButtonRelease, band.bottomRight()))
        assert view._selected_waypoint_set, "band should have picked the bend"

        at = a.sceneBoundingRect().center()
        view.mousePressEvent(_ev(view, QEvent.Type.MouseButtonPress, at,
                                 Qt.KeyboardModifier.AltModifier))
        view.mouseMoveEvent(_ev(view, QEvent.Type.MouseMove, at + QPointF(0, 400)))
        view.mouseReleaseEvent(_ev(view, QEvent.Type.MouseButtonRelease, at + QPointF(0, 400)))

        assert [(w.x(), w.y()) for w in wire.vertices] == [(150.0, -80.0)]
        clone_wire = next(c for c in scene.items()
                          if isinstance(c, ConnectionItem) and c is not wire)
        assert clone_wire.vertices[0].y() > 200, "clone's bend should have moved with it"
        view.setScene(None)
