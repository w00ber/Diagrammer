"""ChangeStyleCommand — undoable style property change for any scene item."""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand


class ChangeStyleCommand(QUndoCommand):
    """Change a single style property on a scene item (undoable).

    Works with any item that has the named attribute.
    Calls item.update() after applying, and item.update_route()
    for ConnectionItems.
    """

    def __init__(
        self,
        item,
        prop_name: str,
        old_value,
        new_value,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._prop = prop_name
        self._old = old_value
        self._new = new_value
        label = getattr(item, 'instance_id', 'item')[:8]
        self.setText(f"Change {prop_name} on {label}")

    def redo(self) -> None:
        self._apply(self._new)

    def undo(self) -> None:
        self._apply(self._old)

    def _apply(self, value) -> None:
        setattr(self._item, self._prop, value)
        from diagrammer.items.connection_item import ConnectionItem
        if isinstance(self._item, ConnectionItem):
            self._item.update_route()
        self._item.update()
