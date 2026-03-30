"""AnnotationsPanel — compact tool palette for annotation shapes and text."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

ICON_SIZE = 28


def _make_icon(draw_func) -> QIcon:
    """Create a small icon by drawing into a QPixmap."""
    px = QPixmap(ICON_SIZE, ICON_SIZE)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(50, 50, 50), 1.5))
    draw_func(p, ICON_SIZE)
    p.end()
    return QIcon(px)


def _draw_rect(p: QPainter, s: int) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(4, 6, s - 8, s - 12)


def _draw_ellipse(p: QPainter, s: int) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(4, 5, s - 8, s - 10)


def _draw_line(p: QPainter, s: int) -> None:
    p.drawLine(4, s - 6, s - 4, 6)


def _draw_arrow(p: QPainter, s: int) -> None:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    p.drawLine(4, s - 6, s - 4, 6)
    # Arrowhead
    p.setBrush(QColor(50, 50, 50))
    p.setPen(Qt.PenStyle.NoPen)
    tip = QPointF(s - 4, 6)
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(tip.x() - 6, tip.y() + 2),
        QPointF(tip.x() - 2, tip.y() + 6),
    ]))


def _draw_text(p: QPainter, s: int) -> None:
    from PySide6.QtGui import QFont
    p.setFont(QFont("Helvetica", 14, QFont.Weight.Bold))
    p.drawText(4, 4, s - 8, s - 8, Qt.AlignmentFlag.AlignCenter, "T")


class AnnotationsPanel(QDockWidget):
    """Compact tool palette for placing annotation shapes and text."""

    # Emitted with shape type string: "rectangle", "ellipse", "line", "arrow", "text"
    tool_activated = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Annotations", parent)
        self.setObjectName("AnnotationsPanel")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(2)

        tools = [
            ("Rectangle", _draw_rect, "rectangle"),
            ("Ellipse", _draw_ellipse, "ellipse"),
            ("Line", _draw_line, "line"),
            ("Arrow", _draw_arrow, "arrow"),
            ("Text", _draw_text, "text"),
        ]

        for tooltip, draw_func, tool_id in tools:
            btn = QToolButton()
            btn.setIcon(_make_icon(draw_func))
            btn.setToolTip(tooltip)
            btn.setFixedSize(36, 36)
            btn.clicked.connect(lambda checked=False, tid=tool_id: self.tool_activated.emit(tid))
            row.addWidget(btn)

        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()

        self.setWidget(container)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        # Keep it compact
        self.setMaximumHeight(80)
