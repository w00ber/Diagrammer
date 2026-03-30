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


class RotateItemCommand(QUndoCommand):
    """Rotate any QGraphicsItem by a given angle about its center (undoable).

    Works with AnnotationItem, ShapeItem, LineItem, etc. — any item
    that supports Qt's built-in setRotation(). Sets the transform origin
    to the bounding rect center before rotating.
    """

    def __init__(self, item, degrees: float, parent: QUndoCommand | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self._degrees = degrees
        self.setText(f"Rotate {type(item).__name__} {degrees:.0f}\u00b0")

    def _ensure_center_origin(self) -> None:
        br = self._item.boundingRect()
        self._item.setTransformOriginPoint(br.center())

    def redo(self) -> None:
        self._ensure_center_origin()
        self._item.setRotation(self._item.rotation() + self._degrees)

    def undo(self) -> None:
        self._ensure_center_origin()
        self._item.setRotation(self._item.rotation() - self._degrees)


class FlipItemCommand(QUndoCommand):
    """Flip any QGraphicsItem horizontally or vertically (undoable).

    Uses QGraphicsItem.setTransform() to apply a scale(-1, 1) or (1, -1).
    """

    def __init__(self, item, horizontal: bool, parent: QUndoCommand | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self._horizontal = horizontal
        axis = "H" if horizontal else "V"
        self.setText(f"Flip {type(item).__name__} {axis}")

    def redo(self) -> None:
        self._apply()

    def undo(self) -> None:
        self._apply()  # flip is its own inverse

    def _apply(self) -> None:
        from PySide6.QtGui import QTransform
        t = self._item.transform()
        br = self._item.boundingRect()
        cx, cy = br.center().x(), br.center().y()
        flip = QTransform()
        flip.translate(cx, cy)
        if self._horizontal:
            flip.scale(-1, 1)
        else:
            flip.scale(1, -1)
        flip.translate(-cx, -cy)
        self._item.setTransform(t * flip)


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
