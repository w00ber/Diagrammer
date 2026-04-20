"""JunctionItem — invisible connectivity anchor for wire endpoints.

Retained solely as an internal anchor object: it holds a single port so that
wires can terminate on another wire (T-junction) or at a free point in space
(double-click wire terminator). It is never drawn, never selectable, and
never movable.
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QPen
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsItem

from diagrammer.items.port_item import PortItem
from diagrammer.models.component_def import PortDef


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
        return QRectF()

    def paint(self, painter, option, widget=None) -> None:
        pass
