"""ShapeItem — simple geometric shapes (rectangle, ellipse, line) with resize handles."""

from __future__ import annotations

import uuid

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

# Default style
DEFAULT_STROKE_COLOR = QColor(50, 50, 50)
DEFAULT_FILL_COLOR = QColor(255, 255, 255, 0)  # transparent
DEFAULT_STROKE_WIDTH = 2.0
SELECTION_PEN_COLOR = QColor(0, 120, 215)
SELECTION_PEN_WIDTH = 1.2
SELECTION_DASH_PATTERN = [4, 3]
HANDLE_SIZE = 8.0
HANDLE_COLOR = QColor(0, 120, 215)
HANDLE_FILL = QColor(255, 255, 255)


class ShapeItem(QGraphicsItem):
    """Base class for simple shape items with customizable stroke, fill, and resize handles.

    Resize handles appear at the 8 bounding-box positions when selected.
    Drag a handle to resize the shape (Illustrator-style).
    Double-click to open a properties dialog.
    """

    def __init__(
        self,
        width: float = 100,
        height: float = 60,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._width = width
        self._height = height
        self._stroke_color = QColor(DEFAULT_STROKE_COLOR)
        self._fill_color = QColor(DEFAULT_FILL_COLOR)
        self._stroke_width = DEFAULT_STROKE_WIDTH

        # Resize handle dragging state
        self._resizing_handle: int | None = None  # handle index 0-7
        self._resize_start_rect: QRectF | None = None
        self._resize_start_mouse: QPointF | None = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(3)

    @property
    def instance_id(self) -> str:
        return self._id

    @property
    def stroke_color(self) -> QColor:
        return self._stroke_color

    @stroke_color.setter
    def stroke_color(self, color: QColor) -> None:
        self._stroke_color = color
        self.update()

    @property
    def fill_color(self) -> QColor:
        return self._fill_color

    @fill_color.setter
    def fill_color(self, color: QColor) -> None:
        self._fill_color = color
        self.update()

    @property
    def stroke_width(self) -> float:
        return self._stroke_width

    @stroke_width.setter
    def stroke_width(self, width: float) -> None:
        self._stroke_width = width
        self.update()

    @property
    def shape_width(self) -> float:
        return self._width

    @property
    def shape_height(self) -> float:
        return self._height

    def resize(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self._width = max(10, width)
        self._height = max(10, height)
        self.update()

    def boundingRect(self) -> QRectF:
        m = max(self._stroke_width, SELECTION_PEN_WIDTH, HANDLE_SIZE) / 2 + 2
        return QRectF(-m, -m, self._width + 2 * m, self._height + 2 * m)

    # -- Handle positions (8 points around the bounding box) --
    # 0=TL, 1=TC, 2=TR, 3=ML, 4=MR, 5=BL, 6=BC, 7=BR

    def _handle_positions(self) -> list[QPointF]:
        w, h = self._width, self._height
        return [
            QPointF(0, 0),       # 0: top-left
            QPointF(w / 2, 0),   # 1: top-center
            QPointF(w, 0),       # 2: top-right
            QPointF(0, h / 2),   # 3: mid-left
            QPointF(w, h / 2),   # 4: mid-right
            QPointF(0, h),       # 5: bottom-left
            QPointF(w / 2, h),   # 6: bottom-center
            QPointF(w, h),       # 7: bottom-right
        ]

    def _handle_at(self, local_pos: QPointF) -> int | None:
        """Return the handle index at the given local position, or None."""
        hs = HANDLE_SIZE
        for i, hp in enumerate(self._handle_positions()):
            if abs(local_pos.x() - hp.x()) < hs and abs(local_pos.y() - hp.y()) < hs:
                return i
        return None

    def _draw_selection(self, painter: QPainter) -> None:
        if not self.isSelected():
            return
        # Dashed selection rect
        pen = QPen(SELECTION_PEN_COLOR, SELECTION_PEN_WIDTH)
        pen.setDashPattern(SELECTION_DASH_PATTERN)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(0, 0, self._width, self._height))
        # Resize handles
        painter.setPen(QPen(HANDLE_COLOR, 1.0))
        painter.setBrush(QBrush(HANDLE_FILL))
        hs = HANDLE_SIZE
        for hp in self._handle_positions():
            painter.drawRect(QRectF(hp.x() - hs / 2, hp.y() - hs / 2, hs, hs))

    # -- Resize interaction --

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            hi = self._handle_at(event.pos())
            if hi is not None:
                self._resizing_handle = hi
                self._resize_start_rect = QRectF(0, 0, self._width, self._height)
                self._resize_start_mouse = event.scenePos()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing_handle is not None:
            delta = event.scenePos() - self._resize_start_mouse
            r = self._resize_start_rect
            hi = self._resizing_handle
            new_x, new_y = self.pos().x(), self.pos().y()
            new_w, new_h = r.width(), r.height()

            # Adjust based on which handle is being dragged
            if hi in (0, 3, 5):  # left handles
                new_w = max(10, r.width() - delta.x())
                new_x = self.pos().x() + (r.width() - new_w)
            if hi in (2, 4, 7):  # right handles
                new_w = max(10, r.width() + delta.x())
            if hi in (0, 1, 2):  # top handles
                new_h = max(10, r.height() - delta.y())
                new_y = self.pos().y() + (r.height() - new_h)
            if hi in (5, 6, 7):  # bottom handles
                new_h = max(10, r.height() + delta.y())

            self.prepareGeometryChange()
            self._width = new_w
            self._height = new_h
            # Move position to account for top/left handle drags
            self._skip_snap_once = True
            self.setPos(new_x, new_y)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing_handle is not None:
            self._resizing_handle = None
            self._resize_start_rect = None
            self._resize_start_mouse = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Open properties dialog on double-click."""
        if event.button() == Qt.MouseButton.LeftButton:
            from diagrammer.panels.shape_dialog import ShapePropertiesDialog
            dlg = ShapePropertiesDialog(self)
            if dlg.exec() == ShapePropertiesDialog.DialogCode.Accepted:
                dlg.apply()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    _skip_snap_once = False

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            if self._skip_snap_once:
                self._skip_snap_once = False
                return value
            from diagrammer.canvas.grid import snap_to_grid
            views = self.scene().views()
            if views and getattr(views[0], '_snap_enabled', True):
                return snap_to_grid(value, views[0].grid_spacing)
        return super().itemChange(change, value)


class RectangleItem(ShapeItem):
    """A simple rectangle shape."""

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.setPen(QPen(self._stroke_color, self._stroke_width))
        painter.setBrush(QBrush(self._fill_color))
        painter.drawRect(QRectF(0, 0, self._width, self._height))
        self._draw_selection(painter)


class EllipseItem(ShapeItem):
    """A simple ellipse shape."""

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.setPen(QPen(self._stroke_color, self._stroke_width))
        painter.setBrush(QBrush(self._fill_color))
        painter.drawEllipse(QRectF(0, 0, self._width, self._height))
        self._draw_selection(painter)


class LineItem(QGraphicsItem):
    """A simple straight line with customizable stroke. Double-click to edit properties."""

    def __init__(
        self,
        start: QPointF | None = None,
        end: QPointF | None = None,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._start = start or QPointF(0, 0)
        self._end = end or QPointF(100, 0)
        self._stroke_color = QColor(DEFAULT_STROKE_COLOR)
        self._stroke_width = DEFAULT_STROKE_WIDTH

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(3)

    @property
    def instance_id(self) -> str:
        return self._id

    @property
    def stroke_color(self) -> QColor:
        return self._stroke_color

    @stroke_color.setter
    def stroke_color(self, color: QColor) -> None:
        self._stroke_color = color
        self.update()

    @property
    def stroke_width(self) -> float:
        return self._stroke_width

    @stroke_width.setter
    def stroke_width(self, width: float) -> None:
        self._stroke_width = width
        self.update()

    @property
    def line_start(self) -> QPointF:
        return self._start

    @property
    def line_end(self) -> QPointF:
        return self._end

    def set_endpoints(self, start: QPointF, end: QPointF) -> None:
        self.prepareGeometryChange()
        self._start = QPointF(start)
        self._end = QPointF(end)
        self.update()

    def boundingRect(self) -> QRectF:
        m = max(self._stroke_width, HANDLE_SIZE) / 2 + 2
        x1, y1 = self._start.x(), self._start.y()
        x2, y2 = self._end.x(), self._end.y()
        return QRectF(
            min(x1, x2) - m, min(y1, y2) - m,
            abs(x2 - x1) + 2 * m, abs(y2 - y1) + 2 * m,
        )

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        color = SELECTION_PEN_COLOR if self.isSelected() else self._stroke_color
        painter.setPen(QPen(color, self._stroke_width, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawLine(QLineF(self._start, self._end))

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            from diagrammer.panels.shape_dialog import ShapePropertiesDialog
            dlg = ShapePropertiesDialog(self)
            if dlg.exec() == ShapePropertiesDialog.DialogCode.Accepted:
                dlg.apply()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            from diagrammer.canvas.grid import snap_to_grid
            views = self.scene().views()
            if views and getattr(views[0], '_snap_enabled', True):
                return snap_to_grid(value, views[0].grid_spacing)
        return super().itemChange(change, value)
