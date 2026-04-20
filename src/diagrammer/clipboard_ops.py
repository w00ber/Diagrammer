"""Clipboard operations mixin for MainWindow."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor

logger = logging.getLogger(__name__)


class ClipboardMixin:
    """Mixin providing cut / copy / paste / delete operations.

    Expects the host class to have ``_scene``, ``_view``, ``_clipboard``,
    and ``statusBar()`` (i.e. MainWindow).
    """

    # -------------------------------------------------------------- helpers

    def _gather_connected_junctions(self, selected_items) -> list:
        """Find JunctionItems connected to selected items but not explicitly selected.

        Only gathers junctions where a connection has one end on a selected
        item and the other end on the junction.  Does NOT chain through
        junctions (which would pull in the entire circuit).
        """
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        selected_ids = set(id(i) for i in selected_items)
        extra_juncs = []
        for item in self._scene.items():
            if not isinstance(item, ConnectionItem):
                continue
            src_comp = item.source_port.component
            tgt_comp = item.target_port.component
            if id(src_comp) in selected_ids and isinstance(tgt_comp, JunctionItem) and id(tgt_comp) not in selected_ids:
                extra_juncs.append(tgt_comp)
                selected_ids.add(id(tgt_comp))
            elif id(tgt_comp) in selected_ids and isinstance(src_comp, JunctionItem) and id(src_comp) not in selected_ids:
                extra_juncs.append(src_comp)
                selected_ids.add(id(src_comp))
        return extra_juncs

    # ---------------------------------------------------------- delete

    def _delete_selected(self) -> None:
        from diagrammer.commands.delete_command import DeleteCommand
        selected = self._scene.selectedItems()
        if selected:
            cmd = DeleteCommand(self._scene, selected)
            self._scene.undo_stack.push(cmd)

    # -------------------------------------------------------------- copy

    def _copy(self) -> None:
        """Copy selected components, junctions, annotations, shapes, and connections to clipboard."""
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import (
            EllipseItem,
            LineItem,
            RectangleItem,
        )
        from diagrammer.items.svg_image_item import SvgImageItem

        selected_comps = [
            item for item in self._scene.selectedItems()
            if isinstance(item, ComponentItem)
        ]
        selected_juncs = [
            item for item in self._scene.selectedItems()
            if isinstance(item, JunctionItem)
        ]
        selected_annots = [
            item for item in self._scene.selectedItems()
            if isinstance(item, AnnotationItem)
        ]
        selected_shapes = [
            item for item in self._scene.selectedItems()
            if isinstance(item, (RectangleItem, EllipseItem, LineItem))
        ]
        selected_svg_images = [
            item for item in self._scene.selectedItems()
            if isinstance(item, SvgImageItem)
        ]
        selected_conns_direct = [
            item for item in self._scene.selectedItems()
            if isinstance(item, ConnectionItem)
        ]

        # Auto-include endpoint junctions for selected connections
        # (free wires have invisible junctions the user can't select directly)
        for conn in selected_conns_direct:
            for port in (conn.source_port, conn.target_port):
                comp = port.component
                if isinstance(comp, JunctionItem) and comp not in selected_juncs:
                    selected_juncs.append(comp)

        if (not selected_comps and not selected_juncs
                and not selected_annots and not selected_shapes
                and not selected_svg_images):
            return

        # Auto-include connected junctions (invisible T-junction markers)
        all_selected = selected_comps + selected_juncs
        extra_juncs = self._gather_connected_junctions(all_selected)
        selected_juncs.extend(extra_juncs)
        all_selected = selected_comps + selected_juncs
        item_set = set(id(c) for c in all_selected)

        # Collect connections where both endpoints are in the selection
        selected_conns = []
        for item in self._scene.items():
            if isinstance(item, ConnectionItem):
                if (id(item.source_port.component) in item_set and
                        id(item.target_port.component) in item_set):
                    selected_conns.append(item)

        # Serialize to clipboard
        self._clipboard = []
        id_map: dict[str, int] = {}  # instance_id -> clipboard index
        idx = 0
        for comp in selected_comps:
            id_map[comp.instance_id] = idx
            self._clipboard.append({
                "type": "component",
                "def_key": comp.library_key,
                "pos": (comp.pos().x(), comp.pos().y()),
                "rotation": comp.rotation_angle,
                "flip_h": comp.flip_h,
                "flip_v": comp.flip_v,
                "stretch_dx": comp.stretch_dx,
                "stretch_dy": comp.stretch_dy,
                "group": list(comp._group_ids),
            })
            idx += 1
        for junc in selected_juncs:
            id_map[junc.instance_id] = idx
            self._clipboard.append({
                "type": "junction",
                "pos": (junc.pos().x(), junc.pos().y()),
                "visible": junc.isVisible(),
                "group": list(junc._group_ids),
            })
            idx += 1
        for annot in selected_annots:
            self._clipboard.append({
                "type": "annotation",
                "pos": (annot.pos().x(), annot.pos().y()),
                "source_text": annot.source_text,
                "font_family": annot.font_family,
                "font_size": annot.font_size,
                "font_bold": annot.font_bold,
                "font_italic": annot.font_italic,
                "text_color": annot.text_color.name(),
                "rotation": annot.rotation(),
                "group": list(annot._group_ids),
            })
        for shape in selected_shapes:
            if isinstance(shape, LineItem):
                self._clipboard.append({
                    "type": "line",
                    "pos": (shape.pos().x(), shape.pos().y()),
                    "start": (shape.line_start.x(), shape.line_start.y()),
                    "end": (shape.line_end.x(), shape.line_end.y()),
                    "stroke_color": shape.stroke_color.name(QColor.NameFormat.HexArgb),
                    "stroke_width": shape.stroke_width,
                    "dash_style": shape.dash_style,
                    "cap_style": shape.cap_style,
                    "arrow_style": shape.arrow_style,
                    "arrow_type": shape.arrow_type,
                    "arrow_scale": shape.arrow_scale,
                    "arrow_extend": shape.arrow_extend,
                    "group": list(shape._group_ids),
                })
            else:
                shape_type = "rectangle" if isinstance(shape, RectangleItem) else "ellipse"
                entry = {
                    "type": shape_type,
                    "pos": (shape.pos().x(), shape.pos().y()),
                    "width": shape.shape_width,
                    "height": shape.shape_height,
                    "stroke_color": shape.stroke_color.name(QColor.NameFormat.HexArgb),
                    "fill_color": shape.fill_color.name(QColor.NameFormat.HexArgb),
                    "stroke_width": shape.stroke_width,
                    "dash_style": shape.dash_style,
                    "group": list(shape._group_ids),
                }
                if isinstance(shape, RectangleItem):
                    entry["corner_radius"] = shape.corner_radius
                self._clipboard.append(entry)
        import base64
        for svg_img in selected_svg_images:
            self._clipboard.append({
                "type": "svg_image",
                "pos": (svg_img.pos().x(), svg_img.pos().y()),
                "width": svg_img.image_width,
                "height": svg_img.image_height,
                "svg_data": base64.b64encode(svg_img.svg_data).decode("ascii"),
                "group": list(svg_img._group_ids),
            })
        for conn in selected_conns:
            src_idx = id_map.get(conn.source_port.component.instance_id)
            tgt_idx = id_map.get(conn.target_port.component.instance_id)
            if src_idx is not None and tgt_idx is not None:
                self._clipboard.append({
                    "type": "connection",
                    "src_comp": src_idx,
                    "src_port": conn.source_port.port_name,
                    "tgt_comp": tgt_idx,
                    "tgt_port": conn.target_port.port_name,
                    "vertices": [(v.x(), v.y()) for v in conn.vertices],
                    "line_width": conn.line_width,
                    "line_color": conn.line_color.name(),
                    "corner_radius": conn.corner_radius,
                    "routing_mode": conn.routing_mode,
                    "closed": conn.closed,
                    "group": list(conn._group_ids),
                })

    # -------------------------------------------------------------- cut

    def _cut(self) -> None:
        """Copy selected items, then delete them."""
        self._copy()
        self._delete_selected()

    # ------------------------------------------------ external SVG paste

    def _try_paste_external_svg(self) -> bool:
        """Check system clipboard for SVG data and paste as SvgImageItem.

        Returns True if SVG was found and pasted, False otherwise.
        """
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None:
            return False

        svg_data: bytes | None = None

        # Check for explicit SVG MIME type
        if mime.hasFormat("image/svg+xml"):
            raw = mime.data("image/svg+xml")
            if raw and raw.size() > 0:
                svg_data = bytes(raw)

        # Fallback: check if plain text looks like SVG
        if svg_data is None and mime.hasText():
            text = mime.text().strip()
            if text.startswith("<svg") or text.startswith("<?xml"):
                svg_data = text.encode("utf-8")

        if svg_data is None:
            return False

        # Validate that QSvgRenderer can parse it
        from PySide6.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(svg_data)
        if not renderer.isValid():
            return False

        from diagrammer.commands.shape_command import AddShapeCommand
        from diagrammer.items.svg_image_item import SvgImageItem
        item = SvgImageItem(svg_data)
        view = self._view
        center = view.mapToScene(view.viewport().rect().center())
        self._scene.undo_stack.beginMacro("Paste SVG")
        cmd = AddShapeCommand(self._scene, item, center)
        self._scene.undo_stack.push(cmd)
        self._scene.undo_stack.endMacro()
        self._scene.clearSelection()
        item.setSelected(True)
        self.statusBar().showMessage("Pasted SVG from clipboard", 5000)
        return True

    # -------------------------------------------------------------- paste

    def _paste(self) -> None:
        """Paste clipboard contents, offset from original positions.

        Checks the system clipboard first for external SVG data (e.g. from
        Illustrator or Inkscape). Falls back to the internal clipboard.
        """
        if self._try_paste_external_svg():
            return
        if not self._clipboard:
            return

        self._scene.undo_stack.beginMacro("Paste")
        import uuid as _uuid
        from diagrammer.commands.add_command import AddComponentCommand
        from diagrammer.commands.connect_command import CreateConnectionCommand
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import (
            EllipseItem,
            LineItem,
            RectangleItem,
        )

        # Remap group_ids so pasted items form new groups (not shared with originals)
        old_gid_to_new: dict[str, str] = {}

        def _remap_group(target_item, entry_data):
            """Remap the group stack from clipboard data onto a new item."""
            from diagrammer.commands.group_command import set_group_ids
            raw = entry_data.get("group", [])
            if isinstance(raw, str):
                raw = [raw] if raw else []
            if raw:
                new_stack = []
                for gid in raw:
                    if gid not in old_gid_to_new:
                        old_gid_to_new[gid] = _uuid.uuid4().hex[:12]
                    new_stack.append(old_gid_to_new[gid])
                set_group_ids(target_item, new_stack)

        PASTE_OFFSET = 40.0  # offset pasted items from originals

        # First pass: create components, junctions, and annotations
        new_items: list = []  # ComponentItem, JunctionItem, AnnotationItem, or None
        pasted_annotations: list = []
        for entry in self._clipboard:
            if entry["type"] == "component":
                comp_def = self._library.get(entry["def_key"])
                if comp_def is None:
                    new_items.append(None)
                    continue
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                cmd = AddComponentCommand(self._scene, comp_def, pos)
                self._scene.undo_stack.push(cmd)
                item = cmd.item
                if item is None:
                    new_items.append(None)
                    continue
                # Apply transforms — bypass snap during setup
                item._skip_snap = True
                if entry.get("rotation", 0):
                    item.rotate_by(entry["rotation"])
                if entry.get("flip_h"):
                    item.set_flip_h(True)
                if entry.get("flip_v"):
                    item.set_flip_v(True)
                if entry.get("stretch_dx", 0) or entry.get("stretch_dy", 0):
                    item.set_stretch(entry.get("stretch_dx", 0), entry.get("stretch_dy", 0))
                # Set exact position after all transforms
                item.setPos(pos)
                item._skip_snap = False
                _remap_group(item, entry)
                new_items.append(item)
            elif entry["type"] == "junction":
                junc = JunctionItem()
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                junc._skip_snap = True
                junc.setPos(pos)
                junc._skip_snap = False
                if not entry.get("visible", True):
                    junc.setVisible(False)
                self._scene.addItem(junc)
                _remap_group(junc, entry)
                new_items.append(junc)
            elif entry["type"] == "annotation":
                annot = AnnotationItem(entry.get("source_text", "Text"))
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                if "font_family" in entry:
                    annot.font_family = entry["font_family"]
                if "font_size" in entry:
                    annot.font_size = entry["font_size"]
                if entry.get("font_bold"):
                    annot.font_bold = True
                if entry.get("font_italic"):
                    annot.font_italic = True
                if "text_color" in entry:
                    annot.text_color = QColor(entry["text_color"])
                if entry.get("rotation"):
                    annot.setTransformOriginPoint(annot.boundingRect().center())
                    annot.setRotation(entry["rotation"])
                annot.setPos(pos)
                self._scene.addItem(annot)
                _remap_group(annot, entry)
                pasted_annotations.append(annot)
            elif entry["type"] in ("rectangle", "ellipse", "line"):
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                if entry["type"] == "rectangle":
                    shape = RectangleItem(
                        width=entry.get("width", 100),
                        height=entry.get("height", 60),
                    )
                    if "fill_color" in entry:
                        shape.fill_color = QColor(entry["fill_color"])
                    if "corner_radius" in entry:
                        shape.corner_radius = entry["corner_radius"]
                elif entry["type"] == "ellipse":
                    shape = EllipseItem(
                        width=entry.get("width", 80),
                        height=entry.get("height", 80),
                    )
                    if "fill_color" in entry:
                        shape.fill_color = QColor(entry["fill_color"])
                else:  # line
                    shape = LineItem(
                        start=QPointF(entry["start"][0], entry["start"][1]),
                        end=QPointF(entry["end"][0], entry["end"][1]),
                    )
                    if "cap_style" in entry:
                        shape.cap_style = entry["cap_style"]
                    if "arrow_style" in entry:
                        shape.arrow_style = entry["arrow_style"]
                    if "arrow_type" in entry:
                        shape.arrow_type = entry["arrow_type"]
                    if "arrow_scale" in entry:
                        shape.arrow_scale = entry["arrow_scale"]
                    if "arrow_extend" in entry:
                        shape.arrow_extend = entry["arrow_extend"]
                if "stroke_color" in entry:
                    shape.stroke_color = QColor(entry["stroke_color"])
                if "stroke_width" in entry:
                    shape.stroke_width = entry["stroke_width"]
                if "dash_style" in entry:
                    shape.dash_style = entry["dash_style"]
                shape._skip_snap = True
                shape.setPos(pos)
                shape._skip_snap = False
                self._scene.addItem(shape)
                _remap_group(shape, entry)
                pasted_annotations.append(shape)
            elif entry["type"] == "svg_image":
                import base64
                from diagrammer.items.svg_image_item import SvgImageItem
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                svg_data = base64.b64decode(entry["svg_data"])
                svg_item = SvgImageItem(
                    svg_data,
                    width=entry.get("width"),
                    height=entry.get("height"),
                )
                svg_item._skip_snap = True
                svg_item.setPos(pos)
                svg_item._skip_snap = False
                self._scene.addItem(svg_item)
                _remap_group(svg_item, entry)
                pasted_annotations.append(svg_item)

        # Second pass: create connections
        for entry in self._clipboard:
            if entry["type"] != "connection":
                continue
            src_item = new_items[entry["src_comp"]] if entry["src_comp"] < len(new_items) else None
            tgt_item = new_items[entry["tgt_comp"]] if entry["tgt_comp"] < len(new_items) else None
            if src_item is None or tgt_item is None:
                continue
            src_port = src_item.port_by_name(entry["src_port"]) if hasattr(src_item, 'port_by_name') else None
            tgt_port = tgt_item.port_by_name(entry["tgt_port"]) if hasattr(tgt_item, 'port_by_name') else None
            # JunctionItem: single port named "center"
            if src_port is None and isinstance(src_item, JunctionItem):
                src_port = src_item.port if entry["src_port"] == "center" else None
            if tgt_port is None and isinstance(tgt_item, JunctionItem):
                tgt_port = tgt_item.port if entry["tgt_port"] == "center" else None
            if src_port and tgt_port:
                cmd = CreateConnectionCommand(self._scene, src_port, tgt_port)
                self._scene.undo_stack.push(cmd)
                conn = cmd.connection
                if conn:
                    # Restore style
                    if "line_width" in entry:
                        conn.line_width = entry["line_width"]
                    if "line_color" in entry:
                        conn.line_color = QColor(entry["line_color"])
                    if "corner_radius" in entry:
                        conn.corner_radius = entry["corner_radius"]
                    if "routing_mode" in entry:
                        conn.routing_mode = entry["routing_mode"]
                    if entry.get("closed"):
                        conn.closed = True
                    # Restore vertices (offset)
                    if entry.get("vertices"):
                        conn.vertices = [
                            QPointF(v[0] + PASTE_OFFSET, v[1] + PASTE_OFFSET)
                            for v in entry["vertices"]
                        ]
                    _remap_group(conn, entry)

        self._scene.undo_stack.endMacro()

        # Update all routes so approach segments and lead shortening are computed
        self._scene.update_connections()

        # Select the pasted items
        self._scene.clearSelection()
        for item in new_items:
            if item is not None:
                item.setSelected(True)
        for item in pasted_annotations:
            item.setSelected(True)
