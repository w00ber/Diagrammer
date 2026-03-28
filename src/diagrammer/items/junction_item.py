"""JunctionItem — a small filled dot that acts as a wire junction/tap point.

A junction is placed where wires meet or branch. It has a single port at its
center so that connections can terminate on it.  When placed on an existing
wire, it provides a visual T-junction marker.
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from diagrammer.items.port_item import PortItem
from diagrammer.models.component_def import PortDef

JUNCTION_RADIUS = 5.0
JUNCTION_COLOR = QColor(50, 50, 50)
SELECTION_COLOR = QColor(0, 120, 215)


class JunctionItem(QGraphicsItem):
    """A small filled circle with one port at its center.

    Used as a wire junction / tap point where multiple connections meet.
    """

    def __init__(
        self,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]

        # Create the single center port
        port_def = PortDef(name="center", x=0.0, y=0.0)
        self._port = PortItem(port_def, parent=self)
        self._port.setVisible(True)  # always visible
        # Make the port invisible (the junction dot IS the visual)
        self._port.setBrush(QBrush(Qt.GlobalColor.transparent))
        self._port.setPen(QPen(Qt.GlobalColor.transparent))

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(6)  # above connections (2), above components (5)

    @property
    def instance_id(self) -> str:
        return self._id

    @property
    def port(self) -> PortItem:
        return self._port

    @property
    def ports(self) -> list[PortItem]:
        return [self._port]

    def boundingRect(self) -> QRectF:
        from diagrammer.panels.settings_dialog import app_settings
        r = app_settings.junction_radius + 2
        return QRectF(-r, -r, r * 2, r * 2)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        from diagrammer.panels.settings_dialog import app_settings
        r = app_settings.junction_radius
        if self.isSelected():
            color = SELECTION_COLOR
        else:
            color = app_settings.junction_color
        if app_settings.junction_outline:
            painter.setPen(QPen(QColor(40, 40, 40), 1.5))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            from diagrammer.canvas.grid import snap_to_grid
            views = self.scene().views()
            if views and getattr(views[0], '_snap_enabled', True):
                return snap_to_grid(value, views[0].grid_spacing)
        return super().itemChange(change, value)
