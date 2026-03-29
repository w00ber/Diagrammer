"""AnnotationItem — editable rich-text label on the diagram canvas.

Double-click to enter edit mode (inline text editor).
Supports basic HTML formatting (bold, italic, font size, color).

Math mode: wrap LaTeX in $...$ delimiters. On finishing edit, math
expressions are rendered as vector SVG via matplotlib and displayed
using QSvgRenderer for resolution-independent quality at any zoom.
The source LaTeX is preserved for re-editing.
"""

from __future__ import annotations

import io
import re
import uuid

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTextCursor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsTextItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

SELECTION_PEN_COLOR = QColor(0, 120, 215)
SELECTION_PEN_WIDTH = 1.5
DEFAULT_FONT_FAMILY = "Helvetica"
DEFAULT_FONT_SIZE = 12.0
DEFAULT_TEXT_COLOR = QColor(0, 0, 0)

# Regex to detect $...$ math (non-greedy)
_MATH_RE = re.compile(r"\$(.+?)\$")

# Common font families grouped by category
FONT_FAMILIES = [
    # Sans-serif
    "Helvetica",
    "Arial",
    "SF Pro",
    "Verdana",
    # Serif
    "Times New Roman",
    "Georgia",
    "Palatino",
    # Monospace
    "Courier New",
    "Menlo",
    "Monaco",
]


