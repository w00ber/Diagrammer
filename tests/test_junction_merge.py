"""Coincident junctions collapse on load, and transforms use the drag rule."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF

from diagrammer.canvas.scene import DiagramScene
from diagrammer.commands.connect_command import CreateConnectionCommand
from diagrammer.io.serializer import DiagramSerializer
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.junction_item import JunctionItem

FIXTURES = Path(__file__).parent / "fixtures"


def _junction(scene, x, y):
    j = JunctionItem()
    j.setPos(QPointF(x, y))
    scene.addItem(j)
    return j


def _connect(scene, a, b):
    cmd = CreateConnectionCommand(scene, a, b)
    scene.undo_stack.push(cmd)
    return cmd.connection


class TestMergeCoincidentJunctions:
    def test_stack_collapses_and_wires_follow(self, scene, library):
        cdef = library.get("CRYO_WIRING_6pt/passive/RESISTOR")
        comp = ComponentItem(cdef)
        comp.setPos(QPointF(0, 0))
        scene.addItem(comp)
        j1 = _junction(scene, 400, 20)
        j2 = _junction(scene, 400, 20)   # the stacked copy
        far = _junction(scene, 700, 20)
        w_a = _connect(scene, comp.port_by_name("right"), j1.port)
        w_b = _connect(scene, j2.port, far.port)
        w_loop = _connect(scene, j1.port, j2.port)  # a join between the copies

        removed = scene.merge_coincident_junctions()

        assert removed == 1
        junctions = [i for i in scene.items() if isinstance(i, JunctionItem)]
        assert len(junctions) == 2
        kept = next(j for j in junctions if j.pos() == QPointF(400, 20))
        wires = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert w_loop not in wires, "self-loop between former copies survived"
        assert {w_a, w_b} == set(wires)
        assert w_a.target_port is kept.port and w_b.source_port is kept.port
        assert scene.connections_on_port(kept.port) == [w_a, w_b] or \
            set(scene.connections_on_port(kept.port)) == {w_a, w_b}

    def test_old_file_with_stacked_junctions_loads_clean(self, library):
        """ladder_two_copies.dgm carries six duplicate junction pairs."""
        scene = DiagramScene(library=library)
        DiagramSerializer.load(scene, FIXTURES / "ladder_two_copies.dgm", library=library)
        positions = [(round(j.pos().x()), round(j.pos().y()))
                     for j in scene.items() if isinstance(j, JunctionItem)]
        assert len(positions) == len(set(positions)), "stacked junctions survived load"
        for wire in (i for i in scene.items() if isinstance(i, ConnectionItem)):
            assert wire.source_port is not wire.target_port or wire.closed


class TestTransformsUseTheDragRule:
    def test_rotating_a_tap_leaves_a_hub_tee_on_the_rail(self, scene, library):
        """A tee whose rail runs on to an unselected component stays put
        when only the tapped component is rotated."""
        from diagrammer.clipboard_ops import ClipboardMixin
        from diagrammer.transform_ops import TransformMixin

        class Host(ClipboardMixin, TransformMixin):
            def __init__(self):
                self._scene = scene
                self._view = None
                self._library = library
                self._clipboard = []

        cdef = library.get("CRYO_WIRING_6pt/passive/RESISTOR")
        a = ComponentItem(cdef); a.setPos(QPointF(0, 0)); scene.addItem(a)
        b = ComponentItem(cdef); b.setPos(QPointF(600, 0)); scene.addItem(b)
        tee = _junction(scene, 300, 20)
        _connect(scene, a.port_by_name("right"), tee.port)
        _connect(scene, tee.port, b.port_by_name("left"))
        scene.update_connections()

        a.setSelected(True)
        Host()._rotate_selected(90)

        assert tee.pos() == QPointF(300, 20)
        assert b.pos() == QPointF(600, 0)
