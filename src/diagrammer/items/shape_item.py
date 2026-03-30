"""ShapeItem — simple geometric shapes (rectangle, ellipse, line) with resize handles.

All shapes support:
- Stroke: color (with alpha), width, dash pattern (solid/dashed/dotted), cap style
- Fill: color with alpha (rectangle, ellipse only)
- Corner radius (rectangle only)
- Arrowheads (line only): none, forward, backward, both
"""

from __future__ import annotations

import math
import uuid

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
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

# Dash pattern presets (in multiples of stroke width)
DASH_PATTERNS = {
    "solid": [],
    "dashed": [5, 3],
    "dotted": [1, 2],
    "dash-dot": [5, 2, 1, 2],
}

# Arrowhead presets
ARROW_NONE = "none"
ARROW_FORWARD = "forward"    # arrowhead at end
ARROW_BACKWARD = "backward"  # arrowhead at start
ARROW_BOTH = "both"          # arrowheads at both ends


class ShapeItem(QGraphicsItem):
    """Base class for simple shape items with customizable stroke, fill, and resize handles."""

    def __init__(
        self,
        width: float = 100,
        height: float = 60,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._group_id: str | None = None
        self._group_ids: list[str] = []
        self._width = width
        self._height = height
        self._stroke_color = QColor(DEFAULT_STROKE_COLOR)
        self._fill_color = QColor(DEFAULT_FILL_COLOR)
        self._stroke_width = DEFAULT_STROKE_WIDTH
        self._dash_style = "solid"
        self._corner_radius = 0.0

        # Resize handle dragging state
        self._resizing_handle: int | None = None
        self._resize_start_rect: QRectF | None = None
        self._resize_start_mouse: QPointF | None = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(3)

    # =====================================================================
    # Properties
    # =====================================================================

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
    def dash_style(self) -> str:
        return self._dash_style

    @dash_style.setter
    def dash_style(self, style: str) -> None:
        self._dash_style = style
        self.update()

    @property
    def corner_radius(self) -> float:
        return self._corner_radius

    @corner_radius.setter
    def corner_radius(self, radius: float) -> None:
        self._corner_radius = radius
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

    def _make_pen(self) -> QPen:
        """Create a QPen with current stroke settings (color, width, dash, cap)."""
        pen = QPen(self._stroke_color, self._stroke_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pattern = DASH_PATTERNS.get(self._dash_style, [])
        if pattern:
            pen.setDashPattern(pattern)
        return pen

    def boundingRect(self) -> QRectF:
        m = max(self._stroke_width, SELECTION_PEN_WIDTH, HANDLE_SIZE) / 2 + 2
        return QRectF(-m, -m, self._width + 2 * m, self._height + 2 * m)

    # -- Handle positions (8 points around the bounding box) --
    # 0=TL, 1=TC, 2=TR, 3=ML, 4=MR, 5=BL, 6=BC, 7=BR

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

    # -- Resize interaction --

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

            # Left handles: shrink width, shift position right
            if hi in (0, 3, 5):
                new_w = max(10, r.width() - delta.x())
                new_x = start_pos.x() + (r.width() - new_w)
            # Right handles: grow width
            if hi in (2, 4, 7):
                new_w = max(10, r.width() + delta.x())
            # Top handles: shrink height, shift position down
            if hi in (0, 1, 2):
                new_h = max(10, r.height() - delta.y())
                new_y = start_pos.y() + (r.height() - new_h)
            # Bottom handles: grow height
            if hi in (5, 6, 7):
                new_h = max(10, r.height() + delta.y())

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


class RectangleItem(ShapeItem):
    """A rectangle with optional corner radius."""

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.setPen(self._make_pen())
        painter.setBrush(QBrush(self._fill_color))
        r = self._corner_radius
        rect = QRectF(0, 0, self._width, self._height)
        if r > 0:
            painter.drawRoundedRect(rect, r, r)
        else:
            painter.drawRect(rect)
        self._draw_selection(painter)


class EllipseItem(ShapeItem):
    """A simple ellipse shape."""

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.setPen(self._make_pen())
        painter.setBrush(QBrush(self._fill_color))
        painter.drawEllipse(QRectF(0, 0, self._width, self._height))
        self._draw_selection(painter)


class LineItem(QGraphicsItem):
    """A straight line with optional arrowheads and dash patterns."""

    def __init__(
        self,
        start: QPointF | None = None,
        end: QPointF | None = None,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._group_id: str | None = None
        self._group_ids: list[str] = []
        self._start = start or QPointF(0, 0)
        self._end = end or QPointF(100, 0)
        self._stroke_color = QColor(DEFAULT_STROKE_COLOR)
        self._stroke_width = DEFAULT_STROKE_WIDTH
        self._dash_style = "solid"
        self._cap_style = "round"   # "round" or "square"
        self._arrow_style = ARROW_NONE

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self._skip_snap = False
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
    def dash_style(self) -> str:
        return self._dash_style

    @dash_style.setter
    def dash_style(self, style: str) -> None:
        self._dash_style = style
        self.update()

    @property
    def cap_style(self) -> str:
        return self._cap_style

    @cap_style.setter
    def cap_style(self, style: str) -> None:
        self._cap_style = style
        self.update()

    @property
    def arrow_style(self) -> str:
        return self._arrow_style

    @arrow_style.setter
    def arrow_style(self, style: str) -> None:
        self._arrow_style = style
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

    def _make_pen(self) -> QPen:
        cap = (Qt.PenCapStyle.SquareCap if self._cap_style == "square"
               else Qt.PenCapStyle.RoundCap)
        pen = QPen(self._stroke_color, self._stroke_width, Qt.PenStyle.SolidLine, cap)
        pattern = DASH_PATTERNS.get(self._dash_style, [])
        if pattern:
            pen.setDashPattern(pattern)
        return pen

    def boundingRect(self) -> QRectF:
        m = max(self._stroke_width, HANDLE_SIZE) / 2 + 10  # extra for arrowheads
        x1, y1 = self._start.x(), self._start.y()
        x2, y2 = self._end.x(), self._end.y()
        return QRectF(
            min(x1, x2) - m, min(y1, y2) - m,
            abs(x2 - x1) + 2 * m, abs(y2 - y1) + 2 * m,
        )

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        color = (SELECTION_PEN_COLOR if (self.isSelected() and not self._group_id)
                 else self._stroke_color)
        pen = self._make_pen()
        pen.setColor(color)
        painter.setPen(pen)
        painter.drawLine(QLineF(self._start, self._end))

        # Arrowheads
        if self._arrow_style != ARROW_NONE:
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(color, 1.0))
            if self._arrow_style in (ARROW_FORWARD, ARROW_BOTH):
                self._draw_arrowhead(painter, self._start, self._end)
            if self._arrow_style in (ARROW_BACKWARD, ARROW_BOTH):
                self._draw_arrowhead(painter, self._end, self._start)

        # Endpoint handles when selected
        if self.isSelected() and not self._group_id:
            hs = HANDLE_SIZE
            painter.setPen(QPen(HANDLE_COLOR, 1.0))
            painter.setBrush(QBrush(HANDLE_FILL))
            for pt in (self._start, self._end):
                painter.drawEllipse(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs))

    # -- Endpoint drag --

    _dragging_endpoint: int | None = None  # 0=start, 1=end
    _endpoint_drag_start_mouse: QPointF | None = None
    _endpoint_drag_start_pt: QPointF | None = None

    def _endpoint_at(self, local_pos: QPointF) -> int | None:
        """Return 0 for start handle, 1 for end handle, or None."""
        hs = HANDLE_SIZE + 4  # generous hit area
        if (abs(local_pos.x() - self._start.x()) < hs and
                abs(local_pos.y() - self._start.y()) < hs):
            return 0
        if (abs(local_pos.x() - self._end.x()) < hs and
                abs(local_pos.y() - self._end.y()) < hs):
            return 1
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            ep = self._endpoint_at(event.pos())
            if ep is not None:
                self._dragging_endpoint = ep
                self._endpoint_drag_start_mouse = event.scenePos()
                self._endpoint_drag_start_pt = QPointF(
                    self._start if ep == 0 else self._end
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging_endpoint is not None:
            delta = event.scenePos() - self._endpoint_drag_start_mouse
            new_pt = self._endpoint_drag_start_pt + delta
            # Snap endpoint to grid
            from diagrammer.canvas.grid import snap_to_grid
            views = self.scene().views() if self.scene() else []
            if views and getattr(views[0], '_snap_enabled', True):
                # Convert to scene coords, snap, convert back
                scene_pt = self.mapToScene(new_pt)
                snapped = snap_to_grid(scene_pt, views[0].grid_spacing)
                new_pt = self.mapFromScene(snapped)
            self.prepareGeometryChange()
            if self._dragging_endpoint == 0:
                self._start = new_pt
            else:
                self._end = new_pt
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging_endpoint is not None:
            self._dragging_endpoint = None
            self._endpoint_drag_start_mouse = None
            self._endpoint_drag_start_pt = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _draw_arrowhead(self, painter: QPainter, tail: QPointF, tip: QPointF) -> None:
        """Draw a filled arrowhead at tip, pointing from tail to tip."""
        dx = tip.x() - tail.x()
        dy = tip.y() - tail.y()
        length = max(math.hypot(dx, dy), 1e-9)
        ux, uy = dx / length, dy / length  # unit vector along line

        arrow_size = max(self._stroke_width * 3, 8.0)
        # Perpendicular
        px, py = -uy, ux

        base = QPointF(tip.x() - ux * arrow_size, tip.y() - uy * arrow_size)
        left = QPointF(base.x() + px * arrow_size * 0.4, base.y() + py * arrow_size * 0.4)
        right = QPointF(base.x() - px * arrow_size * 0.4, base.y() - py * arrow_size * 0.4)

        painter.drawPolygon(QPolygonF([tip, left, right]))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            if getattr(self, '_skip_snap', False):
                return value
            from diagrammer.canvas.grid import snap_to_grid
            views = self.scene().views()
            if views and getattr(views[0], '_snap_enabled', True):
                return snap_to_grid(value, views[0].grid_spacing)
        return super().itemChange(change, value)