def _render_latex_svg(text: str, font_size: float, color: QColor) -> bytes | None:
    """Render a string (with $...$ math) to SVG bytes using matplotlib.

    Matplotlib handles mixed text+math natively: ``$\\alpha$ hello``
    renders the math as glyphs and the rest as text, all as vector paths.

    Returns SVG bytes or None if matplotlib is unavailable or rendering fails.
    """
    try:
        import matplotlib
        matplotlib.use("agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    try:
        fig = plt.figure(figsize=(0.01, 0.01))
        mpl_color = f"#{color.red():02x}{color.green():02x}{color.blue():02x}"
        fig.text(
            0.5, 0.5, text,
            fontsize=font_size,
            color=mpl_color,
            ha="center", va="center",
        )
        buf = io.BytesIO()
        fig.savefig(buf, format="svg", transparent=True,
                    bbox_inches="tight", pad_inches=0.02)
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


class AnnotationItem(QGraphicsTextItem):
    """A rich-text annotation that can be placed and edited on the canvas.

    - Single-click to select (move, delete, etc.)
    - Double-click to enter inline text editing
    - Click away or press Escape to finish editing
    - Supports HTML: bold (Ctrl+B), italic (Ctrl+I), etc.
    - Math: wrap LaTeX in $...$ — rendered as vector SVG on edit finish
    """

    def __init__(
        self,
        text: str = "Text",
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._editing = False
        self._skip_snap = False
        self._source_text: str = text  # plain-text source (with $..$ math)
        self._math_renderer: QSvgRenderer | None = None  # vector math renderer
        self._math_rect: QRectF = QRectF()  # natural size of the math SVG

        # Default font
        font = QFont(DEFAULT_FONT_FAMILY, int(DEFAULT_FONT_SIZE))
        self.setFont(font)
        self.setDefaultTextColor(DEFAULT_TEXT_COLOR)

        # Set initial text
        self.setPlainText(text)
        self._try_render_math()

        # Item flags — movable and selectable, but NOT editable by default
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setZValue(8)  # above wires (6) and components (5)

    # =====================================================================
    # Public properties
    # =====================================================================

    @property
    def instance_id(self) -> str:
        return self._id

    @property
    def source_text(self) -> str:
        """The editable source text (with $..$ math delimiters preserved)."""
        return self._source_text

    @property
    def text_content(self) -> str:
        """The plain text content (source text for editing)."""
        return self._source_text

    @text_content.setter
    def text_content(self, text: str) -> None:
        self._source_text = text
        self.setPlainText(text)
        self._try_render_math()

    @property
    def html_content(self) -> str:
        """The HTML content (for serialization)."""
        return self.toHtml()

    @html_content.setter
    def html_content(self, html: str) -> None:
        self.setHtml(html)

    @property
    def font_family(self) -> str:
        return self.font().family()

    @font_family.setter
    def font_family(self, family: str) -> None:
        f = self.font()
        f.setFamily(family)
        self.setFont(f)
        if self._math_renderer:
            self._try_render_math()

    @property
    def font_size(self) -> float:
        return self.font().pointSizeF()

    @font_size.setter
    def font_size(self, size: float) -> None:
        f = self.font()
        f.setPointSizeF(size)
        self.setFont(f)
        if self._math_renderer:
            self._try_render_math()

    @property
    def font_bold(self) -> bool:
        return self.font().bold()

    @font_bold.setter
    def font_bold(self, bold: bool) -> None:
        f = self.font()
        f.setBold(bold)
        self.setFont(f)

    @property
    def font_italic(self) -> bool:
        return self.font().italic()

    @font_italic.setter
    def font_italic(self, italic: bool) -> None:
        f = self.font()
        f.setItalic(italic)
        self.setFont(f)

    @property
    def text_color(self) -> QColor:
        return self.defaultTextColor()

    @text_color.setter
    def text_color(self, color: QColor) -> None:
        self.setDefaultTextColor(color)
        if self._math_renderer:
            self._try_render_math()

    @property
    def is_editing(self) -> bool:
        return self._editing

    # =====================================================================
    # Math rendering
    # =====================================================================

    def _try_render_math(self) -> None:
        """If source text contains $...$ math, render it as vector SVG.

        Matplotlib handles mixed text+math natively, so we pass the
        entire string (e.g. ``$\\alpha$ hello``) and matplotlib renders
        the math portions as glyphs and the rest as regular text — all
        as vector paths in a single SVG.
        """
        text = self._source_text
        if not _MATH_RE.search(text):
            self._math_renderer = None
            self._math_rect = QRectF()
            return

        svg_bytes = _render_latex_svg(text, self.font_size, self.text_color)
        if svg_bytes is None:
            self._math_renderer = None
            self._math_rect = QRectF()
            return

        renderer = QSvgRenderer(svg_bytes)
        if not renderer.isValid():
            self._math_renderer = None
            self._math_rect = QRectF()
            return

        self._math_renderer = renderer
        # Use the SVG's natural size (in points, from viewBox)
        vs = renderer.viewBoxF()
        self._math_rect = QRectF(0, 0, vs.width(), vs.height())

        # Hide the text content (we'll paint the SVG instead)
        self.document().clear()
        self.prepareGeometryChange()

    # =====================================================================
    # Editing
    # =====================================================================

    def start_editing(self) -> None:
        """Enter inline text editing mode."""
        if self._editing:
            return
        self._editing = True

        # Restore source text for editing (replaces rendered math)
        self._math_renderer = None
        self._math_rect = QRectF()
        self.setPlainText(self._source_text)

        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction
        )
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        # Select all text for easy replacement
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.setTextCursor(cursor)
        self.prepareGeometryChange()
        self.update()

    def finish_editing(self) -> None:
        """Exit inline text editing mode."""
        if not self._editing:
            return
        self._editing = False
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        # Capture the edited text as new source
        self._source_text = self.toPlainText()

        # Clear selection
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)

        # Render math if present
        self._try_render_math()
        self.prepareGeometryChange()
        self.update()

    # =====================================================================
    # Events
    # =====================================================================

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click enters edit mode."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_editing()
            # Let QGraphicsTextItem handle cursor placement
            super().mouseDoubleClickEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        """Escape exits edit mode."""
        if self._editing and event.key() == Qt.Key.Key_Escape:
            self.finish_editing()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        """Focus lost — finish editing only if the scene deselected us.

        Don't auto-finish here because the properties panel or other
        UI widgets may temporarily steal focus.  Editing is reliably
        ended by Escape, clicking another item (deselection via
        itemChange), or clicking empty canvas.
        """
        super().focusOutEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            if self._skip_snap:
                return value
            from diagrammer.canvas.grid import snap_to_grid
            views = self.scene().views()
            if views and getattr(views[0], '_snap_enabled', True):
                return snap_to_grid(value, views[0].grid_spacing)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            if not value and self._editing:
                self.finish_editing()
        return super().itemChange(change, value)

    # =====================================================================
    # Geometry & Painting
    # =====================================================================

    def boundingRect(self) -> QRectF:
        if self._math_renderer:
            return self._math_rect.adjusted(-4, -4, 4, 4)
        return super().boundingRect().adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:
        """Return the clickable/selectable shape — uses boundingRect for reliable hit-testing."""
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if self._math_renderer:
            # Render vector math SVG
            self._math_renderer.render(painter, self._math_rect)
            # Selection highlight
            if self.isSelected():
                painter.setPen(QPen(SELECTION_PEN_COLOR, SELECTION_PEN_WIDTH, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(self._math_rect.adjusted(-2, -2, 2, 2))
            return

        # Draw selection highlight for regular text
        if self.isSelected() and not self._editing:
            painter.setPen(QPen(SELECTION_PEN_COLOR, SELECTION_PEN_WIDTH, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.boundingRect().adjusted(-2, -2, 2, 2))

        # Let QGraphicsTextItem handle text rendering
        super().paint(painter, option, widget)
