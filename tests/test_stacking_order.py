"""Stacking order inside a layer follows creation order, not item type.

Z used to be assigned from a per-type band (shapes < images < components <
wires < annotations), which pinned every wire above every component forever:
a part pasted on top of an existing circuit still had that circuit's wire
stubs painted across it, and Bring to Front could not lift it clear.
Components, junctions, wires, shapes and images now share one band and stack
in the order they were added; annotations keep a band above so a label is
never buried.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPointF

from diagrammer.canvas.scene import DiagramScene
from diagrammer.clipboard_ops import ClipboardMixin
from diagrammer.commands.connect_command import CreateConnectionCommand
from diagrammer.io.serializer import DiagramSerializer
from diagrammer.items.annotation_item import AnnotationItem
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.shape_item import RectangleItem


class _Host(ClipboardMixin):
    def __init__(self, scene, library):
        self._scene = scene
        self._view = None
        self._library = library
        self._clipboard: list = []

    def _try_paste_external_svg(self) -> bool:
        return False


def _two_port_def(library):
    for cdef in library.all_defs():
        if len(cdef.ports) >= 2:
            return cdef
    pytest.skip("no two-port component in the library")


def _place(scene, cdef, x, y):
    from diagrammer.commands.add_command import AddComponentCommand
    cmd = AddComponentCommand(scene, cdef, QPointF(x, y))
    scene.undo_stack.push(cmd)
    return cmd.item


def _wire(scene, a, b):
    cmd = CreateConnectionCommand(scene, a.ports[-1], b.ports[0])
    scene.undo_stack.push(cmd)
    return cmd.connection


@pytest.fixture
def host(library):
    return _Host(DiagramScene(library=library), library)


def test_component_added_later_sits_above_an_existing_wire(host, library):
    cdef = _two_port_def(library)
    a = _place(host._scene, cdef, 0, 0)
    b = _place(host._scene, cdef, 300, 0)
    conn = _wire(host._scene, a, b)

    later = _place(host._scene, cdef, 150, 0)
    assert later.zValue() > conn.zValue()


def test_wire_still_draws_over_the_parts_it_connects(host, library):
    cdef = _two_port_def(library)
    a = _place(host._scene, cdef, 0, 0)
    b = _place(host._scene, cdef, 300, 0)
    conn = _wire(host._scene, a, b)

    assert conn.zValue() > a.zValue()
    assert conn.zValue() > b.zValue()


def test_pasted_component_lands_above_everything_already_drawn(host, library):
    cdef = _two_port_def(library)
    a = _place(host._scene, cdef, 0, 0)
    b = _place(host._scene, cdef, 300, 0)
    conn = _wire(host._scene, a, b)

    a.setSelected(True)
    host._copy()
    host._scene.clearSelection()
    host._paste()

    pasted = [
        i for i in host._scene.items()
        if isinstance(i, ComponentItem) and i not in (a, b)
    ]
    assert len(pasted) == 1
    assert pasted[0].zValue() > conn.zValue()


def test_pasted_shape_lands_above_the_components(host, library):
    cdef = _two_port_def(library)
    comp = _place(host._scene, cdef, 0, 0)

    rect = RectangleItem(width=80, height=40)
    host._scene.addItem(rect)
    host._scene.assign_item_z(rect)
    rect.setSelected(True)
    host._copy()
    host._scene.clearSelection()
    host._paste()

    pasted = [
        i for i in host._scene.items()
        if isinstance(i, RectangleItem) and i is not rect
    ]
    assert len(pasted) == 1
    assert pasted[0].zValue() > comp.zValue()


def test_annotations_stay_above_artwork(host, library):
    cdef = _two_port_def(library)
    annot = AnnotationItem("label")
    host._scene.addItem(annot)
    host._scene.assign_item_z(annot)

    comp = _place(host._scene, cdef, 0, 0)
    host._scene.reflow_z()
    assert annot.zValue() > comp.zValue()


def test_reflow_keeps_the_order_it_was_given(host, library):
    cdef = _two_port_def(library)
    a = _place(host._scene, cdef, 0, 0)
    b = _place(host._scene, cdef, 300, 0)
    conn = _wire(host._scene, a, b)
    later = _place(host._scene, cdef, 150, 0)

    host._scene.reflow_z()
    order = sorted([a, b, conn, later], key=lambda i: i.zValue())
    assert order == [a, b, conn, later]


def test_stacking_order_survives_save_and_load(host, library, tmp_path: Path):
    cdef = _two_port_def(library)
    a = _place(host._scene, cdef, 0, 0)
    b = _place(host._scene, cdef, 300, 0)
    conn = _wire(host._scene, a, b)
    later = _place(host._scene, cdef, 150, 0)
    host._scene.reflow_z()

    path = tmp_path / "stack.dgm"
    DiagramSerializer.save(host._scene, path)
    scene2 = DiagramScene(library=library)
    DiagramSerializer.load(scene2, path, library=library)

    ids_by_z = [
        i.instance_id
        for i in sorted(
            (it for it in scene2.items()
             if isinstance(it, (ComponentItem, ConnectionItem))),
            key=lambda it: it.zValue(),
        )
    ]
    assert ids_by_z == [a.instance_id, b.instance_id,
                        conn.instance_id, later.instance_id]


def test_bring_to_front_survives_a_reflow(host, library):
    """Reflow renumbers Z; it must not undo a deliberate reordering."""
    cdef = _two_port_def(library)
    a = _place(host._scene, cdef, 0, 0)
    b = _place(host._scene, cdef, 300, 0)
    conn = _wire(host._scene, a, b)

    top = max(i.zValue() for i in (a, b, conn))
    a.setZValue(top + 1.0)
    host._scene.reflow_z()
    assert a.zValue() > conn.zValue()
