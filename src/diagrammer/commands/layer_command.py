"""Layer commands — undoable reassignment of items between layers."""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand


class MoveToLayerCommand(QUndoCommand):
    """Move one or more items to a target layer (undoable).

    Records each item's previous `_layer_index` and `zValue` so undo restores
    the exact prior state. Redo reassigns the layer index and asks the scene
    to recompute Z so the items end up on top of the target layer's band.
    """

    def __init__(
        self,
        scene,
        items: list,
        target_layer_index: int,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._items = list(items)
        self._target = target_layer_index
        # (old_layer_index, old_z) per item
        self._old_state: list[tuple[int, float]] = [
            (getattr(it, '_layer_index', 0), it.zValue()) for it in self._items
        ]
        self._new_z: list[float] | None = None
        n = len(self._items)
        self.setText(f"Move {n} item{'s' if n != 1 else ''} to layer")

    def redo(self) -> None:
        if self._new_z is None:
            for it in self._items:
                it._layer_index = self._target
            self._new_z = [self._scene.assign_item_z(it) for it in self._items]
        else:
            for it, z in zip(self._items, self._new_z):
                it._layer_index = self._target
                it.setZValue(z)

    def undo(self) -> None:
        for it, (old_layer, old_z) in zip(self._items, self._old_state):
            it._layer_index = old_layer
            it.setZValue(old_z)
