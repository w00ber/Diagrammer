"""ComponentItem — a placed SVG component on the diagram canvas."""

from __future__ import annotations

import uuid

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from diagrammer.items.port_item import PortItem
from diagrammer.models.component_def import ComponentDef


# Selection highlight
SELECTION_PEN_COLOR = QColor(0, 120, 215)
SELECTION_PEN_WIDTH = 2.0

# Stretch handle appearance
STRETCH_HANDLE_SIZE = 14.0
STRETCH_HANDLE_COLOR = QColor(255, 140, 0)         # orange
STRETCH_HANDLE_HOVER_COLOR = QColor(255, 180, 60)   # lighter orange
STRETCH_HANDLE_PEN = QPen(QColor(180, 100, 0), 2.0)
STRETCH_HANDLE_HIT_MARGIN = 8.0  # extra margin for hit-testing


class ComponentItem(QGraphicsItem):
    """A diagram component rendered from an SVG definition.

    The item renders the SVG artwork and manages child PortItems
    for connection points. Supports rotation in 90-degree increments
    and horizontal/vertical flipping.

    Grid snapping is port-based: the component is positioned so that
    the "snap anchor" port falls on a grid point. The anchor is set
    externally (by the view) when the user starts dragging.

    Stretchable components have a break line defined in the SVG.
    When stretched, the SVG is rendered in two halves with a gap
    between them.  Ports on the "far" side of the break shift by
    the stretch amount.
    """

    def __init__(
        self,
        component_def: ComponentDef,
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._def = component_def
        self._id = instance_id or uuid.uuid4().hex[:12]

        # Render at native viewBox size — 1 viewBox unit = 1 scene unit.
        # This preserves stroke weights exactly as the SVG designer intended.
        self._width = component_def.width
        self._height = component_def.height

        # Rotation and flip state
        self._rotation_angle = 0.0
        self._flip_h = False
        self._flip_v = False

        # Port-based snap
        self._snap_anchor_offset: QPointF | None = None
        self._skip_snap = False

        # Stretch state
        self._stretch_dx: float = 0.0
        self._stretch_dy: float = 0.0

        # Stretch interaction state
        self._dragging_stretch_h: bool = False
        self._dragging_stretch_v: bool = False
        self._stretch_drag_start_pos: QPointF | None = None
        self._stretch_drag_start_dx: float = 0.0
        self._stretch_drag_start_dy: float = 0.0
        self._stretch_h_handle_hovered: bool = False
        self._stretch_v_handle_hovered: bool = False

        # Set transform origin to center
        self.setTransformOriginPoint(self._width / 2, self._height / 2)

        # SVG renderer — hide non-rendered groups (NO lead shortening at init;
        # leads are shortened dynamically based on actual connections)
        self._renderer = QSvgRenderer(self._prepare_svg_bytes(component_def.svg_path))
        # Cache for dynamic lead shortening: maps port_name -> shorten_amount
        self._cached_lead_shortening: dict[str, float] = {}
        self._lead_renderer: QSvgRenderer | None = None  # rebuilt when shortening changes

        # Create port child items at native viewBox positions
        self._ports: list[PortItem] = []
        for port_def in component_def.ports:
            port = PortItem(port_def, parent=self)
            self._ports.append(port)

        # Interaction flags
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(5)

    # -- Public API --

    @property
    def instance_id(self) -> str:
        return self._id

    @property
    def component_def(self) -> ComponentDef:
        return self._def

    @property
    def ports(self) -> list[PortItem]:
        return self._ports

    def port_by_name(self, name: str) -> PortItem | None:
        for p in self._ports:
            if p.port_name == name:
                return p
        return None

    @property
    def library_key(self) -> str:
        """The component definition key, e.g. 'flowchart/process'."""
        return f"{self._def.category}/{self._def.name}"

    # -- Stretch API --

    @property
    def stretch_dx(self) -> float:
        return self._stretch_dx

    @property
    def stretch_dy(self) -> float:
        return self._stretch_dy

    def set_stretch(self, dx: float, dy: float) -> None:
        """Set the stretch amounts and update geometry accordingly.

        dx: horizontal stretch amount (applied when a vertical break line exists)
        dy: vertical stretch amount (applied when a horizontal break line exists)
        """
        cdef = self._def

        # Clamp so stretched size doesn't go below min_width/min_height
        if cdef.stretch_v_pos is not None:
            if cdef.min_width > 0:
                min_dx = cdef.min_width - cdef.width
                dx = max(dx, min_dx)
            # Don't allow negative stretch that would collapse the component
            dx = max(dx, -(cdef.width - cdef.stretch_v_pos) + 1.0)

        if cdef.stretch_h_pos is not None:
            if cdef.min_height > 0:
                min_dy = cdef.min_height - cdef.height
                dy = max(dy, min_dy)
            dy = max(dy, -(cdef.height - cdef.stretch_h_pos) + 1.0)

        if dx == self._stretch_dx and dy == self._stretch_dy:
            return

        self.prepareGeometryChange()
        self._stretch_dx = dx
        self._stretch_dy = dy

        # Update overall dimensions
        self._width = cdef.width + self._stretch_dx
        self._height = cdef.height + self._stretch_dy

        # Update transform origin to new center
        self.setTransformOriginPoint(self._width / 2, self._height / 2)

        # Reposition ports that are on the "far side" of the break
        self._update_port_positions_for_stretch()

        # Rebuild the item transform so the new center is used
        self._apply_transform()

        self.update()

    def _update_port_positions_for_stretch(self) -> None:
        """Reposition port items to account for the current stretch amounts.

        Ports on the far side of a break line are shifted by the stretch delta.
        """
        cdef = self._def
        for port, port_def in zip(self._ports, cdef.ports):
            px = port_def.x
            py = port_def.y

            # Horizontal stretch (vertical break line at X = stretch_v_pos)
            if cdef.stretch_v_pos is not None and px > cdef.stretch_v_pos:
                px += self._stretch_dx

            # Vertical stretch (horizontal break line at Y = stretch_h_pos)
            if cdef.stretch_h_pos is not None and py > cdef.stretch_h_pos:
                py += self._stretch_dy

            port.setPos(px, py)

    # -- Snap anchor --

    def set_snap_anchor_from_port(self, port: PortItem) -> None:
        """Set the snap anchor to a specific port's scene position offset."""
        # The offset is the vector from the component's pos() to the port's scene center
        port_scene = port.scene_center()
        self._snap_anchor_offset = port_scene - self.pos()

    def set_snap_anchor_closest_to(self, scene_pos: QPointF) -> None:
        """Set the snap anchor to the port closest to the given scene position.

        If no ports, uses the component center.
        """
        if not self._ports:
            # Use component center
            center = self.mapToScene(QPointF(self._width / 2, self._height / 2))
            self._snap_anchor_offset = center - self.pos()
            return

        best_port = None
        best_dist = float("inf")
        for port in self._ports:
            port_scene = port.scene_center()
            dx = port_scene.x() - scene_pos.x()
            dy = port_scene.y() - scene_pos.y()
            dist = dx * dx + dy * dy
            if dist < best_dist:
                best_dist = dist
                best_port = port

        if best_port is not None:
            self.set_snap_anchor_from_port(best_port)

    def clear_snap_anchor(self) -> None:
        """Clear the snap anchor (reverts to default center snap)."""
        self._snap_anchor_offset = None

    # -- Rotation / Flip --

    @property
    def rotation_angle(self) -> float:
        return self._rotation_angle

    @property
    def flip_h(self) -> bool:
        return self._flip_h

    @property
    def flip_v(self) -> bool:
        return self._flip_v

    def rotate_by(self, degrees: float) -> None:
        """Rotate the component by the given degrees (about component center)."""
        self._rotation_angle = (self._rotation_angle + degrees) % 360
        self._apply_transform()

    def rotate_around_port(self, port: PortItem, degrees: float) -> None:
        """Rotate the component about a specific port's scene position.

        The port stays fixed in scene space; the component pivots around it.
        """
        # Record the port's scene position before rotation
        pivot = port.scene_center()

        # Apply the rotation to the item's angle
        self._rotation_angle = (self._rotation_angle + degrees) % 360
        self._apply_transform()

        # After rotating the transform, the port has moved in scene space.
        # Translate the component so the port returns to its original scene position.
        # Bypass snap-to-grid during this correction to maintain exact port placement.
        new_pivot = port.scene_center()
        delta = pivot - new_pivot
        self._skip_snap = True
        self.setPos(self.pos() + delta)
        self._skip_snap = False

    def set_flip_h(self, flipped: bool) -> None:
        """Set horizontal flip state."""
        self._flip_h = flipped
        self._apply_transform()

    def set_flip_v(self, flipped: bool) -> None:
        """Set vertical flip state."""
        self._flip_v = flipped
        self._apply_transform()

    def _apply_transform(self) -> None:
        """Rebuild the item transform from rotation + flip state."""
        cx = self._width / 2
        cy = self._height / 2
        t = QTransform()
        t.translate(cx, cy)
        sx = -1.0 if self._flip_h else 1.0
        sy = -1.0 if self._flip_v else 1.0
        t.scale(sx, sy)
        t.rotate(self._rotation_angle)
        t.translate(-cx, -cy)
        self.setTransform(t)

    # -- SVG preparation --

    @staticmethod
    def _prepare_svg_bytes(svg_path, lead_shortening: dict[str, float] | None = None,
                           port_positions: list | None = None) -> bytes:
        """Load SVG, hide non-rendered groups, and optionally shorten leads.

        Args:
            svg_path: Path to the SVG file.
            lead_shortening: Dict mapping port names to shortening amounts.
                           Only ports with non-zero values get their leads shortened.
            port_positions: List of (name, x, y) tuples for port positions.
        """
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(svg_path))
        root = tree.getroot()

        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "g" and elem.get("id") in ("ports", "labels", "stretch"):
                elem.set("display", "none")

        # Shorten specific leads based on actual connections
        if lead_shortening and port_positions:
            xy_map = [(x, y) for _, x, y in port_positions]
            name_map = {(round(x, 1), round(y, 1)): name for name, x, y in port_positions}
            ComponentItem._shorten_leads_in_svg(root, lead_shortening, xy_map, name_map)

        return ET.tostring(root, encoding="unicode").encode("utf-8")

    @staticmethod
    def _shorten_leads_in_svg(root, lead_shortening: dict[str, float],
                               port_positions: list, name_map: dict) -> None:
        """Shorten lead elements per-port based on actual connection angles."""
        leads_group = None
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "g" and elem.get("id") == "leads":
                leads_group = elem
                break
        if leads_group is None:
            return

        for elem in leads_group:
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "line":
                ComponentItem._shorten_line_elem(elem, lead_shortening, port_positions, name_map)
            elif tag == "path":
                ComponentItem._shorten_path_elem(elem, lead_shortening, port_positions, name_map)

    @staticmethod
    def _find_port_at(x: float, y: float, port_positions: list, name_map: dict) -> tuple[str | None, float]:
        """Find which port is at (x, y). Returns (port_name, distance) or (None, inf)."""
        for px, py in port_positions:
            d = (x - px) ** 2 + (y - py) ** 2
            if d < 4.0:
                key = (round(px, 1), round(py, 1))
                return name_map.get(key), d
        return None, float("inf")

    @staticmethod
    def _shorten_line_elem(elem, shortening: dict, positions: list, name_map: dict) -> None:
        x1 = float(elem.get("x1", "0"))
        y1 = float(elem.get("y1", "0"))
        x2 = float(elem.get("x2", "0"))
        y2 = float(elem.get("y2", "0"))

        name1, _ = ComponentItem._find_port_at(x1, y1, positions, name_map)
        if name1 and shortening.get(name1, 0) > 0:
            amt = shortening[name1]
            dx, dy = x2 - x1, y2 - y1
            length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
            elem.set("x1", str(x1 + dx / length * amt))
            elem.set("y1", str(y1 + dy / length * amt))

        name2, _ = ComponentItem._find_port_at(x2, y2, positions, name_map)
        if name2 and shortening.get(name2, 0) > 0:
            amt = shortening[name2]
            dx, dy = x1 - x2, y1 - y2
            length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
            elem.set("x2", str(x2 + dx / length * amt))
            elem.set("y2", str(y2 + dy / length * amt))

    @staticmethod
    def _shorten_path_elem(elem, shortening: dict, positions: list, name_map: dict) -> None:
        import re
        d = elem.get("d", "")
        if not d:
            return
        tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)
        if len(tokens) < 4 or tokens[0].upper() != 'M':
            return

        mx, my = float(tokens[1]), float(tokens[2])

        # Check start point (port at the M position)
        name_start, _ = ComponentItem._find_port_at(mx, my, positions, name_map)
        if name_start and shortening.get(name_start, 0) > 0:
            amt = shortening[name_start]
            cmd = tokens[3] if len(tokens) > 3 else ""
            if cmd.upper() == 'H':
                is_relative = cmd == 'h'
                hval = float(tokens[4])
                if is_relative:
                    # M moves inward, shrink relative distance to keep far end fixed
                    sign = 1.0 if hval > 0 else -1.0
                    tokens[1] = str(mx + sign * amt)
                    tokens[4] = str(hval - sign * amt)
                else:
                    sign = 1.0 if hval > mx else -1.0
                    tokens[1] = str(mx + sign * amt)
            elif cmd.upper() == 'V':
                is_relative = cmd == 'v'
                vval = float(tokens[4])
                if is_relative:
                    sign = 1.0 if vval > 0 else -1.0
                    tokens[2] = str(my + sign * amt)
                    tokens[4] = str(vval - sign * amt)
                else:
                    sign = 1.0 if vval > my else -1.0
                    tokens[2] = str(my + sign * amt)
            elem.set("d", " ".join(tokens))
            return

        # Check end point (port at the H/V destination)
        if len(tokens) > 3:
            cmd = tokens[3]
            is_relative = cmd.islower()
            if cmd.upper() == 'H':
                hval = float(tokens[4])
                end_x = mx + hval if is_relative else hval
                name_end, _ = ComponentItem._find_port_at(end_x, my, positions, name_map)
                if name_end and shortening.get(name_end, 0) > 0:
                    amt = shortening[name_end]
                    if is_relative:
                        sign = 1.0 if hval < 0 else -1.0
                        tokens[4] = str(hval + sign * amt)
                    else:
                        sign = 1.0 if mx > hval else -1.0
                        tokens[4] = str(hval + sign * amt)
                    elem.set("d", " ".join(tokens))
            elif cmd.upper() == 'V':
                vval = float(tokens[4])
                end_y = my + vval if is_relative else vval
                name_end, _ = ComponentItem._find_port_at(mx, end_y, positions, name_map)
                if name_end and shortening.get(name_end, 0) > 0:
                    amt = shortening[name_end]
                    if is_relative:
                        sign = 1.0 if vval < 0 else -1.0
                        tokens[4] = str(vval + sign * amt)
                    else:
                        sign = 1.0 if my > vval else -1.0
                        tokens[4] = str(vval + sign * amt)
                    elem.set("d", " ".join(tokens))

    # -- Stretch handle geometry --

    def _h_stretch_handle_rect(self, side: str = "right") -> QRectF | None:
        """Return the bounding rect of a horizontal stretch handle.

        side: 'right' or 'left'
        """
        if self._def.stretch_v_pos is None:
            return None
        s = STRETCH_HANDLE_SIZE
        y = self._height / 2 - s / 2
        if side == "right":
            x = self._width - s / 2
        else:
            x = -s / 2
        return QRectF(x, y, s, s)

    def _v_stretch_handle_rect(self, side: str = "bottom") -> QRectF | None:
        """Return the bounding rect of a vertical stretch handle.

        side: 'bottom' or 'top'
        """
        if self._def.stretch_h_pos is None:
            return None
        s = STRETCH_HANDLE_SIZE
        x = self._width / 2 - s / 2
        if side == "bottom":
            y = self._height - s / 2
        else:
            y = -s / 2
        return QRectF(x, y, s, s)

    def _h_stretch_handle_polygon(self, side: str = "right") -> QPolygonF | None:
        """Diamond polygon for a horizontal stretch handle."""
        rect = self._h_stretch_handle_rect(side)
        if rect is None:
            return None
        cx = rect.center().x()
        cy = rect.center().y()
        s = STRETCH_HANDLE_SIZE / 2
        return QPolygonF([
            QPointF(cx, cy - s),
            QPointF(cx + s, cy),
            QPointF(cx, cy + s),
            QPointF(cx - s, cy),
        ])

    def _v_stretch_handle_polygon(self, side: str = "bottom") -> QPolygonF | None:
        """Diamond polygon for a vertical stretch handle."""
        rect = self._v_stretch_handle_rect(side)
        if rect is None:
            return None
        cx = rect.center().x()
        cy = rect.center().y()
        s = STRETCH_HANDLE_SIZE / 2
        return QPolygonF([
            QPointF(cx, cy - s),
            QPointF(cx + s, cy),
            QPointF(cx, cy + s),
            QPointF(cx - s, cy),
        ])

    def _point_in_h_handle(self, local_pos: QPointF) -> str | None:
        """Test if a point is in an H-stretch handle. Returns 'left', 'right', or None."""
        for side in ("right", "left"):
            rect = self._h_stretch_handle_rect(side)
            if rect is None:
                continue
            expanded = rect.adjusted(
                -STRETCH_HANDLE_HIT_MARGIN, -STRETCH_HANDLE_HIT_MARGIN,
                STRETCH_HANDLE_HIT_MARGIN, STRETCH_HANDLE_HIT_MARGIN,
            )
            if expanded.contains(local_pos):
                return side
        return None

    def _point_in_v_handle(self, local_pos: QPointF) -> str | None:
        """Test if a point is in a V-stretch handle. Returns 'top', 'bottom', or None."""
        for side in ("bottom", "top"):
            rect = self._v_stretch_handle_rect(side)
            if rect is None:
                continue
            expanded = rect.adjusted(
                -STRETCH_HANDLE_HIT_MARGIN, -STRETCH_HANDLE_HIT_MARGIN,
                STRETCH_HANDLE_HIT_MARGIN, STRETCH_HANDLE_HIT_MARGIN,
            )
            if expanded.contains(local_pos):
                return side
        return None

    # -- QGraphicsItem interface --

    def boundingRect(self) -> QRectF:
        margin = max(SELECTION_PEN_WIDTH, STRETCH_HANDLE_SIZE + STRETCH_HANDLE_HIT_MARGIN) / 2 + 1
        return QRectF(-margin, -margin, self._width + 2 * margin, self._height + 2 * margin)

    def _compute_lead_shortening(self) -> dict[str, float]:
        """Compute per-port lead shortening based on actual connected wires.

        Only shortens a lead when a wire connects perpendicular to it.
        Inline (collinear) connections leave the lead at full length.
        """
        from diagrammer.panels.settings_dialog import app_settings
        scene = self.scene()
        if scene is None:
            return {}

        radius = app_settings.default_corner_radius
        if radius <= 0:
            return {}

        shortening: dict[str, float] = {}
        from diagrammer.items.connection_item import ConnectionItem

        for item in scene.items():
            if not isinstance(item, ConnectionItem):
                continue

            for port in self._ports:
                is_source = item.source_port is port
                is_target = item.target_port is port
                if not is_source and not is_target:
                    continue

                # Get the wire's ROUTE direction at this port (before approach
                # segments are added, to avoid circular dependency).
                # Use key_points: [source_port, *waypoints, target_port]
                kps = item._key_points()
                if len(kps) < 2:
                    continue

                if is_source:
                    seg_dx = kps[1].x() - kps[0].x()
                    seg_dy = kps[1].y() - kps[0].y()
                else:
                    seg_dx = kps[-1].x() - kps[-2].x()
                    seg_dy = kps[-1].y() - kps[-2].y()

                seg_len = max((seg_dx ** 2 + seg_dy ** 2) ** 0.5, 1e-9)
                seg_nx = seg_dx / seg_len
                seg_ny = seg_dy / seg_len

                # Get the lead's approach direction (in scene coords)
                adx, ady = ConnectionItem._get_scene_approach(port)
                if abs(adx) + abs(ady) == 0:
                    continue

                # Dot product: if aligned (collinear), don't shorten
                dot = abs(adx * seg_nx + ady * seg_ny)
                if dot > 0.5:
                    continue  # wire is inline with lead — no shortening

                # Wire is perpendicular — shorten by 2*radius so
                # build_rounded_path can apply the full corner radius
                shortening[port.port_name] = radius * 2.0

        return shortening

    def _get_lead_renderer(self) -> QSvgRenderer:
        """Get an SVG renderer with dynamically shortened leads."""
        shortening = self._compute_lead_shortening()
        if shortening != self._cached_lead_shortening:
            self._cached_lead_shortening = shortening
            if shortening:
                port_positions = [(p.name, p.x, p.y) for p in self._def.ports]
                self._lead_renderer = QSvgRenderer(self._prepare_svg_bytes(
                    self._def.svg_path,
                    lead_shortening=shortening,
                    port_positions=port_positions,
                ))
            else:
                self._lead_renderer = None
        return self._lead_renderer if self._lead_renderer else self._renderer

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        cdef = self._def
        is_stretched = (
            (cdef.stretch_v_pos is not None and self._stretch_dx != 0.0)
            or (cdef.stretch_h_pos is not None and self._stretch_dy != 0.0)
        )

        if is_stretched:
            renderer = self._get_stretched_renderer()
            target_rect = QRectF(0, 0, self._width, self._height)
            renderer.render(painter, target_rect)
        else:
            # Use dynamically shortened leads based on actual connections
            renderer = self._get_lead_renderer()
            target_rect = QRectF(0, 0, self._width, self._height)
            renderer.render(painter, target_rect)

        # Selection highlight
        if self.isSelected():
            painter.setPen(QPen(SELECTION_PEN_COLOR, SELECTION_PEN_WIDTH, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(0, 0, self._width, self._height))

            # Draw stretch handles
            self._paint_stretch_handles(painter)

    def _get_stretched_renderer(self) -> QSvgRenderer:
        """Create a QSvgRenderer from SVG with coordinates shifted past the break.

        This modifies the actual SVG geometry — shifting all X/Y coordinates
        beyond the break line by the stretch amount — producing pure vector output.
        The result is cached and only rebuilt when the stretch amount changes.
        """
        cache_key = (self._stretch_dx, self._stretch_dy)
        if (hasattr(self, '_stretched_cache_key')
                and self._stretched_cache_key == cache_key
                and hasattr(self, '_stretched_renderer_cache')):
            return self._stretched_renderer_cache

        import re
        import xml.etree.ElementTree as ET

        cdef = self._def
        tree = ET.parse(str(cdef.svg_path))
        root = tree.getroot()

        # Update the viewBox to the stretched size
        vb = root.get("viewBox", "")
        if vb:
            parts = vb.replace(",", " ").split()
            if len(parts) == 4:
                root.set("viewBox", f"{parts[0]} {parts[1]} {self._width} {self._height}")

        # Hide ports/labels/stretch layers
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "g" and elem.get("id") in ("ports", "labels", "stretch"):
                elem.set("display", "none")

        # Shift coordinates in artwork elements
        bx = cdef.stretch_v_pos  # vertical break line X (for horizontal stretch)
        by = cdef.stretch_h_pos  # horizontal break line Y (for vertical stretch)
        dx = self._stretch_dx
        dy = self._stretch_dy

        artwork = None
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "g" and elem.get("id") == "artwork":
                artwork = elem
                break

        if artwork is not None:
            self._shift_svg_element(artwork, bx, by, dx, dy)

        # Also shift the leads layer
        leads = None
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "g" and elem.get("id") == "leads":
                leads = elem
                break
        if leads is not None:
            self._shift_svg_element(leads, bx, by, dx, dy)

        svg_bytes = ET.tostring(root, encoding="unicode").encode("utf-8")
        renderer = QSvgRenderer(svg_bytes)
        self._stretched_cache_key = cache_key
        self._stretched_renderer_cache = renderer
        return renderer

    @classmethod
    def _shift_svg_element(cls, elem, bx, by, dx, dy) -> None:
        """Recursively shift SVG element coordinates past the break lines."""
        import re

        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        # Process coordinate attributes based on element type
        if tag == "line":
            cls._shift_attr(elem, "x1", bx, dx)
            cls._shift_attr(elem, "x2", bx, dx)
            cls._shift_attr(elem, "y1", by, dy)
            cls._shift_attr(elem, "y2", by, dy)
        elif tag == "rect":
            x = float(elem.get("x", "0"))
            w = float(elem.get("width", "0"))
            if bx is not None and dx != 0:
                if x > bx:
                    # Entire rect beyond break — shift
                    elem.set("x", str(x + dx))
                elif x + w > bx:
                    # Rect spans break — extend width
                    elem.set("width", str(w + dx))
            y = float(elem.get("y", "0"))
            h = float(elem.get("height", "0"))
            if by is not None and dy != 0:
                if y > by:
                    elem.set("y", str(y + dy))
                elif y + h > by:
                    elem.set("height", str(h + dy))
        elif tag == "circle":
            cls._shift_attr(elem, "cx", bx, dx)
            cls._shift_attr(elem, "cy", by, dy)
        elif tag == "ellipse":
            cls._shift_attr(elem, "cx", bx, dx)
            cls._shift_attr(elem, "cy", by, dy)
        elif tag in ("path", "polyline", "polygon"):
            d = elem.get("d") or elem.get("points")
            if d and bx is not None and dx != 0:
                elem.set("d" if tag == "path" else "points",
                         cls._shift_path_coords(d, bx, dx, axis="x"))
            d2 = elem.get("d") or elem.get("points")
            if d2 and by is not None and dy != 0:
                elem.set("d" if tag == "path" else "points",
                         cls._shift_path_coords(d2, by, dy, axis="y"))

        # Recurse into child elements (g, etc.)
        for child in elem:
            cls._shift_svg_element(child, bx, by, dx, dy)

    @staticmethod
    def _shift_attr(elem, attr: str, break_pos: float | None, delta: float) -> None:
        """Shift a numeric attribute if its value exceeds break_pos."""
        if break_pos is None or delta == 0:
            return
        val_str = elem.get(attr)
        if val_str is None:
            return
        try:
            val = float(val_str)
            if val > break_pos:
                elem.set(attr, str(val + delta))
        except ValueError:
            pass

    @staticmethod
    def _shift_path_coords(d: str, break_pos: float, delta: float, axis: str = "x") -> str:
        """Shift coordinates in an SVG path data string that exceed break_pos.

        For axis='x', shifts X coordinates (1st, 3rd, 5th... numbers in each command).
        For axis='y', shifts Y coordinates (2nd, 4th, 6th... numbers).
        Handles M, L, H, V, C, Q, S, T, A commands (uppercase = absolute only).
        """
        import re

        # Tokenize: split into commands and numbers
        tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)
        result = []
        cmd = ""
        coord_idx = 0  # tracks which coordinate we're on within a command

        # Commands and how many numbers they consume per repetition
        cmd_args = {
            'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0,
            'm': 2, 'l': 2, 'h': 1, 'v': 1, 'c': 6, 's': 4, 'q': 4, 't': 2, 'a': 7, 'z': 0,
        }
        # Which argument indices are X coords vs Y coords for each command
        x_indices = {
            'M': {0}, 'L': {0}, 'H': {0}, 'V': set(), 'C': {0, 2, 4}, 'S': {0, 2}, 'Q': {0, 2}, 'T': {0},
        }
        y_indices = {
            'M': {1}, 'L': {1}, 'H': set(), 'V': {0}, 'C': {1, 3, 5}, 'S': {1, 3}, 'Q': {1, 3}, 'T': {1},
        }

        for token in tokens:
            if token.upper() in cmd_args or token.lower() in cmd_args:
                cmd = token
                coord_idx = 0
                result.append(token)
            else:
                # It's a number
                val = float(token)
                upper_cmd = cmd.upper()
                n_args = cmd_args.get(upper_cmd, 0)
                idx_in_group = coord_idx % n_args if n_args > 0 else 0

                # Only shift absolute commands (uppercase)
                if cmd == upper_cmd and upper_cmd != 'A':
                    target_indices = x_indices.get(upper_cmd, set()) if axis == "x" else y_indices.get(upper_cmd, set())
                    if idx_in_group in target_indices and val > break_pos:
                        val += delta

                result.append(str(round(val, 4) if val != int(val) else int(val)))
                coord_idx += 1

        return " ".join(result)

    def _paint_stretch_handles(self, painter: QPainter) -> None:
        """Draw stretch handles on both ends when selected and stretchable."""
        arrow_pen = QPen(QColor(100, 60, 0), 1.5)
        a = STRETCH_HANDLE_SIZE * 0.25

        # Horizontal stretch handles (left and right edges)
        for side in ("left", "right"):
            poly = self._h_stretch_handle_polygon(side)
            if poly is None:
                continue
            painter.setPen(STRETCH_HANDLE_PEN)
            hovered = (side == "right" and self._stretch_h_handle_hovered) or \
                      (side == "left" and getattr(self, '_stretch_h_left_hovered', False))
            painter.setBrush(STRETCH_HANDLE_HOVER_COLOR if hovered else STRETCH_HANDLE_COLOR)
            painter.drawPolygon(poly)
            cx = poly.boundingRect().center().x()
            cy = poly.boundingRect().center().y()
            painter.setPen(arrow_pen)
            painter.drawLine(QPointF(cx - a, cy), QPointF(cx + a, cy))

        # Vertical stretch handles (top and bottom edges)
        for side in ("top", "bottom"):
            poly = self._v_stretch_handle_polygon(side)
            if poly is None:
                continue
            painter.setPen(STRETCH_HANDLE_PEN)
            hovered = (side == "bottom" and self._stretch_v_handle_hovered) or \
                      (side == "top" and getattr(self, '_stretch_v_top_hovered', False))
            painter.setBrush(STRETCH_HANDLE_HOVER_COLOR if hovered else STRETCH_HANDLE_COLOR)
            painter.drawPolygon(poly)
            cx = poly.boundingRect().center().x()
            cy = poly.boundingRect().center().y()
            painter.setPen(arrow_pen)
            painter.drawLine(QPointF(cx, cy - a), QPointF(cx, cy + a))

    # -- Stretch handle interaction --

    def _snap_stretch_to_grid(self, value: float) -> float:
        """Snap a stretch delta to grid increments."""
        scene = self.scene()
        if scene is None:
            return value
        views = scene.views()
        if not views:
            return value
        view = views[0]
        if not getattr(view, '_snap_enabled', True):
            return value
        spacing = view.grid_spacing
        return round(value / spacing) * spacing

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            local_pos = event.pos()

            # Check horizontal stretch handles (left or right)
            h_side = self._point_in_h_handle(local_pos)
            if h_side is not None:
                self._dragging_stretch_h = True
                self._stretch_drag_side = h_side  # 'left' or 'right'
                self._stretch_drag_start_pos = event.scenePos()
                self._stretch_drag_start_dx = self._stretch_dx
                self._stretch_drag_start_dy = self._stretch_dy
                self._stretch_drag_start_comp_pos = QPointF(self.pos())
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                event.accept()
                return

            # Check vertical stretch handles (top or bottom)
            v_side = self._point_in_v_handle(local_pos)
            if v_side is not None:
                self._dragging_stretch_v = True
                self._stretch_drag_side = v_side  # 'top' or 'bottom'
                self._stretch_drag_start_pos = event.scenePos()
                self._stretch_drag_start_dx = self._stretch_dx
                self._stretch_drag_start_dy = self._stretch_dy
                self._stretch_drag_start_comp_pos = QPointF(self.pos())
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging_stretch_h and self._stretch_drag_start_pos is not None:
            local_start = self.mapFromScene(self._stretch_drag_start_pos)
            local_now = self.mapFromScene(event.scenePos())
            local_dx = local_now.x() - local_start.x()

            side = getattr(self, '_stretch_drag_side', 'right')
            if side == "left":
                # Left handle: drag left = increase stretch, shift component left
                raw_dx = self._stretch_drag_start_dx - local_dx
                snapped_dx = self._snap_stretch_to_grid(raw_dx)
                self.set_stretch(snapped_dx, self._stretch_dy)
                # Shift component position so right side stays fixed
                delta_dx = snapped_dx - self._stretch_drag_start_dx
                start_pos = getattr(self, '_stretch_drag_start_comp_pos', self.pos())
                self._skip_snap = True
                self.setPos(start_pos.x() - delta_dx, start_pos.y())
                self._skip_snap = False
            else:
                raw_dx = self._stretch_drag_start_dx + local_dx
                snapped_dx = self._snap_stretch_to_grid(raw_dx)
                self.set_stretch(snapped_dx, self._stretch_dy)

            if self.scene():
                from diagrammer.canvas.scene import DiagramScene
                scene = self.scene()
                if isinstance(scene, DiagramScene):
                    scene.update_connections()
            event.accept()
            return

        if self._dragging_stretch_v and self._stretch_drag_start_pos is not None:
            local_start = self.mapFromScene(self._stretch_drag_start_pos)
            local_now = self.mapFromScene(event.scenePos())
            local_dy = local_now.y() - local_start.y()

            side = getattr(self, '_stretch_drag_side', 'bottom')
            if side == "top":
                raw_dy = self._stretch_drag_start_dy - local_dy
                snapped_dy = self._snap_stretch_to_grid(raw_dy)
                self.set_stretch(self._stretch_dx, snapped_dy)
                delta_dy = snapped_dy - self._stretch_drag_start_dy
                start_pos = getattr(self, '_stretch_drag_start_comp_pos', self.pos())
                self._skip_snap = True
                self.setPos(start_pos.x(), start_pos.y() - delta_dy)
                self._skip_snap = False
            else:
                raw_dy = self._stretch_drag_start_dy + local_dy
                snapped_dy = self._snap_stretch_to_grid(raw_dy)
                self.set_stretch(self._stretch_dx, snapped_dy)

            if self.scene():
                from diagrammer.canvas.scene import DiagramScene
                scene = self.scene()
                if isinstance(scene, DiagramScene):
                    scene.update_connections()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging_stretch_h or self._dragging_stretch_v:
            # Push undo command for the stretch change
            old_dx = self._stretch_drag_start_dx
            old_dy = self._stretch_drag_start_dy
            new_dx = self._stretch_dx
            new_dy = self._stretch_dy

            self._dragging_stretch_h = False
            self._dragging_stretch_v = False
            self._stretch_drag_start_pos = None
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

            if old_dx != new_dx or old_dy != new_dy:
                from diagrammer.commands.stretch_command import StretchComponentCommand
                scene = self.scene()
                if scene and hasattr(scene, 'undo_stack'):
                    cmd = StretchComponentCommand(self, old_dx, old_dy, new_dx, new_dy)
                    scene.undo_stack.push(cmd)

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            if self._skip_snap:
                return value

            from diagrammer.canvas.grid import snap_to_grid

            views = self.scene().views()
            if not views:
                return value

            view = views[0]
            # Check if snapping is enabled
            snap_enabled = getattr(view, '_snap_enabled', True)
            if not snap_enabled:
                return value

            spacing = view.grid_spacing
            new_pos = value  # QPointF — the proposed new position

            if self._snap_anchor_offset is not None:
                # Snap so the anchor port lands on a grid point
                anchor_scene = new_pos + self._snap_anchor_offset
                snapped_anchor = snap_to_grid(anchor_scene, spacing)
                return new_pos + (snapped_anchor - anchor_scene)
            else:
                # Default: snap using the first port or center
                if self._ports:
                    # Use first port as default anchor
                    port = self._ports[0]
                    port_local = QPointF(port.pos())
                    # Apply item transform to get port offset in scene-relative coords
                    port_offset = self.transform().map(port_local)
                    anchor_scene = new_pos + port_offset
                    snapped_anchor = snap_to_grid(anchor_scene, spacing)
                    return new_pos + (snapped_anchor - anchor_scene)
                else:
                    # No ports: snap component center
                    center_offset = self.transform().map(
                        QPointF(self._width / 2, self._height / 2)
                    )
                    anchor_scene = new_pos + center_offset
                    snapped_anchor = snap_to_grid(anchor_scene, spacing)
                    return new_pos + (snapped_anchor - anchor_scene)

        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._update_port_visibility()
        return super().itemChange(change, value)

    # -- Port visibility --

    def show_all_ports(self) -> None:
        """Force all ports visible (used during connection mode)."""
        for port in self._ports:
            port.setVisible(True)

    def restore_port_visibility(self) -> None:
        """Restore normal port visibility rules."""
        self._update_port_visibility()

    def _update_port_visibility(self) -> None:
        visible = self.isSelected() or self._hovered
        for port in self._ports:
            # Alignment-selected ports stay visible regardless
            if port.is_alignment_selected:
                port.setVisible(True)
            else:
                port.setVisible(visible)

    _hovered: bool = False

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self._update_port_visibility()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self._update_port_visibility()
        self._stretch_h_handle_hovered = False
        self._stretch_v_handle_hovered = False
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event) -> None:
        """Track hover state over stretch handles and update cursor."""
        local_pos = event.pos()
        old_h = self._stretch_h_handle_hovered
        old_v = self._stretch_v_handle_hovered

        h_side = self._point_in_h_handle(local_pos) if self.isSelected() else None
        v_side = self._point_in_v_handle(local_pos) if self.isSelected() else None

        self._stretch_h_handle_hovered = h_side == "right"
        self._stretch_h_left_hovered = h_side == "left"
        self._stretch_v_handle_hovered = v_side == "bottom"
        self._stretch_v_top_hovered = v_side == "top"

        if h_side is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif v_side is not None:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

        # Repaint if hover state changed (to show hover color on handle)
        if old_h != self._stretch_h_handle_hovered or old_v != self._stretch_v_handle_hovered:
            self.update()

        super().hoverMoveEvent(event)
