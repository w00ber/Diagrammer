"""Replay the user gestures that broke, against the user's own files.

``tests/fixtures/*.dgm`` are the diagrams from the October 2026 bug
reports, unchanged. Each test loads one, performs the exact gesture
through the view's event handlers, and asserts the property that was
violated. See ``docs/wiring-interaction-redesign.md``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent

from diagrammer.canvas.scene import DiagramScene
from diagrammer.canvas.view import DiagramView
from diagrammer.io.serializer import DiagramSerializer
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.junction_item import JunctionItem

FIXTURES = Path(__file__).parent / "fixtures"


@contextmanager
def _open(library, name: str):
    """Yield (scene, view) for a fixture and tear them down safely.

    The view must let go of the scene before the scene is destroyed:
    a QGraphicsView whose scene dies first segfaults in Qt, and the
    order in which a test's locals are collected is not ours to pick.
    """
    scene = DiagramScene(library=library)
    DiagramSerializer.load(scene, FIXTURES / name, library=library)
    view = DiagramView(scene)
    view.resize(1400, 1000)
    scene.update_connections()
    try:
        yield scene, view
    finally:
        view.setScene(None)


def _event(view, kind, scene_pos: QPointF) -> QMouseEvent:
    buttons = (Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease
               else Qt.MouseButton.LeftButton)
    return QMouseEvent(kind, QPointF(view.mapFromScene(scene_pos)),
                       Qt.MouseButton.LeftButton, buttons,
                       Qt.KeyboardModifier.NoModifier)


def _rubber_band(scene, view, band: QRectF) -> None:
    """What Qt's rubber band does, then the view's own post-processing."""
    for it in scene.items(band):
        if isinstance(it, (ComponentItem, JunctionItem, ConnectionItem)):
            it.setSelected(True)
    view._rubber_band_rect = band
    view.mouseReleaseEvent(_event(view, QEvent.Type.MouseButtonRelease,
                                  band.bottomRight()))


def _drag(scene, view, anchor, delta: QPointF) -> None:
    at = anchor.sceneBoundingRect().center()
    view.mousePressEvent(_event(view, QEvent.Type.MouseButtonPress, at))
    view.mouseMoveEvent(_event(view, QEvent.Type.MouseMove, at + delta))
    view.mouseReleaseEvent(_event(view, QEvent.Type.MouseButtonRelease, at + delta))
    scene.update_connections()


def _geometry(scene) -> dict:
    out = {}
    for i in scene.items():
        if isinstance(i, ConnectionItem):
            out["W:" + i.instance_id] = [
                (round(p.x(), 1), round(p.y(), 1)) for p in i.routed_points()
            ]
        elif isinstance(i, (ComponentItem, JunctionItem)):
            out["I:" + i.instance_id] = (round(i.pos().x(), 1), round(i.pos().y(), 1))
    return out


def _ladder_items(scene, y_lo: float, y_hi: float):
    comps = [i for i in scene.items()
             if isinstance(i, ComponentItem) and y_lo <= i.pos().y() <= y_hi]
    return comps


class TestSelectAllAndDragIsRigid:
    def test_boxing_the_whole_ladder_moves_it_as_one(self, library):
        """Every junction on the rails must travel; nothing may deform."""
        with _open(library, "ladder_torn_rails.dgm") as (scene, view):
            before = _geometry(scene)
            _rubber_band(scene, view, QRectF(0, 0, 1200, 1200))
            anchor = next(i for i in scene.selectedItems()
                          if isinstance(i, ComponentItem)
                          and "current" in i.component_def.name)

            _drag(scene, view, anchor, QPointF(100, 60))

            after = _geometry(scene)
            probe = "I:" + anchor.instance_id
            dx = after[probe][0] - before[probe][0]
            dy = after[probe][1] - before[probe][1]
            assert (dx, dy) != (0, 0)
            for key, was in before.items():
                now = after[key]
                if isinstance(was, tuple):
                    assert now == (was[0] + dx, was[1] + dy), key
                else:
                    assert now == [(x + dx, y + dy) for x, y in was], key


class TestPartialSelectionLeavesTheRestAlone:
    def test_boxing_the_source_and_resistor_does_not_tear_the_bottom_rail(self, library):
        """The band takes the tee at (340, 840) — the user boxed it — so
        the rail segment hanging off that tee rubber-bands. Every other
        segment of the bottom rail runs on to LC branches that were not
        selected and must come through untouched; the stretched segment
        must still end on its far junction."""
        band = QRectF(180, 640, 260, 260)
        with _open(library, "ladder_two_copies.dgm") as (scene, view):
            rails_before = {}
            for i in scene.items():
                if (isinstance(i, ConnectionItem)
                        and isinstance(i.source_port.component, JunctionItem)
                        and isinstance(i.target_port.component, JunctionItem)
                        and abs(i.source_port.scene_center().y() - 840) < 1):
                    rails_before[i.instance_id] = [
                        (round(p.x()), round(p.y())) for p in i.routed_points()
                    ]
            assert rails_before, "fixture changed: no bottom-rail segments found"

            _rubber_band(scene, view, band)
            boxed = {j.instance_id for j in scene.selectedItems()
                     if isinstance(j, JunctionItem)}
            assert boxed, "the band should have selected the tee it covers"
            anchor = next(i for i in scene.selectedItems() if isinstance(i, ComponentItem))
            _drag(scene, view, anchor, QPointF(-60, 170))

            for i in scene.items():
                if not (isinstance(i, ConnectionItem) and i.instance_id in rails_before):
                    continue
                now = [(round(p.x()), round(p.y())) for p in i.routed_points()]
                was = rails_before[i.instance_id]
                touches_boxed = (i.source_port.component.instance_id in boxed
                                 or i.target_port.component.instance_id in boxed)
                if touches_boxed:
                    assert now[-1] == was[-1], "stretched segment lost its far end"
                else:
                    assert now == was, "untouched rail segment deformed"


class TestUndoAfterGestureRestoresGeometry:
    @pytest.mark.parametrize("band,delta", [
        (QRectF(0, 0, 1200, 1200), QPointF(100, 60)),
        (QRectF(180, 640, 260, 260), QPointF(-60, 170)),
    ])
    def test_one_undo_is_exact(self, library, band, delta):
        with _open(library, "ladder_torn_rails.dgm") as (scene, view):
            before = _geometry(scene)
            _rubber_band(scene, view, band)
            anchor = next(i for i in scene.selectedItems() if isinstance(i, ComponentItem))
            _drag(scene, view, anchor, delta)
            assert _geometry(scene) != before

            scene.undo_stack.undo()
            scene.update_connections()

            assert _geometry(scene) == before
