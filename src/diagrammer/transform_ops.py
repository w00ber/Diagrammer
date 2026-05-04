"""Transform operations mixin for MainWindow."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPointF

logger = logging.getLogger(__name__)


def _scene_center(item) -> QPointF:
    """Return the item's intrinsic anchor in scene coordinates.

    After Phase C every persistent item type (component, annotation,
    shape, line, junction) exposes ``intrinsic_anchor`` returning a
    local-coords pivot. Mapping that through the item's own transform
    gives the visible center used for group rotation, flip, and any
    other rigid-body operation — one formula across all item types.
    """
    if hasattr(item, "intrinsic_anchor"):
        return item.mapToScene(item.intrinsic_anchor())
    return item.mapToScene(QPointF(0, 0))


class TransformMixin:
    """Mixin providing rotate, flip, and align operations.

    Expects the host class to have ``_scene`` and ``_gather_connected_junctions()``
    (provided by :class:`ClipboardMixin`).
    """

    # ------------------------------------------------- closed-polygon helper

    def _rotate_closed_polygons(self, conns, degrees: float,
                                pivot: QPointF | None = None) -> None:
        """Rotate closed polygon waypoints around *pivot* (default: centroid)."""
        import math
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
        from diagrammer.items.junction_item import JunctionItem

        for conn in conns:
            wps = conn.vertices
            if not wps:
                continue
            if pivot is None:
                cx = sum(w.x() for w in wps) / len(wps)
                cy = sum(w.y() for w in wps) / len(wps)
            else:
                cx, cy = pivot.x(), pivot.y()

            rad = math.radians(degrees)
            cos_a, sin_a = math.cos(rad), math.sin(rad)

            new_wps = [QPointF(cx + (w.x() - cx) * cos_a - (w.y() - cy) * sin_a,
                               cy + (w.x() - cx) * sin_a + (w.y() - cy) * cos_a)
                       for w in wps]
            cmd = EditWaypointsCommand(conn, [QPointF(w) for w in wps], new_wps)
            self._scene.undo_stack.push(cmd)

            # Move the junction to track rotation
            junc = conn.source_port.component
            if isinstance(junc, JunctionItem):
                old_pos = junc.pos()
                dx, dy = old_pos.x() - cx, old_pos.y() - cy
                new_pos = QPointF(cx + dx * cos_a - dy * sin_a,
                                  cy + dx * sin_a + dy * cos_a)
                junc._skip_snap = True
                move_cmd = MoveComponentCommand(junc, old_pos, new_pos)
                self._scene.undo_stack.push(move_cmd)
                junc._skip_snap = False

    # ---------------------------------------------------------- rotate 90

    def _rotate_selected(self, degrees: float) -> None:
        """Rotate the selection rigidly by *degrees* (90 / 180 / -90 etc.).

        With Phase B's port-local waypoints and Phase C's intrinsic
        transform model, the wire shape automatically follows its
        endpoint components — so this no longer needs to pre-capture
        the expanded route or force ROUTE_DIRECT. Each item rotates
        internally AND orbits around the group center; wires take
        care of themselves.
        """
        import math
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.transform_command import (
            RotateComponentCommand,
            RotateItemCommand,
        )
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import ShapeItem

        comp_targets = [i for i in self._scene.selectedItems() if isinstance(i, ComponentItem)]
        junc_targets = [i for i in self._scene.selectedItems() if isinstance(i, JunctionItem)]
        annot_targets = [i for i in self._scene.selectedItems() if isinstance(i, AnnotationItem)]
        shape_targets = [i for i in self._scene.selectedItems() if isinstance(i, ShapeItem)]
        conn_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, ConnectionItem)]

        movable = comp_targets + junc_targets + annot_targets + shape_targets
        if not movable and not conn_targets:
            return

        # Standalone connection rotation (closed polygons or free wires).
        # Closed polygons still need explicit waypoint rotation: their
        # anchor (a junction) has no rotation of its own, so port-local
        # offsets don't help — we rotate the offsets directly.
        if not movable and conn_targets:
            closed = [c for c in conn_targets if c.closed]
            if closed:
                self._scene.undo_stack.beginMacro(f"Rotate {len(closed)} closed polygon(s)")
                self._rotate_closed_polygons(closed, degrees)
                self._scene.undo_stack.endMacro()
                self._scene.update_connections()
                return

        # Auto-include connected junctions and endpoint junctions from
        # selected connections so they participate in the rotation.
        extra_juncs = self._gather_connected_junctions(movable)
        junc_targets.extend(extra_juncs)
        for conn in conn_targets:
            for port in (conn.source_port, conn.target_port):
                junc = port.component
                if isinstance(junc, JunctionItem) and junc not in junc_targets:
                    junc_targets.append(junc)

        movable = comp_targets + junc_targets + annot_targets + shape_targets

        self._scene.undo_stack.beginMacro(f"Rotate {len(movable)} items")

        if len(movable) == 1 and len(comp_targets) == 1:
            self._scene.undo_stack.push(RotateComponentCommand(comp_targets[0], degrees))
        elif len(movable) == 1 and isinstance(movable[0], (AnnotationItem, ShapeItem)):
            self._scene.undo_stack.push(RotateItemCommand(movable[0], degrees))
        else:
            # Group rotation: each item rotates internally AND orbits
            # around the group center. One scene-center formula across
            # every item type via _scene_center.
            scene_centers = [_scene_center(item) for item in movable]
            gcx = sum(p.x() for p in scene_centers) / len(scene_centers)
            gcy = sum(p.y() for p in scene_centers) / len(scene_centers)
            rad = math.radians(degrees)
            cos_a, sin_a = math.cos(rad), math.sin(rad)

            target_centers = []
            for sc in scene_centers:
                dx, dy = sc.x() - gcx, sc.y() - gcy
                target_centers.append(QPointF(
                    gcx + dx * cos_a - dy * sin_a,
                    gcy + dx * sin_a + dy * cos_a,
                ))

            for i, item in enumerate(movable):
                if isinstance(item, ComponentItem):
                    self._scene.undo_stack.push(RotateComponentCommand(item, degrees))
                elif isinstance(item, (AnnotationItem, ShapeItem)):
                    self._scene.undo_stack.push(RotateItemCommand(item, degrees))
                cur_sc = _scene_center(item)
                offset = target_centers[i] - cur_sc
                new_pos = item.pos() + offset
                if hasattr(item, '_skip_snap'):
                    item._skip_snap = True
                self._scene.undo_stack.push(MoveComponentCommand(item, item.pos(), new_pos))
                if hasattr(item, '_skip_snap'):
                    item._skip_snap = False

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    # -------------------------------------------------------- fine rotate

    def _fine_rotate_selected(self, degrees: float) -> None:
        """Fine-rotate the selection by an arbitrary *degrees* around the
        rotation pivot port (or group center if no pivot is set).

        For non-90° rotations the orthogonal router can't represent the
        rotated wire shape, so internal wires (both endpoints in the
        rotated set) switch to ROUTE_DIRECT — wrapped in
        ``SetRoutingModeCommand`` so undo restores the previous mode.
        Wire SHAPE follows automatically via Phase B's port-local
        waypoints; no pre-capture needed.
        """
        import math
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import SetRoutingModeCommand
        from diagrammer.commands.transform_command import (
            RotateComponentCommand,
            RotateItemCommand,
        )
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ROUTE_DIRECT, ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import ShapeItem

        comp_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, ComponentItem)]
        junc_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, JunctionItem)]
        annot_targets = [i for i in self._scene.selectedItems()
                         if isinstance(i, AnnotationItem)]
        shape_targets = [i for i in self._scene.selectedItems()
                         if isinstance(i, ShapeItem)]
        conn_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, ConnectionItem)]

        movable = comp_targets + junc_targets + annot_targets + shape_targets
        if not movable and not conn_targets:
            return

        if not movable and conn_targets:
            closed = [c for c in conn_targets if c.closed]
            if closed:
                self._scene.undo_stack.beginMacro(f"Fine rotate {len(closed)} closed polygon(s)")
                self._rotate_closed_polygons(closed, degrees)
                self._scene.undo_stack.endMacro()
                self._scene.update_connections()
                return

        extra_juncs = self._gather_connected_junctions(movable)
        junc_targets.extend(extra_juncs)
        for conn in conn_targets:
            for port in (conn.source_port, conn.target_port):
                junc = port.component
                if isinstance(junc, JunctionItem) and junc not in junc_targets:
                    junc_targets.append(junc)

        movable = comp_targets + junc_targets + annot_targets + shape_targets

        self._scene.undo_stack.beginMacro(f"Fine rotate {len(movable)} items")

        # Pivot: explicit port pin > single component's first port > group center.
        pivot = self._scene.rotation_pivot_port
        if pivot is not None:
            pivot_scene = pivot.scene_center()
        elif (len(movable) == 1 and len(comp_targets) == 1
                and comp_targets[0].ports):
            # Single component with at least one port: spin around
            # its first port. Decorative (port-less) components fall
            # through to the group-center branch below.
            pivot_scene = comp_targets[0].ports[0].scene_center()
        else:
            scene_centers = [_scene_center(item) for item in movable]
            pivot_scene = QPointF(
                sum(p.x() for p in scene_centers) / len(scene_centers),
                sum(p.y() for p in scene_centers) / len(scene_centers),
            )

        rad = math.radians(degrees)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        px, py = pivot_scene.x(), pivot_scene.y()

        for item in movable:
            if isinstance(item, ComponentItem):
                self._scene.undo_stack.push(RotateComponentCommand(item, degrees))
            elif isinstance(item, (AnnotationItem, ShapeItem)):
                self._scene.undo_stack.push(RotateItemCommand(item, degrees))

            cur_sc = _scene_center(item)
            dx, dy = cur_sc.x() - px, cur_sc.y() - py
            new_sc = QPointF(px + dx * cos_a - dy * sin_a,
                             py + dx * sin_a + dy * cos_a)
            offset = new_sc - cur_sc
            new_pos = item.pos() + offset
            if hasattr(item, '_skip_snap'):
                item._skip_snap = True
            self._scene.undo_stack.push(MoveComponentCommand(item, item.pos(), new_pos))
            if hasattr(item, '_skip_snap'):
                item._skip_snap = False

        # For non-90° rotations the orthogonal router can't represent
        # the rotated wire shape, so internal wires (both endpoints
        # rotated) switch to direct routing. Port-local waypoints
        # (Phase B) handle the shape preservation automatically; this
        # command exists only to keep ortho from re-routing on top.
        if degrees % 90 != 0:
            item_ids = set(id(c) for c in movable)
            for citem in self._scene.items():
                if not isinstance(citem, ConnectionItem):
                    continue
                if (id(citem.source_port.component) in item_ids and
                        id(citem.target_port.component) in item_ids and
                        citem.routing_mode != ROUTE_DIRECT):
                    self._scene.undo_stack.push(
                        SetRoutingModeCommand(citem, ROUTE_DIRECT)
                    )

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    # -------------------------------------------------------------- flip

    def _flip_selected(self, horizontal: bool) -> None:
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.transform_command import FlipComponentCommand
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import ShapeItem

        selected_comps = [i for i in self._scene.selectedItems() if isinstance(i, ComponentItem)]
        selected_juncs = [i for i in self._scene.selectedItems() if isinstance(i, JunctionItem)]
        selected_annots = [i for i in self._scene.selectedItems() if isinstance(i, AnnotationItem)]
        selected_shapes = [i for i in self._scene.selectedItems() if isinstance(i, ShapeItem)]
        selected_conns = [i for i in self._scene.selectedItems() if isinstance(i, ConnectionItem)]

        all_movable = selected_comps + selected_juncs + selected_annots + selected_shapes
        extra_juncs = self._gather_connected_junctions(all_movable)
        selected_juncs.extend(extra_juncs)

        # Include endpoint junctions from selected connections
        for conn in selected_conns:
            for port in (conn.source_port, conn.target_port):
                junc = port.component
                if isinstance(junc, JunctionItem) and junc not in selected_juncs:
                    selected_juncs.append(junc)

        all_movable = selected_comps + selected_juncs + selected_annots + selected_shapes

        axis = "H" if horizontal else "V"
        self._scene.undo_stack.beginMacro(f"Flip {axis} {len(all_movable)} items")

        if len(all_movable) <= 1:
            for item in selected_comps:
                cmd = FlipComponentCommand(item, horizontal)
                self._scene.undo_stack.push(cmd)
            from diagrammer.commands.transform_command import FlipItemCommand
            for item in selected_annots + selected_shapes:
                cmd = FlipItemCommand(item, horizontal)
                self._scene.undo_stack.push(cmd)
            self._scene.undo_stack.endMacro()
            self._scene.update_connections()
            return

        # Compute group center using mapToScene for consistency with
        # transforms. After Phase C every item exposes intrinsic_anchor,
        # so the module-level _scene_center handles all types.
        centers = [_scene_center(item) for item in all_movable]
        group_cx = sum(p.x() for p in centers) / len(centers)
        group_cy = sum(p.y() for p in centers) / len(centers)

        # Flip and mirror components using scene-center mirroring.
        # Compute each component's scene center, mirror it, then derive
        # the new position from the offset.
        for comp in selected_comps:
            old_sc = _scene_center(comp)
            cmd = FlipComponentCommand(comp, horizontal)
            self._scene.undo_stack.push(cmd)
            # After flip, the scene center has changed — recalculate
            new_sc_after_flip = _scene_center(comp)
            # Mirror the OLD scene center around the group center
            if horizontal:
                mirrored_x = 2 * group_cx - old_sc.x()
                offset_x = mirrored_x - new_sc_after_flip.x()
                new_pos = QPointF(comp.pos().x() + offset_x, comp.pos().y())
            else:
                mirrored_y = 2 * group_cy - old_sc.y()
                offset_y = mirrored_y - new_sc_after_flip.y()
                new_pos = QPointF(comp.pos().x(), comp.pos().y() + offset_y)
            comp._skip_snap = True
            move_cmd = MoveComponentCommand(comp, comp.pos(), new_pos)
            self._scene.undo_stack.push(move_cmd)
            comp._skip_snap = False

        # Flip annotations and shapes internally
        from diagrammer.commands.transform_command import FlipItemCommand
        for item in selected_annots + selected_shapes:
            cmd = FlipItemCommand(item, horizontal)
            self._scene.undo_stack.push(cmd)

        # Mirror non-component items (junctions, annotations, shapes)
        for item in selected_juncs + selected_annots + selected_shapes:
            old_pos = item.pos()
            sc = _scene_center(item)
            if horizontal:
                new_sc_x = 2 * group_cx - sc.x()
                new_pos = QPointF(old_pos.x() + (new_sc_x - sc.x()), old_pos.y())
            else:
                new_sc_y = 2 * group_cy - sc.y()
                new_pos = QPointF(old_pos.x(), old_pos.y() + (new_sc_y - sc.y()))
            if hasattr(item, '_skip_snap'):
                item._skip_snap = True
            move_cmd = MoveComponentCommand(item, old_pos, new_pos)
            self._scene.undo_stack.push(move_cmd)
            if hasattr(item, '_skip_snap'):
                item._skip_snap = False

        # Internal-wire waypoint mirroring is handled automatically
        # by Phase B's port-local offsets: when both endpoint
        # components flip, their port-local waypoint offsets flip
        # along with the parents — the wire shape mirrors as a
        # rigid body without explicit per-waypoint code here.

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    # -------------------------------------------------------------- align

    def _align_selected(self, direction: str) -> None:
        """Align components by ports or centers.

        Priority:
        1. If ports have been Ctrl+click selected (>=2), align by those ports.
        2. Otherwise, align selected components by their centers.
        """
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.items.component_item import ComponentItem

        alignment_ports = self._scene.alignment_ports

        if len(alignment_ports) >= 2:
            # Align by explicitly selected ports
            anchors: list[tuple[ComponentItem, QPointF]] = [
                (port.component, port.scene_center())
                for port in alignment_ports
            ]
        else:
            # Align selected components by their centers
            selected = [
                item for item in self._scene.selectedItems()
                if isinstance(item, ComponentItem)
            ]
            if len(selected) < 2:
                return
            anchors = []
            for comp in selected:
                center = comp.mapToScene(
                    QPointF(comp._width / 2, comp._height / 2)
                )
                anchors.append((comp, center))

        if len(anchors) < 2:
            return

        self._scene.undo_stack.beginMacro(f"Align {direction} {len(anchors)} items")

        if direction == "horizontal":
            avg_y = sum(pos.y() for _, pos in anchors) / len(anchors)
            for comp, anchor_pos in anchors:
                dy = avg_y - anchor_pos.y()
                if abs(dy) > 0.5:
                    old_pos = comp.pos()
                    new_pos = QPointF(old_pos.x(), old_pos.y() + dy)
                    # Bypass snap so the port lands at the exact target
                    comp._skip_snap = True
                    cmd = MoveComponentCommand(comp, old_pos, new_pos)
                    self._scene.undo_stack.push(cmd)
                    comp._skip_snap = False
        elif direction == "vertical":
            avg_x = sum(pos.x() for _, pos in anchors) / len(anchors)
            for comp, anchor_pos in anchors:
                dx = avg_x - anchor_pos.x()
                if abs(dx) > 0.5:
                    old_pos = comp.pos()
                    new_pos = QPointF(old_pos.x() + dx, old_pos.y())
                    comp._skip_snap = True
                    cmd = MoveComponentCommand(comp, old_pos, new_pos)
                    self._scene.undo_stack.push(cmd)
                    comp._skip_snap = False

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()
        self._scene.clear_alignment_ports()
