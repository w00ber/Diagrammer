"""Shape commands — undoable creation of simple shape items."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene


class AddShapeCommand(QUndoCommand):
    """Add a shape item to the scene (undoable)."""

    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        pos: QPointF,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._item = item
        self._pos = pos
        self._z: float | None = None
        self.setText(f"Add {type(item).__name__}")

    @property
    def item(self) -> QGraphicsItem:
        return self._item

    def redo(self) -> None:
        if not hasattr(self._item, '_layer_index') and hasattr(self._scene, 'assign_active_layer'):
            self._scene.assign_active_layer(self._item)
        self._item.setPos(self._pos)
        self._scene.addItem(self._item)
        if self._z is None:
            self._z = self._scene.assign_item_z(self._item)
        else:
            self._item.setZValue(self._z)

    def undo(self) -> None:
        self._scene.removeItem(self._item)


class ResizeShapeCommand(QUndoCommand):
    """Record a resize (handle drag) of a ShapeItem for undo/redo.

    Resizing also shifts the item's position (left/top handles), so the
    old and new position are stored alongside width/height.
    """

    def __init__(
        self,
        item: QGraphicsItem,
        old_w: float,
        old_h: float,
        old_pos: QPointF,
        new_w: float,
        new_h: float,
        new_pos: QPointF,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._old_w = old_w
        self._old_h = old_h
        self._old_pos = QPointF(old_pos)
        self._new_w = new_w
        self._new_h = new_h
        self._new_pos = QPointF(new_pos)
        self.setText(f"Resize {type(item).__name__}")

    def _apply(self, w: float, h: float, pos: QPointF) -> None:
        self._item.resize(w, h)
        self._item._skip_snap_once = True
        self._item.setPos(pos)

    def redo(self) -> None:
        self._apply(self._new_w, self._new_h, self._new_pos)

    def undo(self) -> None:
        self._apply(self._old_w, self._old_h, self._old_pos)


class MoveLineEndpointCommand(QUndoCommand):
    """Record an endpoint drag of a LineItem for undo/redo."""

    def __init__(
        self,
        item: QGraphicsItem,
        old_start: QPointF,
        old_end: QPointF,
        new_start: QPointF,
        new_end: QPointF,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._old_start = QPointF(old_start)
        self._old_end = QPointF(old_end)
        self._new_start = QPointF(new_start)
        self._new_end = QPointF(new_end)
        self.setText("Move line endpoint")

    def _apply(self, start: QPointF, end: QPointF) -> None:
        self._item.prepareGeometryChange()
        self._item._start = QPointF(start)
        self._item._end = QPointF(end)
        self._item.update()

    def redo(self) -> None:
        self._apply(self._new_start, self._new_end)

    def undo(self) -> None:
        self._apply(self._old_start, self._old_end)
