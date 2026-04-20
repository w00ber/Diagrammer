"""Library Table View — read-only canvas displaying all components in a library category."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from diagrammer.models.component_def import ComponentDef

# Layout constants
CELL_PADDING = 20.0
LABEL_GAP = 6.0
ROW_GAP = 20.0
COL_GAP = 20.0
MIN_CELL_WIDTH = 80.0
TITLE_FONT_SIZE = 16
SUBTITLE_FONT_SIZE = 12
LABEL_FONT_SIZE = 9


# ---------------------------------------------------------------------------
# Lightweight component renderer (artwork only, no interaction)
# ---------------------------------------------------------------------------

class _TableComponentItem(QGraphicsItem):
    """Renders a component's artwork at its native viewbox size.  Read-only."""

    def __init__(self, comp_def: ComponentDef, parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self._width = comp_def.width
        self._height = comp_def.height
        from diagrammer.items.component_item import ComponentItem
        svg_bytes = ComponentItem._prepare_svg_bytes(comp_def.svg_path)
        self._renderer = QSvgRenderer(svg_bytes)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._width, self._height)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: QWidget | None = None) -> None:
        self._renderer.render(painter, self.boundingRect())


# ---------------------------------------------------------------------------
# Read-only view with zoom / pan
# ---------------------------------------------------------------------------

