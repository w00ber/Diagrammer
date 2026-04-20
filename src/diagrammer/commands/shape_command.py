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
