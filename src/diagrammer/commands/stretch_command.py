"""StretchComponentCommand — undoable stretch operation on a component."""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from diagrammer.items.component_item import ComponentItem


class StretchComponentCommand(QUndoCommand):
    """Record a stretch change on a component for undo/redo.

    Stores the old and new stretch deltas (dx, dy) and restores them
    on undo/redo.
    """

    def __init__(
        self,
        item: ComponentItem,
        old_dx: float,
        old_dy: float,
        new_dx: float,
        new_dy: float,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._old_dx = old_dx
        self._old_dy = old_dy
        self._new_dx = new_dx
        self._new_dy = new_dy
        axis_parts = []
        if old_dx != new_dx:
            axis_parts.append("H")
        if old_dy != new_dy:
            axis_parts.append("V")
        axis_label = "/".join(axis_parts) if axis_parts else "Stretch"
        self.setText(f"Stretch {item.component_def.name} ({axis_label})")

    def redo(self) -> None:
        self._item.set_stretch(self._new_dx, self._new_dy)
        # Update connections after stretch
        scene = self._item.scene()
        if scene and hasattr(scene, 'update_connections'):
            scene.update_connections()

    def undo(self) -> None:
        self._item.set_stretch(self._old_dx, self._old_dy)
        scene = self._item.scene()
        if scene and hasattr(scene, 'update_connections'):
            scene.update_connections()
