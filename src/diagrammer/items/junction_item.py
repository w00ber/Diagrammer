"""JunctionItem — connectivity anchor for wire endpoints.

Holds a single port so that wires can terminate on another wire
(T-junction) or at a free point in space (double-click wire terminator).
It is never selectable or movable by itself. A junction where two or
more wires actually meet paints the standard schematic filled dot;
single-wire endpoint anchors stay invisible unless the user assigns an
explicit end marker (filled or open terminal dot) via right-click.
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
        self._end_marker: str = "none"  # "none" | "filled" | "open"

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
        # Generous fixed bounds: covers the automatic dot and both end
        # markers (incl. the open ring's stroke) at typical line widths.
        r = JUNCTION_DOT_RADIUS * 2 + 5.0
        return QRectF(-r, -r, r * 2, r * 2)

    @property
    def end_marker(self) -> str:
        """Explicit terminal marker: "none", "filled", or "open".

        A deliberate per-junction annotation (typically on a free wire
        end), independent of the global "Show junction dots" setting,
        which governs only the automatic dots where >= 2 wires meet.
        """
        return self._end_marker

    @end_marker.setter
    def end_marker(self, value: str) -> None:
        if value not in ("none", "filled", "open"):
            value = "none"
        self._end_marker = value
        if value != "none":
            # Free-end anchors are created hidden — the marker must show.
            self.setVisible(True)
            # Paint above the attached wires so the open marker's
            # background fill masks the wire tip. reflow_z preserves
            # relative order within the band, so this sticks.
            scene = self.scene()
            if scene is not None and hasattr(scene, 'connections_on_port'):
                conns = scene.connections_on_port(self._port)
                if conns:
                    top = max(c.zValue() for c in conns)
                    if self.zValue() <= top:
                        self.setZValue(top + 0.0005)
        self.update()

    def _attached_style(self, conns: list) -> tuple[QColor, float]:
        """Color and max line width of the wires attached to this port."""
        color = QColor(50, 50, 50)
        width = 0.0
        for conn in conns:
            width = max(width, getattr(conn, 'line_width', 0.0))
            c = getattr(conn, 'line_color', None)
            if c is not None:
                color = QColor(c)
        return color, width

    def _dot_connections(self) -> list:
        """Connections that justify drawing the junction dot.

        Empty when the "Show junction dots" setting is off (the dot is a
        purely visual convention — connectivity is unaffected) or when
        fewer than two wires meet at this port.
        """
        from diagrammer.panels.settings_dialog import app_settings
        if not getattr(app_settings, "show_junction_dots", True):
            return []
        scene = self.scene()
        if scene is None or not hasattr(scene, 'connections_on_port'):
            return []
        conns = scene.connections_on_port(self._port)
        return conns if len(conns) >= 2 else []

    def _should_draw_dot(self) -> bool:
        return bool(self._dot_connections())

    def paint(self, painter, option, widget=None) -> None:
        """Paint the explicit end marker, or the automatic junction dot.

        Explicit markers ("filled"/"open") always draw. The automatic
        dot appears when >= 2 wires meet here — without it a
        T-connection is indistinguishable from two unconnected crossing
        wires — and is suppressed by the "Show junction dots" setting.
        """
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._end_marker != "none":
            scene = self.scene()
            conns = (scene.connections_on_port(self._port)
                     if scene is not None and hasattr(scene, 'connections_on_port')
                     else [])
            color, width = self._attached_style(conns)
            if self._end_marker == "filled":
                r = max(JUNCTION_DOT_RADIUS, width * 0.9)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))
            else:  # "open" — hollow terminal circle
                from diagrammer.panels.settings_dialog import app_settings
                r = max(4.5, width * 1.1)
                painter.setPen(QPen(color, max(1.5, width * 0.6)))
                # Background fill so the wire visually terminates at the ring
                painter.setBrush(QBrush(app_settings.current_background_color()))
                painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))
            return

        conns = self._dot_connections()
        if not conns:
            return
        color, width = self._attached_style(conns)
        r = max(JUNCTION_DOT_RADIUS, width * 0.9)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

    def intrinsic_anchor(self) -> QPointF:
        """Local-coords pivot for rotation/flip — junction has no extent,
        so the anchor is its origin (which coincides with its single port)."""
        from PySide6.QtCore import QPointF
        return QPointF(0, 0)
