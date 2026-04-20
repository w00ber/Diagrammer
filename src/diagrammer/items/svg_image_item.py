"""SvgImageItem — an arbitrary SVG image placed on the canvas with free-form resize.

Supports:
- 8-point resize handles (corners + edge midpoints)
- Shift+corner drag for proportional (aspect-locked) scaling
- Edge handles for single-axis scaling
- Grid snapping (consistent with other items)
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

SELECTION_PEN_COLOR = QColor(0, 120, 215)
SELECTION_PEN_WIDTH = 1.2
SELECTION_DASH_PATTERN = [4, 3]
HANDLE_SIZE = 8.0
HANDLE_COLOR = QColor(0, 120, 215)
HANDLE_FILL = QColor(255, 255, 255)

MIN_SIZE = 10.0


class SvgImageItem(QGraphicsItem):
    """An arbitrary SVG rendered into a resizable rectangle on the canvas."""

    def __init__(
        self,
        svg_data: bytes,
        width: float | None = None,
        height: float | None = None,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._group_id: str | None = None
        self._group_ids: list[str] = []
        self._svg_data = svg_data

        self._renderer = QSvgRenderer(svg_data)

        # Determine initial size from SVG viewBox, falling back to defaultSize
        default = self._renderer.defaultSize()
        self._native_width = float(default.width()) if default.width() > 0 else 100.0
        self._native_height = float(default.height()) if default.height() > 0 else 100.0

        self._width = width if width is not None else self._native_width
        self._height = height if height is not None else self._native_height

        # Resize handle dragging state
        self._resizing_handle: int | None = None
        self._resize_start_rect: QRectF | None = None
        self._resize_start_mouse: QPointF | None = None
        self._resize_start_pos: QPointF | None = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(4)  # between shapes (3) and components (5)

    # =====================================================================
    # Properties
    # =====================================================================

    @property
    def instance_id(self) -> str:
        return self._id

    @property
    def svg_data(self) -> bytes:
        return self._svg_data

    @property
    def image_width(self) -> float:
        return self._width

    @property
    def image_height(self) -> float:
        return self._height

    def resize(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self._width = max(MIN_SIZE, width)
        self._height = max(MIN_SIZE, height)
        self.update()

    # =====================================================================
    # Painting
    # =====================================================================

    def boundingRect(self) -> QRectF:
        m = max(SELECTION_PEN_WIDTH, HANDLE_SIZE) / 2 + 2
        return QRectF(-m, -m, self._width + 2 * m, self._height + 2 * m)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        # Render the SVG into the current rect
        target = QRectF(0, 0, self._width, self._height)
        self._renderer.render(painter, target)
        self._draw_selection(painter)

    # =====================================================================
    # Selection + resize handles
    # =====================================================================

    # Handle indices: 0=TL, 1=TC, 2=TR, 3=ML, 4=MR, 5=BL, 6=BC, 7=BR

    def _handle_positions(self) -> list[QPointF]:
        w, h = self._width, self._height
        return [
            QPointF(0, 0), QPointF(w / 2, 0), QPointF(w, 0),
            QPointF(0, h / 2), QPointF(w, h / 2),
            QPointF(0, h), QPointF(w / 2, h), QPointF(w, h),
        ]

    def _handle_at(self, local_pos: QPointF) -> int | None:
        hs = HANDLE_SIZE
        for i, hp in enumerate(self._handle_positions()):
            if abs(local_pos.x() - hp.x()) < hs and abs(local_pos.y() - hp.y()) < hs:
                return i
        return None

    def _is_corner_handle(self, hi: int) -> bool:
        return hi in (0, 2, 5, 7)

    def _draw_selection(self, painter: QPainter) -> None:
        if not self.isSelected() or self._group_id:
            return
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

    # =====================================================================
    # Resize interaction
    # =====================================================================

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            hi = self._handle_at(event.pos())
            if hi is not None:
                self._resizing_handle = hi
                self._resize_start_rect = QRectF(0, 0, self._width, self._height)
                self._resize_start_mouse = event.scenePos()
                self._resize_start_pos = QPointF(self.pos())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing_handle is not None:
            delta = event.scenePos() - self._resize_start_mouse
            r = self._resize_start_rect
            hi = self._resizing_handle
            start_pos = self._resize_start_pos
            new_x, new_y = start_pos.x(), start_pos.y()
            new_w, new_h = r.width(), r.height()

            # Compute raw resize based on handle
            # Left handles: shrink width, shift position right
            if hi in (0, 3, 5):
                new_w = max(MIN_SIZE, r.width() - delta.x())
                new_x = start_pos.x() + (r.width() - new_w)
            # Right handles: grow width
            if hi in (2, 4, 7):
                new_w = max(MIN_SIZE, r.width() + delta.x())
            # Top handles: shrink height, shift position down
            if hi in (0, 1, 2):
                new_h = max(MIN_SIZE, r.height() - delta.y())
                new_y = start_pos.y() + (r.height() - new_h)
            # Bottom handles: grow height
            if hi in (5, 6, 7):
                new_h = max(MIN_SIZE, r.height() + delta.y())

            # Shift+corner = proportional constraint
            if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and self._is_corner_handle(hi):
                aspect = r.width() / r.height() if r.height() > 0 else 1.0
                # Use the axis with the larger proportional change
                scale_w = new_w / r.width() if r.width() > 0 else 1.0
                scale_h = new_h / r.height() if r.height() > 0 else 1.0
                # Pick the dominant axis
                if abs(scale_w - 1.0) >= abs(scale_h - 1.0):
                    new_h = max(MIN_SIZE, new_w / aspect)
                else:
                    new_w = max(MIN_SIZE, new_h * aspect)

                # Recompute position for constrained size
                if hi in (0, 5):  # left handles
                    new_x = start_pos.x() + (r.width() - new_w)
                if hi in (0, 2):  # top handles
                    new_y = start_pos.y() + (r.height() - new_h)

            self.prepareGeometryChange()
            self._width = new_w
            self._height = new_h
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
            self._resize_start_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    _skip_snap_once = False

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            if self._skip_snap_once:
                self._skip_snap_once = False
                return value
            if getattr(self, '_skip_snap', False):
                return value
            from diagrammer.canvas.grid import snap_to_grid
            views = self.scene().views()
            if views and getattr(views[0], '_snap_enabled', True):
                return snap_to_grid(value, views[0].grid_spacing)
        return super().itemChange(change, value)
