"""ChangeSvgStyleCommand — undoable per-element SVG style override."""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand


class ChangeSvgStyleCommand(QUndoCommand):
    """Change a single style property on an SVG sub-element of a component."""

    def __init__(
        self,
        item,
        element_path: str,
        prop_name: str,
        old_value,
        new_value,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._element_path = element_path
        self._prop = prop_name
        self._old = old_value
        self._new = new_value
        label = item.instance_id[:8]
        self.setText(f"Change {prop_name} on {element_path} ({label})")

    def redo(self) -> None:
        self._item.set_element_style(self._element_path, self._prop, self._new)

    def undo(self) -> None:
        self._item.set_element_style(self._element_path, self._prop, self._old)
