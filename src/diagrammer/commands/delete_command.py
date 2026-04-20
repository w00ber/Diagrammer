"""DeleteCommand — undoable deletion of items from the scene."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene


class DeleteCommand(QUndoCommand):
    """Remove one or more items from the scene (undoable)."""

    def __init__(
        self,
        scene: QGraphicsScene,
        items: list[QGraphicsItem],
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        # Store items and their positions for restoration
        self._items = list(items)
        self._positions = [item.pos() for item in items]
        count = len(items)
        self.setText(f"Delete {count} item{'s' if count != 1 else ''}")

    def redo(self) -> None:
        from diagrammer.items.connection_item import ConnectionItem
        for item in self._items:
            if isinstance(item, ConnectionItem):
                self._scene.unregister_connection(item)
            self._scene.removeItem(item)

    def undo(self) -> None:
        from diagrammer.items.connection_item import ConnectionItem
        for item, pos in zip(self._items, self._positions):
            self._scene.addItem(item)
            if isinstance(item, ConnectionItem):
                self._scene.register_connection(item)
            item.setPos(pos)