class LibraryTableView(QGraphicsView):
    """Read-only view for browsing a library table.

    Zoom/pan interactions match the main diagram view:
    - Two-finger scroll → pan
    - Pinch gesture → zoom at centre of gesture
    - Mouse scroll wheel → zoom at cursor
    - Middle-click drag → pan
    """

    _ZOOM_FACTOR = 1.15
    _ZOOM_MAX = 20.0

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None):
        super().__init__(scene, parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAcceptDrops(False)
        self.setBackgroundBrush(QColor(255, 255, 255))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        # Accept native gestures (pinch-to-zoom on trackpad)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.grabGesture(Qt.GestureType.PinchGesture)

        # Zoom-window state
        self._zoom_window_mode = False
        self._zoom_rect_start: QPointF | None = None
        self._zoom_rect_item: QGraphicsRectItem | None = None
        self._min_scale: float = 0.01  # updated by fit_all
        # Pan state (middle-click or in non-zoom mode)
        self._panning = False
        self._pan_start = QPointF()

    # -- Zoom helpers --

    def _current_scale(self) -> float:
        return self.transform().m11()

    def _clamp_zoom(self) -> None:
        """Enforce minimum zoom level (0.8x of fit-all scale)."""
        if self._current_scale() < self._min_scale:
            ratio = self._min_scale / self._current_scale()
            self.scale(ratio, ratio)

    def zoom_at(self, factor: float, scene_pos: QPointF) -> None:
        """Zoom by *factor*, keeping *scene_pos* visually stationary."""
        new_scale = self._current_scale() * factor
        if new_scale > self._ZOOM_MAX:
            return
        if new_scale < self._min_scale:
            factor = self._min_scale / self._current_scale()
        view_point = self.mapFromScene(scene_pos)
        self.scale(factor, factor)
        new_scene_pos = self.mapToScene(view_point)
        delta = new_scene_pos - scene_pos
        self.translate(delta.x(), delta.y())

    def zoom_centered(self, factor: float) -> None:
        """Zoom around the viewport centre."""
        center = self.mapToScene(self.viewport().rect().center())
        self.zoom_at(factor, center)

    def fit_all(self) -> None:
        """Fit the entire scene content into the viewport."""
        scene = self.scene()
        if scene is None:
            return
        rect = scene.itemsBoundingRect()
        if not rect.isNull():
            margin = max(rect.width(), rect.height()) * 0.05
            rect.adjust(-margin, -margin, margin, margin)
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._min_scale = self._current_scale() * 0.8

    # -- Gesture / wheel / pan events (match diagram view) --

    def event(self, ev) -> bool:  # noqa: ANN001
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Type.Gesture:
            return self._handle_gesture(ev)
        return super().event(ev)

    def _handle_gesture(self, ev) -> bool:
        from PySide6.QtWidgets import QPinchGesture
        pinch = ev.gesture(Qt.GestureType.PinchGesture)
        if pinch is not None:
            flags = pinch.changeFlags()
            if flags & QPinchGesture.ChangeFlag.ScaleFactorChanged:
                factor = pinch.scaleFactor()
                center = self.mapToScene(self.mapFromGlobal(pinch.centerPoint().toPoint()))
                self.zoom_at(factor, center)
            return True
        return False

    def wheelEvent(self, event) -> None:  # noqa: ANN001
        # Discrete mouse wheel: zoom toward cursor
        if event.phase() in (Qt.ScrollPhase.NoScrollPhase,):
            angle = event.angleDelta().y()
            if angle == 0:
                return
            factor = self._ZOOM_FACTOR if angle > 0 else 1.0 / self._ZOOM_FACTOR
            cursor_pos = self.mapToScene(event.position().toPoint())
            self.zoom_at(factor, cursor_pos)
            return

        # Trackpad two-finger scroll: pan
        dx = event.pixelDelta().x()
        dy = event.pixelDelta().y()
        if dx == 0 and dy == 0:
            dx = event.angleDelta().x()
            dy = event.angleDelta().y()
        if dx != 0 or dy != 0:
            scale = self._current_scale()
            self.translate(dx / scale, dy / scale)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        # Middle-click pan
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._zoom_window_mode and event.button() == Qt.MouseButton.LeftButton:
            self._zoom_rect_start = self.mapToScene(event.position().toPoint())
            pen = QPen(QColor(0, 120, 212), 1.0, Qt.PenStyle.DashLine)
            self._zoom_rect_item = QGraphicsRectItem()
            self._zoom_rect_item.setPen(pen)
            self._zoom_rect_item.setBrush(QColor(0, 120, 212, 30))
            self.scene().addItem(self._zoom_rect_item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            scale = self._current_scale()
            self.translate(delta.x() / scale, delta.y() / scale)
            event.accept()
            return
        if self._zoom_window_mode and self._zoom_rect_item and self._zoom_rect_start:
            current = self.mapToScene(event.position().toPoint())
            rect = QRectF(self._zoom_rect_start, current).normalized()
            self._zoom_rect_item.setRect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if self._zoom_window_mode and event.button() == Qt.MouseButton.LeftButton:
            if self._zoom_rect_item and self._zoom_rect_start:
                rect = self._zoom_rect_item.rect()
                self.scene().removeItem(self._zoom_rect_item)
                self._zoom_rect_item = None
                click_pos = self._zoom_rect_start
                self._zoom_rect_start = None
                if rect.width() > 5 and rect.height() > 5:
                    self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
                    self._clamp_zoom()
                else:
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        self.zoom_centered(0.5)
                    else:
                        self.zoom_centered(2.0)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- Zoom window mode --

    def set_zoom_window_mode(self, enabled: bool) -> None:
        """Enable or disable zoom-window mode (drag a rectangle to zoom into)."""
        self._zoom_window_mode = enabled
        if enabled:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if self._zoom_rect_item:
                self.scene().removeItem(self._zoom_rect_item)
                self._zoom_rect_item = None
            self._zoom_rect_start = None

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        # Z and A are handled by MainWindow menu actions which route to
        # set_zoom_window_mode() and fit_all() via _active_view().
        if event.key() == Qt.Key.Key_Escape and self._zoom_window_mode:
            # Exit zoom window — also uncheck the menu action if accessible
            self.set_zoom_window_mode(False)
            mw = self.window()
            if hasattr(mw, '_zoom_window_act'):
                mw._zoom_window_act.blockSignals(True)
                mw._zoom_window_act.setChecked(False)
                mw._zoom_window_act.blockSignals(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if self._zoom_window_mode and event.button() == Qt.MouseButton.LeftButton:
            self._zoom_rect_start = self.mapToScene(event.position().toPoint())
            pen = QPen(QColor(0, 120, 212), 1.0, Qt.PenStyle.DashLine)
            self._zoom_rect_item = QGraphicsRectItem()
            self._zoom_rect_item.setPen(pen)
            self._zoom_rect_item.setBrush(QColor(0, 120, 212, 30))
            self.scene().addItem(self._zoom_rect_item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._zoom_window_mode and self._zoom_rect_item and self._zoom_rect_start:
            current = self.mapToScene(event.position().toPoint())
            rect = QRectF(self._zoom_rect_start, current).normalized()
            self._zoom_rect_item.setRect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._zoom_window_mode and event.button() == Qt.MouseButton.LeftButton:
            if self._zoom_rect_item and self._zoom_rect_start:
                rect = self._zoom_rect_item.rect()
                self.scene().removeItem(self._zoom_rect_item)
                self._zoom_rect_item = None
                click_pos = self._zoom_rect_start
                self._zoom_rect_start = None
                if rect.width() > 5 and rect.height() > 5:
                    self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
                    self._clamp_zoom()
                else:
                    # Single click: zoom in (shift+click = zoom out)
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        self.zoom_centered(0.5)
                    else:
                        self.zoom_centered(2.0)
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------

def build_library_table(
    title: str,
    defs_by_category: dict[str, list[ComponentDef]],
    columns: int = 6,
) -> tuple[QGraphicsScene, LibraryTableView]:
    """Build a read-only scene and view showing components in a labelled grid.

    Args:
        title: Display title for the table (shown at top).
        defs_by_category: Mapping of subcategory name → list of ComponentDefs.
        columns: Number of columns in the grid.

    Returns:
        (scene, view) tuple ready to be added as a tab.
    """
    scene = QGraphicsScene()

    # Fonts
    title_font = QFont()
    title_font.setPointSize(TITLE_FONT_SIZE)
    title_font.setBold(True)
    subtitle_font = QFont()
    subtitle_font.setPointSize(SUBTITLE_FONT_SIZE)
    subtitle_font.setBold(True)
    label_font = QFont()
    label_font.setPointSize(LABEL_FONT_SIZE)

    # Frame pen
    frame_pen = QPen(QColor(200, 200, 200))
    frame_pen.setWidthF(0.5)

    y_cursor = 0.0

    # Title
    title_item = QGraphicsSimpleTextItem(_format_category(title))
    title_item.setFont(title_font)
    title_item.setPos(0, y_cursor)
    scene.addItem(title_item)
    y_cursor += title_item.boundingRect().height() + ROW_GAP

    # Lay out each subcategory
    for cat_name in sorted(defs_by_category.keys()):
        defs = defs_by_category[cat_name]
        if not defs:
            continue

        # Subcategory header (skip if there's only one category — it matches the title)
        if len(defs_by_category) > 1:
            sub_item = QGraphicsSimpleTextItem(_format_category(cat_name))
            sub_item.setFont(subtitle_font)
            sub_item.setPos(0, y_cursor)
            scene.addItem(sub_item)
            y_cursor += sub_item.boundingRect().height() + LABEL_GAP

        # Sort components by name
        sorted_defs = sorted(defs, key=lambda d: d.name.lower())

        # Compute cell sizes for each component
        from PySide6.QtGui import QFontMetricsF
        label_metrics = QFontMetricsF(label_font)
        cells: list[dict] = []
        for comp_def in sorted_defs:
            label_text = comp_def.name.replace("_", " ")
            label_w = label_metrics.horizontalAdvance(label_text)
            label_h = label_metrics.height()
            cell_w = max(comp_def.width, label_w, MIN_CELL_WIDTH) + 2 * CELL_PADDING
            cell_h = comp_def.height + label_h + LABEL_GAP + 2 * CELL_PADDING
            cells.append({
                "def": comp_def,
                "label": label_text,
                "label_w": label_w,
                "label_h": label_h,
                "cell_w": cell_w,
                "cell_h": cell_h,
            })

        # Partition into rows
        rows: list[list[dict]] = []
        for i in range(0, len(cells), columns):
            rows.append(cells[i : i + columns])

        # Compute per-column widths (max across all rows)
        col_widths = [0.0] * columns
        for row in rows:
            for ci, cell in enumerate(row):
                col_widths[ci] = max(col_widths[ci], cell["cell_w"])

        # Place cells
        for row in rows:
            row_height = max(c["cell_h"] for c in row)
            x_cursor = 0.0
            for ci, cell in enumerate(row):
                cw = col_widths[ci]
                ch = row_height
                comp_def = cell["def"]

                # Frame rect
                frame = QGraphicsRectItem(x_cursor, y_cursor, cw, ch)
                frame.setPen(frame_pen)
                frame.setBrush(Qt.GlobalColor.transparent)
                scene.addItem(frame)

                # Label (centered at top of cell)
                label_item = QGraphicsSimpleTextItem(cell["label"])
                label_item.setFont(label_font)
                label_x = x_cursor + (cw - cell["label_w"]) / 2
                label_y = y_cursor + CELL_PADDING * 0.5
                label_item.setPos(label_x, label_y)
                scene.addItem(label_item)

                # Component artwork (centered below label)
                comp_item = _TableComponentItem(comp_def)
                comp_x = x_cursor + (cw - comp_def.width) / 2
                comp_y = label_y + cell["label_h"] + LABEL_GAP
                comp_item.setPos(comp_x, comp_y)
                scene.addItem(comp_item)

                x_cursor += cw + COL_GAP

            y_cursor += row_height + ROW_GAP

    # Set scene rect with generous margins so edge items can be panned to centre
    items_rect = scene.itemsBoundingRect()
    if not items_rect.isNull():
        pad = max(items_rect.width(), items_rect.height())
        scene.setSceneRect(items_rect.adjusted(-pad, -pad, pad, pad))

    # Create view — parent the scene to the view so it isn't garbage-collected
    # when the view is reparented into the tab widget.
    view = LibraryTableView(scene)
    scene.setParent(view)
    return scene, view


def _format_category(category: str) -> str:
    """Format a category path for display: 'electrical/simple' → 'electrical / simple'."""
    return category.replace("/", " / ").replace("_", " ")
