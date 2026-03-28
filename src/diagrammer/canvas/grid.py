"""Grid rendering and snap-to-grid logic for the diagram canvas."""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen


# Grid appearance defaults
DEFAULT_GRID_SPACING = 20.0
MIN_GRID_SPACING_PX = 8  # Don't draw grid when spacing is less than 8 px on screen

# Colors
GRID_DOT_COLOR = QColor(180, 180, 180)
GRID_LINE_COLOR = QColor(220, 220, 220)
GRID_MAJOR_LINE_COLOR = QColor(190, 190, 190)
MAJOR_GRID_EVERY = 5  # Major gridline every N minor lines


def snap_to_grid(pos: QPointF, spacing: float) -> QPointF:
    """Snap a scene position to the nearest grid point."""
    x = round(pos.x() / spacing) * spacing
    y = round(pos.y() / spacing) * spacing
    return QPointF(x, y)


def draw_grid(painter: QPainter, rect: QRectF, spacing: float, scale: float) -> None:
    """Draw a grid of lines within the given rectangle.

    Args:
        painter: The QPainter to draw with.
        rect: The exposed rectangle in scene coordinates.
        spacing: Grid spacing in scene units.
        scale: Current view scale factor (used to skip drawing when zoomed out too far).
    """
    # Don't draw grid if spacing would be less than MIN_GRID_SPACING_PX on screen
    if spacing * scale < MIN_GRID_SPACING_PX:
        return

    left = rect.left()
    right = rect.right()
    top = rect.top()
    bottom = rect.bottom()

    # Align to grid boundaries
    first_x = (left // spacing) * spacing
    first_y = (top // spacing) * spacing

    # Minor grid lines
    minor_pen = QPen(GRID_LINE_COLOR, 0)  # cosmetic pen (width=0 means 1px regardless of zoom)
    major_pen = QPen(GRID_MAJOR_LINE_COLOR, 0)

    # Draw vertical lines
    x = first_x
    while x <= right:
        grid_index = round(x / spacing)
        if grid_index % MAJOR_GRID_EVERY == 0:
            painter.setPen(major_pen)
        else:
            painter.setPen(minor_pen)
        painter.drawLine(QLineF(x, top, x, bottom))
        x += spacing

    # Draw horizontal lines
    y = first_y
    while y <= bottom:
        grid_index = round(y / spacing)
        if grid_index % MAJOR_GRID_EVERY == 0:
            painter.setPen(major_pen)
        else:
            painter.setPen(minor_pen)
        painter.drawLine(QLineF(left, y, right, y))
        y += spacing
