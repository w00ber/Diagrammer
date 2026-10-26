"""SvgImageItem — an arbitrary SVG image placed on the canvas with free-form resize.

Supports:
- 8-point resize handles (corners + edge midpoints)
- Shift+corner drag for proportional (aspect-locked) scaling
- Edge handles for single-axis scaling
- Rotation / flip through the same intrinsic-transform model as ShapeItem
- Grid snapping (consistent with other items)

Sizing is content-aware. ``QSvgRenderer.render(painter, target)`` does *not*
clip to ``target``: anything the document draws outside its own viewBox lands
outside the item's rect, where it is visible but unclickable (and leaves
repaint droppings, since Qt only repairs ``boundingRect``). Exports from other
apps do this routinely — a cropped artboard keeps the full artwork and shrinks
the viewBox around it. So the item measures the painted content and
(a) adopts it as the source rect when the overflow is gross, and
(b) always folds it into ``boundingRect``, which is also the default
``shape()`` and therefore the hit area.
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

SELECTION_PEN_COLOR = QColor(0, 120, 215)
SELECTION_PEN_WIDTH = 1.2
SELECTION_DASH_PATTERN = [4, 3]
HANDLE_SIZE = 8.0
HANDLE_COLOR = QColor(0, 120, 215)
HANDLE_FILL = QColor(255, 255, 255)

MIN_SIZE = 10.0

# How far painted content may spill past the viewBox before the item resizes
# itself around it, as a fraction of the viewBox dimension. A stroke sitting on
# the viewBox edge spills half its width, which is a rendering detail and
# shouldn't move the selection box; a cropped artboard spills whole multiples.
OVERFLOW_TOLERANCE = 0.10


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
        self._measure_source()

        self._width = width if width is not None else self._native_width
        self._height = height if height is not None else self._native_height

        # Persistent intrinsic transform (matches ShapeItem's model).
        self._rotation_angle = 0.0
        self._flip_h = False
        self._flip_v = False

        # Resize handle dragging state
        self._resizing_handle: int | None = None
        self._resize_start_rect: QRectF | None = None
        self._resize_start_mouse: QPointF | None = None
        self._resize_start_pos: QPointF | None = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(4)  # seed Z; the scene re-stacks by creation order

    # =====================================================================
    # Source geometry
    # =====================================================================

    def _measure_source(self) -> None:
        """Work out the SVG user-space rect the item's rect stands for.

        ``_source_rect`` is the viewBox, widened to the painted content when
        the content spills past it by more than ``OVERFLOW_TOLERANCE``.
        ``_content_rect`` keeps the raw painted bounds for ``boundingRect``.
        """
        view_box = QRectF(self._renderer.viewBoxF())
        default = self._renderer.defaultSize()

        if view_box.width() <= 0 or view_box.height() <= 0:
            w = float(default.width()) if default.width() > 0 else 100.0
            h = float(default.height()) if default.height() > 0 else 100.0
            view_box = QRectF(0.0, 0.0, w, h)

        try:
            content = QRectF(self._renderer.boundsOnElement(""))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            content = QRectF()
        if content.isEmpty():
            content = QRectF(view_box)

        self._content_rect = QRectF(content)

        # Gross overflow only — see OVERFLOW_TOLERANCE.
        tol_x = view_box.width() * OVERFLOW_TOLERANCE
        tol_y = view_box.height() * OVERFLOW_TOLERANCE
        gross = (
            content.left() < view_box.left() - tol_x
            or content.top() < view_box.top() - tol_y
            or content.right() > view_box.right() + tol_x
            or content.bottom() > view_box.bottom() + tol_y
        )
        self._view_box = view_box
        self._source_rect = view_box.united(content) if gross else QRectF(view_box)

        # defaultSize is the viewBox measured in device units; carry that
        # scale onto the (possibly widened) source rect.
        kx = (float(default.width()) / view_box.width()
              if default.width() > 0 else 1.0)
        ky = (float(default.height()) / view_box.height()
              if default.height() > 0 else 1.0)
        self._native_width = max(MIN_SIZE, self._source_rect.width() * kx)
        self._native_height = max(MIN_SIZE, self._source_rect.height() * ky)

    @property
    def source_rect(self) -> QRectF:
        """The SVG user-space rect that maps onto the item's rect."""
        return QRectF(self._source_rect)

    def _source_scale(self) -> tuple[float, float]:
        sx = self._width / self._source_rect.width() if self._source_rect.width() else 1.0
        sy = self._height / self._source_rect.height() if self._source_rect.height() else 1.0
        return sx, sy

    def _painted_rect(self) -> QRectF:
        """Painted content in item-local coordinates."""
        sx, sy = self._source_scale()
        c = self._content_rect
        return QRectF(
            (c.left() - self._source_rect.left()) * sx,
            (c.top() - self._source_rect.top()) * sy,
            c.width() * sx,
            c.height() * sy,
        )

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

    @property
    def native_width(self) -> float:
        return self._native_width

    @property
    def native_height(self) -> float:
        return self._native_height

    @property
    def rotation_angle(self) -> float:
        return self._rotation_angle

    @property
    def flip_h(self) -> bool:
        return self._flip_h

    @property
    def flip_v(self) -> bool:
        return self._flip_v

    def resize(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self._width = max(MIN_SIZE, width)
        self._height = max(MIN_SIZE, height)
        self._apply_transform()
        self.update()

    # =====================================================================
    # Intrinsic transform (rotation + flip), shared model with ShapeItem
    # =====================================================================

    def intrinsic_anchor(self) -> QPointF:
        """Local-coords pivot used for rotation and flip."""
        return QPointF(self._width / 2, self._height / 2)

    def rotate_by(self, degrees: float) -> None:
        self._rotation_angle = (self._rotation_angle + degrees) % 360
        self._apply_transform()

    def set_flip_h(self, flipped: bool) -> None:
        self._flip_h = bool(flipped)
        self._apply_transform()

    def set_flip_v(self, flipped: bool) -> None:
        self._flip_v = bool(flipped)
        self._apply_transform()

    def _apply_transform(self) -> None:
        """Rebuild the item transform from rotation + flip state.

        Pivots around the intrinsic anchor so that point stays put in scene
        space. ``setRotation`` is left at 0 — the angle is baked into
        ``setTransform`` so there is one source of truth.
        """
        if self._rotation_angle == 0.0 and not self._flip_h and not self._flip_v:
            self.setTransform(QTransform())
            return
        cx = self._width / 2
        cy = self._height / 2
        t = QTransform()
        t.translate(cx, cy)
        t.scale(-1.0 if self._flip_h else 1.0, -1.0 if self._flip_v else 1.0)
        t.rotate(self._rotation_angle)
        t.translate(-cx, -cy)
        self.setTransform(t)

    def _to_local_delta(self, scene_delta: QPointF) -> QPointF:
        """Map a scene-space delta into item-local space (rotation/flip only)."""
        t = self.transform()
        inv, ok = t.inverted()
        if not ok:
            return QPointF(scene_delta)
        return QPointF(
            inv.m11() * scene_delta.x() + inv.m21() * scene_delta.y(),
            inv.m12() * scene_delta.x() + inv.m22() * scene_delta.y(),
        )

    def _to_scene_delta(self, local_delta: QPointF) -> QPointF:
        t = self.transform()
        return QPointF(
            t.m11() * local_delta.x() + t.m21() * local_delta.y(),
            t.m12() * local_delta.x() + t.m22() * local_delta.y(),
        )

    # =====================================================================
    # Painting
    # =====================================================================

    def boundingRect(self) -> QRectF:
        m = max(SELECTION_PEN_WIDTH, HANDLE_SIZE) / 2 + 2
        rect = QRectF(0, 0, self._width, self._height)
        # Content that spills past the item rect by less than
        # OVERFLOW_TOLERANCE still has to be inside boundingRect, or Qt
        # neither repaints nor hit-tests it.
        rect = rect.united(self._painted_rect())
        return rect.adjusted(-m, -m, m, m)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        sx, sy = self._source_scale()
        painter.save()
        painter.scale(sx, sy)
        painter.translate(-self._source_rect.left(), -self._source_rect.top())
        # render(painter, viewBox) is the identity mapping in SVG user
        # units, so the painter transform above is the only scaling.
        self._renderer.render(painter, self._view_box)
        painter.restore()
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
            # Handles are drawn in item space, so the drag has to be read
            # there too or a rotated item resizes along the wrong axes.
            delta = self._to_local_delta(event.scenePos() - self._resize_start_mouse)
            r = self._resize_start_rect
            hi = self._resizing_handle
            start_pos = self._resize_start_pos
            off_x = off_y = 0.0
            new_w, new_h = r.width(), r.height()

            # Compute raw resize based on handle
            # Left handles: shrink width, shift position right
            if hi in (0, 3, 5):
                new_w = max(MIN_SIZE, r.width() - delta.x())
                off_x = r.width() - new_w
            # Right handles: grow width
            if hi in (2, 4, 7):
                new_w = max(MIN_SIZE, r.width() + delta.x())
            # Top handles: shrink height, shift position down
            if hi in (0, 1, 2):
                new_h = max(MIN_SIZE, r.height() - delta.y())
                off_y = r.height() - new_h
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
                    off_x = r.width() - new_w
                if hi in (0, 2):  # top handles
                    off_y = r.height() - new_h

            self.prepareGeometryChange()
            self._width = new_w
            self._height = new_h
            self._apply_transform()
            scene_off = self._to_scene_delta(QPointF(off_x, off_y))
            self._skip_snap_once = True
            self.setPos(start_pos + scene_off)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing_handle is not None:
            old_rect = self._resize_start_rect
            old_pos = self._resize_start_pos
            self._resizing_handle = None
            self._resize_start_rect = None
            self._resize_start_mouse = None
            self._resize_start_pos = None
            new_pos = QPointF(self.pos())
            changed = (
                old_rect is not None
                and (old_rect.width() != self._width
                     or old_rect.height() != self._height
                     or old_pos != new_pos)
            )
            scene = self.scene()
            if changed and scene is not None and hasattr(scene, 'undo_stack'):
                from diagrammer.commands.shape_command import ResizeShapeCommand
                # push() re-runs redo(), which re-applies the geometry the
                # drag already set — a visual no-op.
                scene.undo_stack.push(ResizeShapeCommand(
                    self,
                    old_rect.width(), old_rect.height(), old_pos,
                    self._width, self._height, new_pos,
                ))
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
