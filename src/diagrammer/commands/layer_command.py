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
            self._scene.invalidate_z_bands()

    def undo(self) -> None:
        for it, (old_layer, old_z) in zip(self._items, self._old_state):
            it._layer_index = old_layer
            it.setZValue(old_z)
        self._scene.invalidate_z_bands()


def _invalidate(cmd) -> None:
    """Direct Z writes bypass assign_item_z's band-top cache."""
    scene = getattr(cmd, "_scene", None)
    if scene is None:
        for it in getattr(cmd, "_items", []):
            scene = it.scene()
            if scene is not None:
                break
    if scene is not None and hasattr(scene, "invalidate_z_bands"):
        scene.invalidate_z_bands()


class ChangeZOrderCommand(QUndoCommand):
    """Reassign z-values on a set of items (undoable).

    Used by the Bring-Forward / Send-Backward / Bring-To-Front /
    Send-To-Back actions on MainWindow. One command captures the
    entire selection's old/new z values for one gesture so undo
    restores the exact stacking order.
    """

    def __init__(
        self,
        items_with_z: list[tuple[object, float, float]],
        label: str,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        # Store (item, old_z, new_z) tuples; copy the list defensively
        # so callers can't mutate it post-construction.
        self._states = list(items_with_z)
        self.setText(label)

    def redo(self) -> None:
        for item, _old_z, new_z in self._states:
            item.setZValue(new_z)
        _invalidate(self)

    def undo(self) -> None:
        for item, old_z, _new_z in self._states:
            item.setZValue(old_z)
        _invalidate(self)


class SetLayerStateCommand(QUndoCommand):
    """Set a layer's ``visible`` / ``locked`` flag (undoable).

    These flags used to be mutated in place by the Edit-menu actions and by
    the Layers panel, so hiding a layer — once reachable from a bare "H" —
    looked exactly like losing the diagram, and Ctrl+Z had nothing to
    reverse. Routing both flags through a command keeps the project rule
    that every canvas mutation is undoable.

    ``on_change`` is called after each redo/undo so the caller can refresh
    the panel and re-apply the state to the scene's items.
    """

    def __init__(
        self,
        layer_manager,
        layer_index: int,
        *,
        visible: bool | None = None,
        locked: bool | None = None,
        on_change=None,
        label: str | None = None,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        if visible is None and locked is None:
            raise ValueError("SetLayerStateCommand needs visible= or locked=")
        self._manager = layer_manager
        self._index = layer_index
        self._on_change = on_change
        self._new: dict[str, bool] = {}
        if visible is not None:
            self._new["visible"] = bool(visible)
        if locked is not None:
            self._new["locked"] = bool(locked)
        layer = layer_manager.layers[layer_index]
        self._old = {attr: bool(getattr(layer, attr)) for attr in self._new}
        self.setText(label or self._describe(layer.name))

    def _describe(self, name: str) -> str:
        verbs = []
        if "visible" in self._new:
            verbs.append("Show" if self._new["visible"] else "Hide")
        if "locked" in self._new:
            verbs.append("Lock" if self._new["locked"] else "Unlock")
        return f"{' + '.join(verbs)} layer \"{name}\""

    def _apply(self, state: dict[str, bool]) -> None:
        # The layer can be gone if it was removed after this command was
        # pushed (layer removal is not itself undoable), so don't assume.
        if not (0 <= self._index < len(self._manager.layers)):
            return
        layer = self._manager.layers[self._index]
        for attr, value in state.items():
            setattr(layer, attr, value)
        if self._on_change is not None:
            self._on_change()

    def redo(self) -> None:
        self._apply(self._new)

    def undo(self) -> None:
        self._apply(self._old)
