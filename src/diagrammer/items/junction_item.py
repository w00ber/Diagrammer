"""JunctionItem — connectivity anchor for wire endpoints.

Holds a single port so that wires can terminate on another wire
(T-junction) or at a free point in space (double-click wire terminator).
It is never selectable or movable by itself. A junction where two or
more wires actually meet paints the standard schematic filled dot;
single-wire endpoint anchors stay invisible.
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsItem

from diagrammer.items.port_item import PortItem
from diagrammer.models.component_def import PortDef

# Minimum radius of the filled dot drawn where >= 2 wires meet
JUNCTION_DOT_RADIUS = 3.5


class JunctionItem(QGraphicsItem):
    """Invisible single-port anchor used for wire connectivity."""

    def __init__(
        self,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._group_id: str | None = None
        self._group_ids: list[str] = []
        self._skip_snap = False

        port_def = PortDef(name="center", x=0.0, y=0.0)
        self._port = PortItem(port_def, parent=self)
        self._port.setVisible(True)
        self._port.setBrush(QBrush(Qt.GlobalColor.transparent))
        self._port.setPen(QPen(Qt.GlobalColor.transparent))
        # Junction ports are valid connection targets and get the green
        # target pulse; the "normal" brush restored afterwards must stay
        # transparent, not the component-port steel blue.
        self._port._normal_brush = QBrush(Qt.GlobalColor.transparent)
        # Junction ports must stay invisible; hover events would restore the
        # steel-blue _normal_brush on leave and leave orphan dots on canvas.
        self._port.setAcceptHoverEvents(False)

        self.setZValue(6)

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
        r = JUNCTION_DOT_RADIUS * 2 + 1.0
        return QRectF(-r, -r, r * 2, r * 2)

    def paint(self, painter, option, widget=None) -> None:
        """Paint the schematic junction dot when >= 2 wires meet here.

        Without the dot, a T-connection is indistinguishable from two
        unconnected crossing wires.
        """
        scene = self.scene()
        if scene is None or not hasattr(scene, 'connections_on_port'):
            return
        conns = scene.connections_on_port(self._port)
        if len(conns) < 2:
            return
        color = QColor(50, 50, 50)
        width = 0.0
        for conn in conns:
            width = max(width, getattr(conn, 'line_width', 0.0))
            c = getattr(conn, 'line_color', None)
            if c is not None:
                color = QColor(c)
        r = max(JUNCTION_DOT_RADIUS, width * 0.9)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

    def intrinsic_anchor(self) -> QPointF:
        """Local-coords pivot for rotation/flip — junction has no extent,
        so the anchor is its origin (which coincides with its single port)."""
        from PySide6.QtCore import QPointF
        return QPointF(0, 0)
