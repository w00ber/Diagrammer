"""AddComponentCommand — undoable command for placing a component on the scene."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsScene

from diagrammer.items.component_item import ComponentItem
from diagrammer.models.component_def import ComponentDef


class AddComponentCommand(QUndoCommand):
    """Add a component to the scene at a given position."""

    def __init__(
        self,
        scene: QGraphicsScene,
        component_def: ComponentDef,
        pos: QPointF,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._component_def = component_def
        self._pos = pos
        self._item: ComponentItem | None = None
        self.setText(f"Add {component_def.name}")

    @property
    def item(self) -> ComponentItem | None:
        return self._item

    def redo(self) -> None:
        if self._item is None:
            self._item = ComponentItem(self._component_def)
            # Assign to active layer
            if hasattr(self._scene, 'assign_active_layer'):
                self._scene.assign_active_layer(self._item)
        self._item.setPos(self._pos)
        self._scene.addItem(self._item)

    def undo(self) -> None:
        if self._item is not None:
            self._scene.removeItem(self._item)


class MoveComponentCommand(QUndoCommand):
    """Record a component move for undo/redo."""

    def __init__(
        self,
        item: ComponentItem,
        old_pos: QPointF,
        new_pos: QPointF,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._old_pos = old_pos
        self._new_pos = new_pos
        self.setText(f"Move {item.component_def.name}")

    def redo(self) -> None:
        # Bypass snap to place at exact position (alignment, etc.)
        self._item._skip_snap = True
        self._item.setPos(self._new_pos)
        self._item._skip_snap = False

    def undo(self) -> None:
        self._item._skip_snap = True
        self._item.setPos(self._old_pos)
        self._item._skip_snap = False
