"""Transform commands — undoable rotation and flip operations on components."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand

from diagrammer.items.component_item import ComponentItem
from diagrammer.items.port_item import PortItem


class RotateComponentCommand(QUndoCommand):
    """Rotate a component by a given angle about its center (undoable)."""

    def __init__(
        self,
        item: ComponentItem,
        degrees: float,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._degrees = degrees
        direction = "CCW" if degrees > 0 else "CW"
        self.setText(f"Rotate {item.component_def.name} {direction}")

    def redo(self) -> None:
        self._item.rotate_by(self._degrees)

    def undo(self) -> None:
        self._item.rotate_by(-self._degrees)


class RotateAroundPortCommand(QUndoCommand):
    """Rotate a component around a specific port (undoable).

    The port stays fixed in scene space; the component pivots around it.
    """

    def __init__(
        self,
        item: ComponentItem,
        port: PortItem,
        degrees: float,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._port = port
        self._degrees = degrees
        # Record positions for exact undo
        self._old_pos = QPointF(item.pos())
        self._old_angle = item.rotation_angle
        direction = "CW" if degrees < 0 else "CCW"
        self.setText(f"Rotate {item.component_def.name} {abs(degrees):.0f}\u00b0 {direction} around {port.port_name}")

    def redo(self) -> None:
        self._item.rotate_around_port(self._port, self._degrees)

    def undo(self) -> None:
        # Restore exact state
        self._item._rotation_angle = self._old_angle
        self._item._apply_transform()
        self._item.setPos(self._old_pos)


class FlipComponentCommand(QUndoCommand):
    """Flip a component horizontally or vertically (undoable)."""

    def __init__(
        self,
        item: ComponentItem,
        horizontal: bool,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._horizontal = horizontal
        axis = "Horizontal" if horizontal else "Vertical"
        self.setText(f"Flip {item.component_def.name} {axis}")

    def redo(self) -> None:
        self._toggle()

    def undo(self) -> None:
        self._toggle()  # flipping is its own inverse

    def _toggle(self) -> None:
        if self._horizontal:
            self._item.set_flip_h(not self._item.flip_h)
        else:
            self._item.set_flip_v(not self._item.flip_v)
