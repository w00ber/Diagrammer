"""Snapping is an input concern.

The drag pipeline and the library drop decide where things land; a
programmatic ``setPos`` — a move command, an alignment, a transform, a
paste — is exact. Grid snapping used to live in each item's
``itemChange`` and had to be disarmed with ``_skip_snap`` at 88 sites.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF

from diagrammer.canvas.view import DiagramView
from diagrammer.commands.add_command import AddComponentCommand, MoveComponentCommand
from diagrammer.items.component_item import ComponentItem


def _def_with_ports(library):
    for d in library.all_defs():
        if d.ports:
            return d
    raise AssertionError("library has no component with ports")


def test_library_drop_lands_the_port_on_the_grid(scene, library):
    view = DiagramView(scene)
    view.grid_spacing = 20
    cmd = AddComponentCommand(scene, _def_with_ports(library), QPointF(7, 13),
                              snap_port_to_grid=True)
    scene.undo_stack.push(cmd)
    port = cmd.item.ports[0].scene_center()
    assert port.x() % 20 == 0 and port.y() % 20 == 0
    # And redo after undo keeps the same decision.
    scene.undo_stack.undo()
    scene.undo_stack.redo()
    assert cmd.item.ports[0].scene_center() == port
    view.setScene(None)


def test_programmatic_move_is_exact(scene, library):
    view = DiagramView(scene)
    view.grid_spacing = 20
    cmd = AddComponentCommand(scene, _def_with_ports(library), QPointF(0, 0))
    scene.undo_stack.push(cmd)
    item: ComponentItem = cmd.item
    scene.undo_stack.push(MoveComponentCommand(item, item.pos(), QPointF(3, 5)))
    assert item.pos() == QPointF(3, 5)
    view.setScene(None)


def test_drag_delta_snaps_the_anchor_port(scene, library):
    view = DiagramView(scene)
    view.resize(800, 600)
    view.grid_spacing = 20
    cmd = AddComponentCommand(scene, _def_with_ports(library), QPointF(0, 0))
    scene.undo_stack.push(cmd)
    item: ComponentItem = cmd.item
    item.set_snap_anchor_closest_to(item.ports[0].scene_center())

    view._begin_item_drag(item, [item], QPointF(0, 0))
    delta = view._snapped_drag_delta(QPointF(33, 48))  # raw drag of (33, 48)
    view._drag.move_to(delta)
    port = item.ports[0].scene_center()
    assert port.x() % 20 == 0 and port.y() % 20 == 0
    view._drag.commit("Move group")
    view._drag = None
    view.setScene(None)
