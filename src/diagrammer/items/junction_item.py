"""JunctionItem — connectivity anchor for wire endpoints.

Holds a single port so that wires can terminate on another wire
(T-junction) or at a free point in space (double-click wire terminator).

It is an ordinary scene item (``docs/wiring-interaction-redesign.md``,
invariant I1): selectable, hit-testable within a small radius, and
moved like any other item — a rubber band takes the junctions inside
it, and dragging a junction rubber-bands the wires that meet there.
What it *paints* is a separate matter: nothing at all in the common
case, the standard filled dot where three or more wires meet (two are
a bend or a straight run, which takes no dot), an explicit end marker
if the user set one, and a handle while it or an attached wire is
selected so a free end can be found and grabbed.
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from diagrammer.items.port_item import PortItem
from diagrammer.models.component_def import PortDef

# Minimum radius of the filled dot drawn where >= 3 wires meet
JUNCTION_DOT_RADIUS = 3.5
# Wires that must meet at a junction for the automatic dot. Two wires are
# a bend or a straight run (e.g. what's left of a tee after deleting its
# branch), which takes no dot by schematic convention.
JUNCTION_DOT_MIN_WIRES = 3
# Hit radius (scene units). Generous enough to grab an invisible free
# end, small enough that a wire passing through a tee is still the wire.
JUNCTION_PICK_RADIUS = 6.0
# Handle drawn while selected / attached wire selected — same family as
# the waypoint handles on ConnectionItem so a free end reads as "the
# end of this wire".
HANDLE_SIZE = 8.0
HANDLE_COLOR = QColor(0, 120, 215)
HANDLE_SELECTED_FILL = QColor(255, 160, 0)
HANDLE_SELECTED_EDGE = QColor(200, 80, 0)


class JunctionItem(QGraphicsItem):
    """Single-port anchor used for wire connectivity; see module docstring."""

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

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._hovered = False
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
        # Generous fixed bounds: covers the automatic dot, both end
        # markers (incl. the open ring's stroke) and the selection handle.
        r = JUNCTION_DOT_RADIUS * 2 + 5.0
        return QRectF(-r, -r, r * 2, r * 2)

    def shape(self) -> QPainterPath:
        """Pick region: a small disc, whatever is painted.

        The free end of a wire draws nothing, so without an explicit
        shape it could never be clicked, boxed or dragged.
        """
        path = QPainterPath()
        r = JUNCTION_PICK_RADIUS
        path.addEllipse(QRectF(-r, -r, r * 2, r * 2))
        return path

    def hoverEnterEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def _attached_wire_selected(self) -> bool:
        scene = self.scene()
        if scene is None or not hasattr(scene, 'connections_on_port'):
            return False
        return any(c.isSelected() for c in scene.connections_on_port(self._port))

    def _paint_handle(self, painter: QPainter) -> None:
        """The grab affordance: diamond when selected, square otherwise."""
        r = HANDLE_SIZE / 2
        if self.isSelected():
            sr = r * 1.6
            painter.setPen(QPen(HANDLE_SELECTED_EDGE, 2.0))
            painter.setBrush(QBrush(HANDLE_SELECTED_FILL))
            painter.drawPolygon(QPolygonF([
                QPointF(0, -sr), QPointF(sr, 0), QPointF(0, sr), QPointF(-sr, 0),
            ]))
        else:
            painter.setPen(QPen(HANDLE_COLOR, 1.0))
            painter.setBrush(QBrush(HANDLE_COLOR))
            painter.drawRect(QRectF(-r, -r, HANDLE_SIZE, HANDLE_SIZE))

    @property
    def end_marker(self) -> str:
        """Explicit terminal marker: "none", "filled", or "open".

        A deliberate per-junction annotation (typically on a free wire
        end), independent of the global "Show junction dots" setting,
        which governs only the automatic dots where >= 3 wires meet.
        """
        return self._end_marker

    @end_marker.setter
    def end_marker(self, value: str) -> None:
        if value not in ("none", "filled", "open"):
            value = "none"
        self._end_marker = value
        if value != "none":
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
        fewer than ``JUNCTION_DOT_MIN_WIRES`` wires meet at this port.
        """
        from diagrammer.panels.settings_dialog import app_settings
        if not getattr(app_settings, "show_junction_dots", True):
            return []
        scene = self.scene()
        if scene is None or not hasattr(scene, 'connections_on_port'):
            return []
        conns = scene.connections_on_port(self._port)
        return conns if len(conns) >= JUNCTION_DOT_MIN_WIRES else []

    def _should_draw_dot(self) -> bool:
        return bool(self._dot_connections())

    def paint(self, painter, option, widget=None) -> None:
        """Paint the explicit end marker, or the automatic junction dot.

        Explicit markers ("filled"/"open") always draw. The automatic
        dot appears when >= 3 wires meet here — without it a
        T-connection is indistinguishable from two unconnected crossing
        wires — and is suppressed by the "Show junction dots" setting.
        A selection handle is drawn on top while this junction, or a
        wire ending here, is selected or the cursor is over it; exports
        clear the selection first, so it never reaches paper.
        """
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_marks(painter)
        if self.isSelected() or self._hovered or self._attached_wire_selected():
            self._paint_handle(painter)

    def _paint_marks(self, painter: QPainter) -> None:
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
