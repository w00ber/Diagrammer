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

    # -------------------------------------------------------------- helpers

    def _gather_connected_junctions(self, selected_items) -> list:
        """Junctions that belong with *selected_items* for a copy-like
        operation (copy, duplicate, group, rotate, flip).

        A wrapper over the one connectivity rule,
        :func:`diagrammer.canvas.closure.movable_closure`, with
        ``include_touching`` — nothing stays behind to pin a junction
        in these operations, so every junction wired to a selected item
        travels, plus any rail that ends on the selection. Step D of
        ``docs/wiring-interaction-redesign.md`` is expected to move the
        transforms onto the drag rule instead.
        """
        from diagrammer.canvas.closure import movable_closure

        return movable_closure(
            self._scene.items(), selected_items, include_touching=True,
        ).junctions

    # ---------------------------------------------------------- delete

    def _delete_selected(self) -> None:
        selected = self._scene.selectedItems()
        if selected:
            # Cascades to attached wires and stranded junctions so no
            # dangling connections are left behind; one undo restores all.
            self._scene.delete_items_with_dependents(selected)

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

        if (not selected_comps and not selected_juncs and not selected_conns_direct
                and not selected_annots and not selected_shapes
                and not selected_svg_images):
            return

        # One rule for which junctions and wires belong to the selection
        # (docs/wiring-interaction-redesign.md, I4). A copy takes every
        # junction touching a selected item — nothing stays behind to
        # pin it — plus any rail that ends on the selection, and the
        # wires between all of those.
        from diagrammer.canvas.closure import movable_closure
        closure = movable_closure(
            self._scene.items(),
            selected_comps + selected_juncs + selected_conns_direct,
            include_touching=True,
        )
        for j in closure.junctions:
            if j not in selected_juncs:
                selected_juncs.append(j)
        all_selected = selected_comps + selected_juncs
        selected_conns = list(closure.internal_wires)

        # Serialize to clipboard
        self._clipboard = []
        id_map: dict[str, int] = {}  # instance_id -> clipboard index
        idx = 0
        for comp in selected_comps:
            id_map[comp.instance_id] = idx
            entry = {
                "type": "component",
                "def_key": comp.library_key,
                "pos": (comp.pos().x(), comp.pos().y()),
                "rotation": comp.rotation_angle,
                "flip_h": comp.flip_h,
                "flip_v": comp.flip_v,
                "stretch_dx": comp.stretch_dx,
                "stretch_dy": comp.stretch_dy,
                "group": list(comp._group_ids),
            }
            # Per-instance SVG style edits (fill, stroke, line width...).
            # Stored as plain data so later edits to the original do not
            # leak into what is already on the clipboard.
            if not comp.style_overrides.is_empty():
                entry["style_overrides"] = comp.style_overrides.to_dict()
            self._clipboard.append(entry)
            idx += 1
        for junc in selected_juncs:
            id_map[junc.instance_id] = idx
            self._clipboard.append({
                "type": "junction",
                "pos": (junc.pos().x(), junc.pos().y()),
                "visible": junc.isVisible(),
                "end_marker": junc.end_marker,
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
                # Phase C: persistent intrinsic transform fields. The
                # legacy ``rotation`` key mirrors ``rotation_angle`` for
                # any external consumers reading the clipboard dict.
                "rotation": annot.rotation_angle,
                "rotation_angle": annot.rotation_angle,
                "flip_h": annot.flip_h,
                "flip_v": annot.flip_v,
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
                    "rotation": shape.rotation_angle,
                    "rotation_angle": shape.rotation_angle,
                    "flip_h": shape.flip_h,
                    "flip_v": shape.flip_v,
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
                    "rotation": shape.rotation_angle,
                    "rotation_angle": shape.rotation_angle,
                    "flip_h": shape.flip_h,
                    "flip_v": shape.flip_v,
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
                "rotation_angle": svg_img.rotation_angle,
                "flip_h": svg_img.flip_h,
                "flip_v": svg_img.flip_v,
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
                    # Scene coordinates; the paste shifts them by the
                    # same offset as the items.
                    "waypoints": [[w.x(), w.y()] for w in conn.vertices],
                    "line_width": conn.line_width,
                    "line_color": conn.line_color.name(),
                    "corner_radius": conn.corner_radius,
                    "routing_mode": conn.routing_mode,
                    "closed": conn.closed,
                    "group": list(conn._group_ids),
                    # Direction arrows: t is route-relative, so arrows
                    # follow the pasted wire with no offset handling.
                    "arrows": [
                        {"t": a.t, "forward": a.forward, "style": a.style,
                         "size": a.size, "line_width": a.line_width}
                        for a in conn._arrows
                    ],
                })

        self._note_internal_copy()

    # ------------------------------------ internal vs external precedence

    def _note_internal_copy(self) -> None:
        """Stamp the pasteboard's state at the moment of an internal copy.

        ``_copy`` fills only the in-memory clipboard; it never writes to the
        system pasteboard.  So an SVG left there by another app (graphulator,
        Illustrator) outlives it and, now that we read that flavour, would
        win every later paste -- Ctrl+C / Ctrl+V inside Diagrammer would keep
        pasting a stale picture.  Remembering ``changeCount`` lets ``_paste``
        tell "the user copied inside Diagrammer most recently" from "a new
        external SVG has arrived since".
        """
        from diagrammer.io import pasteboard_read

        self._copy_change_count = pasteboard_read.change_count()

    def _internal_copy_is_current(self) -> bool:
        """True when the internal clipboard postdates the pasteboard's content."""
        from diagrammer.io import pasteboard_read

        if not self._clipboard:
            return False
        stamped = getattr(self, "_copy_change_count", None)
        if stamped is None:
            return False
        current = pasteboard_read.change_count()
        # Unreadable (non-macOS) -> keep the old behaviour of letting
        # external SVG win.
        return current is not None and current == stamped

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

        from diagrammer.io import pasteboard_read

        if self._internal_copy_is_current():
            return False

        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        svg_data: bytes | None = None

        # Check for explicit SVG MIME type
        if mime is not None and mime.hasFormat("image/svg+xml"):
            raw = mime.data("image/svg+xml")
            if raw and raw.size() > 0:
                svg_data = bytes(raw)

        # Fallback: check if plain text looks like SVG
        if svg_data is None and mime is not None and mime.hasText():
            text = mime.text().strip()
            if text.startswith("<svg") or text.startswith("<?xml"):
                svg_data = text.encode("utf-8")

        # The usual case on macOS: exporters tag clipboard SVG with the
        # ``public.svg-image`` UTI, which Qt has no mime converter for, so
        # mimeData() offers only the raster flavour.  Read NSPasteboard
        # directly instead -- see diagrammer.io.pasteboard_read.
        if svg_data is None:
            svg_data = pasteboard_read.read_svg()

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

    def _paste_offset(self, fallback: float) -> tuple[float, float]:
        """Translation to apply to clipboard coordinates, as (dx, dy).

        Pasted items land centred on the visible canvas rather than a
        fixed nudge from wherever they were copied: after scrolling to a
        new part of a drawing, a paste that reappears next to the
        original is off screen and has to be hunted down and dragged.

        The delta is rounded to whole grid steps so items that were on
        the grid stay on it, and falls back to *fallback* on both axes
        when there is no view to centre on, or when centring would drop
        the copy exactly on top of what it was copied from.
        """
        from diagrammer.canvas.grid import DEFAULT_GRID_SPACING

        view = getattr(self, "_view", None)
        xs = [e["pos"][0] for e in self._clipboard if "pos" in e]
        ys = [e["pos"][1] for e in self._clipboard if "pos" in e]
        if view is None or not xs:
            return fallback, fallback

        target = view.mapToScene(view.viewport().rect().center())
        dx = target.x() - (min(xs) + max(xs)) / 2.0
        dy = target.y() - (min(ys) + max(ys)) / 2.0

        spacing = getattr(view, "grid_spacing", DEFAULT_GRID_SPACING) or 1.0
        dx = round(dx / spacing) * spacing
        dy = round(dy / spacing) * spacing
        if dx == 0 and dy == 0:
            return fallback, fallback
        return dx, dy

    def _paste(self) -> None:
        """Paste clipboard contents, centred on the visible canvas.

        Checks the system clipboard first for external SVG data (e.g. from
        Illustrator or Inkscape). Falls back to the internal clipboard.
        """
        if self._try_paste_external_svg():
            return
        if not self._clipboard:
            return

        self._scene.undo_stack.beginMacro("Paste")
        import uuid as _uuid
        from diagrammer.commands.add_command import (
            AddComponentCommand,
            AddItemsCommand,
        )
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

        def _add_pasted(item, label: str) -> None:
            """Put *item* in the scene through the undo stack.

            Junctions, annotations, shapes and images used to go in with
            a bare ``scene.addItem``, which left them on the canvas after
            an undo of the paste — the pasted text had to be deleted by
            hand, and the orphaned junctions kept nearby wires routing
            around points the user could no longer see.
            """
            self._scene.undo_stack.push(
                AddItemsCommand(self._scene, [item], label)
            )

        PASTE_OFFSET = 40.0  # fallback nudge when the paste can't be centred
        off_x, off_y = self._paste_offset(PASTE_OFFSET)

        # Items created through a command (components, wires) get a
        # top-of-band Z from the command itself; the ones added straight to
        # the scene below need ``assign_item_z`` or they would paste
        # underneath the diagram they were copied from.

        # First pass: create components, junctions, and annotations
        new_items: list = []  # ComponentItem, JunctionItem, AnnotationItem, or None
        pasted_annotations: list = []
        for entry in self._clipboard:
            if entry["type"] == "component":
                comp_def = self._library.get(entry["def_key"])
                if comp_def is None:
                    new_items.append(None)
                    continue
                pos = QPointF(entry["pos"][0] + off_x, entry["pos"][1] + off_y)
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
                if entry.get("style_overrides"):
                    from diagrammer.models.svg_style_override import (
                        ComponentStyleOverrides,
                    )
                    item.set_style_overrides(
                        ComponentStyleOverrides.from_dict(entry["style_overrides"]))
                # Set exact position after all transforms
                item.setPos(pos)
                item._skip_snap = False
                _remap_group(item, entry)
                new_items.append(item)
            elif entry["type"] == "junction":
                junc = JunctionItem()
                pos = QPointF(entry["pos"][0] + off_x, entry["pos"][1] + off_y)
                junc._skip_snap = True
                junc.setPos(pos)
                junc._skip_snap = False
                if entry.get("end_marker", "none") in ("filled", "open"):
                    junc.end_marker = entry["end_marker"]
                _add_pasted(junc, "Paste junction")
                _remap_group(junc, entry)
                new_items.append(junc)
            elif entry["type"] == "annotation":
                annot = AnnotationItem(entry.get("source_text", "Text"))
                pos = QPointF(entry["pos"][0] + off_x, entry["pos"][1] + off_y)
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
                # Phase C: persistent intrinsic transform fields. Set
                # pos before flip/rotate so the recompute logic sees
                # the final position.
                annot.setPos(pos)
                if entry.get("flip_h"):
                    annot.set_flip_h(True)
                if entry.get("flip_v"):
                    annot.set_flip_v(True)
                angle = entry.get("rotation_angle", entry.get("rotation", 0)) or 0
                if angle:
                    annot.rotate_by(angle)
                _add_pasted(annot, "Paste annotation")
                _remap_group(annot, entry)
                pasted_annotations.append(annot)
            elif entry["type"] in ("rectangle", "ellipse", "line"):
                pos = QPointF(entry["pos"][0] + off_x, entry["pos"][1] + off_y)
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
                # Phase C: persistent intrinsic transform fields.
                if entry.get("flip_h"):
                    shape.set_flip_h(True)
                if entry.get("flip_v"):
                    shape.set_flip_v(True)
                angle = entry.get("rotation_angle", entry.get("rotation", 0)) or 0
                if angle:
                    shape.rotate_by(angle)
                _add_pasted(shape, f"Paste {entry['type']}")
                _remap_group(shape, entry)
                pasted_annotations.append(shape)
            elif entry["type"] == "svg_image":
                import base64
                from diagrammer.items.svg_image_item import SvgImageItem
                pos = QPointF(entry["pos"][0] + off_x, entry["pos"][1] + off_y)
                svg_data = base64.b64decode(entry["svg_data"])
                svg_item = SvgImageItem(
                    svg_data,
                    width=entry.get("width"),
                    height=entry.get("height"),
                )
                if entry.get("flip_h"):
                    svg_item.set_flip_h(True)
                if entry.get("flip_v"):
                    svg_item.set_flip_v(True)
                if entry.get("rotation_angle"):
                    svg_item.rotate_by(entry["rotation_angle"])
                svg_item._skip_snap = True
                svg_item.setPos(pos)
                svg_item._skip_snap = False
                _add_pasted(svg_item, "Paste image")
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
                    wps = entry.get("waypoints") or entry.get("vertices") or []
                    if wps:
                        conn.vertices = [
                            QPointF(v[0] + off_x, v[1] + off_y) for v in wps
                        ]
                    # Restore direction arrows (route-relative fractions)
                    if entry.get("arrows"):
                        from diagrammer.items.connection_item import WireArrow
                        conn.arrows = [
                            WireArrow(t=ad["t"], forward=ad["forward"],
                                      style=ad["style"], size=ad["size"],
                                      line_width=ad["line_width"])
                            for ad in entry["arrows"]
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
