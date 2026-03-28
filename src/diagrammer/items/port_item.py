"""PortItem — a small circle representing a connection port on a component."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem

if TYPE_CHECKING:
    from diagrammer.items.component_item import ComponentItem
    from diagrammer.models.component_def import PortDef

# Port visual settings
PORT_RADIUS = 4.0
PORT_COLOR = QColor(70, 130, 180)       # steel blue
PORT_HOVER_COLOR = QColor(255, 165, 0)  # orange highlight on hover
PORT_TARGET_COLOR = QColor(0, 200, 80)  # green highlight when valid connection target
PORT_SELECTED_COLOR = QColor(255, 80, 80)  # red when selected for alignment
PORT_PEN_WIDTH = 1.5
PORT_TARGET_RADIUS_MULT = 1.8
PORT_SELECTED_RADIUS_MULT = 1.5


class PortItem(QGraphicsEllipseItem):
    """A connection port displayed as a small circle on a component.

    Ports are children of their parent ComponentItem and positioned
    relative to the component's local coordinate space.
    """

    def __init__(
        self,
        port_def: PortDef,
        parent: ComponentItem,
    ) -> None:
        diameter = PORT_RADIUS * 2
        super().__init__(
            QRectF(-PORT_RADIUS, -PORT_RADIUS, diameter, diameter),
            parent,
        )
        self._port_def = port_def
        self._component = parent

        # Position within parent's coordinate space
        self.setPos(port_def.x, port_def.y)

        # Appearance
        self._normal_brush = QBrush(PORT_COLOR)
        self._hover_brush = QBrush(PORT_HOVER_COLOR)
        self._target_brush = QBrush(PORT_TARGET_COLOR)
        self.setBrush(self._normal_brush)
        self.setPen(QPen(QColor(40, 40, 40), PORT_PEN_WIDTH))

        # Interaction
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        # Ports are visible only when the parent component is hovered or selected
        self.setVisible(False)
        self.setZValue(10)

        # Target highlight state (connection drag)
        self._is_target_highlighted = False
        self._pulse_timer: QTimer | None = None
        self._pulse_state = False

        # Alignment selection state (Ctrl+click)
        self._is_alignment_selected = False

    @property
    def port_name(self) -> str:
        return self._port_def.name

    @property
    def component(self) -> ComponentItem:
        return self._component

    @property
    def approach_dx(self) -> float:
        """Direction the component lead extends from this port (X component)."""
        return self._port_def.approach_dx

    @property
    def approach_dy(self) -> float:
        """Direction the component lead extends from this port (Y component)."""
        return self._port_def.approach_dy

    def scene_center(self):
        """Return the port's center position in scene coordinates."""
        return self.mapToScene(self.rect().center())

    # -- Target highlighting for connection drag --

    def set_target_highlight(self, highlighted: bool) -> None:
        """Show/hide the connection target highlight with a pulsing effect."""
        if highlighted == self._is_target_highlighted:
            return
        self._is_target_highlighted = highlighted
        if highlighted:
            self._pulse_state = True
            self._apply_target_visual()
            # Start pulse timer
            self._pulse_timer = QTimer()
            self._pulse_timer.timeout.connect(self._pulse_tick)
            self._pulse_timer.start(300)  # pulse every 300ms
        else:
            if self._pulse_timer:
                self._pulse_timer.stop()
                self._pulse_timer = None
            self._pulse_state = False
            # Restore normal size and color
            self.setRect(QRectF(-PORT_RADIUS, -PORT_RADIUS, PORT_RADIUS * 2, PORT_RADIUS * 2))
            self.setBrush(self._normal_brush)

    def _pulse_tick(self) -> None:
        self._pulse_state = not self._pulse_state
        self._apply_target_visual()

    def _apply_target_visual(self) -> None:
        if self._pulse_state:
            r = PORT_RADIUS * PORT_TARGET_RADIUS_MULT
            self.setRect(QRectF(-r, -r, r * 2, r * 2))
            self.setBrush(self._target_brush)
        else:
            self.setRect(QRectF(-PORT_RADIUS, -PORT_RADIUS, PORT_RADIUS * 2, PORT_RADIUS * 2))
            self.setBrush(QBrush(PORT_TARGET_COLOR.lighter(150)))

    # -- Alignment selection (Ctrl+click) --

    @property
    def is_alignment_selected(self) -> bool:
        return self._is_alignment_selected

    def set_alignment_selected(self, selected: bool) -> None:
        """Toggle the alignment-selection visual on this port."""
        if selected == self._is_alignment_selected:
            return
        self._is_alignment_selected = selected
        if selected:
            r = PORT_RADIUS * PORT_SELECTED_RADIUS_MULT
            self.setRect(QRectF(-r, -r, r * 2, r * 2))
            self.setBrush(QBrush(PORT_SELECTED_COLOR))
            self.setVisible(True)  # force visible so user can see selection
        else:
            self.setRect(QRectF(-PORT_RADIUS, -PORT_RADIUS, PORT_RADIUS * 2, PORT_RADIUS * 2))
            self.setBrush(self._normal_brush)

    # -- Hover feedback --

    def hoverEnterEvent(self, event) -> None:
        if not self._is_target_highlighted and not self._is_alignment_selected:
            self.setBrush(self._hover_brush)
        self.setCursor(Qt.CursorShape.CrossCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        if not self._is_target_highlighted and not self._is_alignment_selected:
            self.setBrush(self._normal_brush)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverLeaveEvent(event)
