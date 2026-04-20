"""Transform operations mixin for MainWindow."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPointF

logger = logging.getLogger(__name__)


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
        import math
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
        from diagrammer.commands.transform_command import RotateComponentCommand
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import ShapeItem

        # Collect all movable selected items
        comp_targets = [i for i in self._scene.selectedItems() if isinstance(i, ComponentItem)]
        junc_targets = [i for i in self._scene.selectedItems() if isinstance(i, JunctionItem)]
        annot_targets = [i for i in self._scene.selectedItems() if isinstance(i, AnnotationItem)]
        shape_targets = [i for i in self._scene.selectedItems() if isinstance(i, ShapeItem)]
        conn_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, ConnectionItem)]

        movable = comp_targets + junc_targets + annot_targets + shape_targets
        if not movable and not conn_targets:
            return

        # Standalone connection rotation (closed polygons or free wires)
        if not movable and conn_targets:
            closed = [c for c in conn_targets if c.closed]
            if closed:
                self._scene.undo_stack.beginMacro(f"Rotate {len(closed)} closed polygon(s)")
                self._rotate_closed_polygons(closed, degrees)
                self._scene.undo_stack.endMacro()
                self._scene.update_connections()
                return

        # Auto-include connected junctions
        extra_juncs = self._gather_connected_junctions(movable)
        junc_targets.extend(extra_juncs)

        # Include endpoint junctions from selected connections
        # so they participate in group rotation
        for conn in conn_targets:
            for port in (conn.source_port, conn.target_port):
                junc = port.component
                if isinstance(junc, JunctionItem) and junc not in junc_targets:
                    junc_targets.append(junc)

        movable = comp_targets + junc_targets + annot_targets + shape_targets

        self._scene.undo_stack.beginMacro(f"Rotate {len(movable)} items")

        if len(movable) == 1 and len(comp_targets) == 1:
            # Single component: rotate around its own center
            cmd = RotateComponentCommand(comp_targets[0], degrees)
            self._scene.undo_stack.push(cmd)
        elif len(movable) == 1 and isinstance(movable[0], (AnnotationItem, ShapeItem)):
            # Single annotation/shape: rotate around its own center
            from diagrammer.commands.transform_command import RotateItemCommand
            cmd = RotateItemCommand(movable[0], degrees)
            self._scene.undo_stack.push(cmd)
        else:
            # Group rotation: rigid-body
            # Compute scene center for each item
            def _scene_center(item):
                if isinstance(item, ComponentItem):
                    return item.mapToScene(QPointF(item._def.width / 2, item._def.height / 2))
                elif isinstance(item, AnnotationItem):
                    br = item.boundingRect()
                    return item.mapToScene(br.center())
                elif isinstance(item, ShapeItem):
                    return item.mapToScene(QPointF(item.shape_width / 2, item.shape_height / 2))
                else:
                    return item.mapToScene(QPointF(0, 0))

            scene_centers = [_scene_center(item) for item in movable]
            gcx = sum(p.x() for p in scene_centers) / len(scene_centers)
            gcy = sum(p.y() for p in scene_centers) / len(scene_centers)
            rad = math.radians(degrees)
            cos_a, sin_a = math.cos(rad), math.sin(rad)

            # Pre-compute orbited target positions
            target_centers = []
            for sc in scene_centers:
                dx, dy = sc.x() - gcx, sc.y() - gcy
                target_centers.append(QPointF(
                    gcx + dx * cos_a - dy * sin_a,
                    gcy + dx * sin_a + dy * cos_a,
                ))

            from diagrammer.commands.transform_command import RotateItemCommand

            # Pre-capture the FULL expanded route (not just user waypoints)
            # so that after rotation the wire shape is preserved exactly.
            # The expanded points become the new waypoints, and _expand_route
            # between adjacent points produces near-straight segments.
            pre_captured_expanded: dict[int, list[QPointF]] = {}
            pre_captured_routing: dict[int, str] = {}
            item_ids = set(id(c) for c in movable)
            for citem in self._scene.items():
                if not isinstance(citem, ConnectionItem):
                    continue
                if (id(citem.source_port.component) in item_ids and
                        id(citem.target_port.component) in item_ids):
                    # Capture the full expanded route minus endpoints (which are port positions)
                    expanded = citem.all_points()
                    if len(expanded) > 2:
                        pre_captured_expanded[id(citem)] = [QPointF(p) for p in expanded[1:-1]]
                    elif expanded:
                        mid = QPointF(
                            (expanded[0].x() + expanded[-1].x()) / 2,
                            (expanded[0].y() + expanded[-1].y()) / 2,
                        )
                        pre_captured_expanded[id(citem)] = [mid]
                    else:
                        pre_captured_expanded[id(citem)] = []
                    pre_captured_routing[id(citem)] = citem.routing_mode

            for i, item in enumerate(movable):
                if isinstance(item, ComponentItem):
                    # Rotate internally AND orbit
                    cmd = RotateComponentCommand(item, degrees)
                    self._scene.undo_stack.push(cmd)
                elif isinstance(item, (AnnotationItem, ShapeItem)):
                    # Rotate internally (Qt rotation) AND orbit
                    cmd = RotateItemCommand(item, degrees)
                    self._scene.undo_stack.push(cmd)
                # All items: orbit position around group center
                cur_sc = _scene_center(item)
                offset = target_centers[i] - cur_sc
                new_pos = item.pos() + offset
                if hasattr(item, '_skip_snap'):
                    item._skip_snap = True
                move_cmd = MoveComponentCommand(item, item.pos(), new_pos)
                self._scene.undo_stack.push(move_cmd)
                if hasattr(item, '_skip_snap'):
                    item._skip_snap = False

            # Rotate internal connection expanded-route points around group center,
            # set them as new waypoints, and switch to ROUTE_DIRECT so the
            # rotated shape is preserved exactly (ortho routing would re-route).
            from diagrammer.items.connection_item import ROUTE_DIRECT
            for item in self._scene.items():
                if not isinstance(item, ConnectionItem):
                    continue
                if id(item) not in pre_captured_expanded:
                    continue
                old_expanded = pre_captured_expanded[id(item)]

                if old_expanded:
                    new_wps = []
                    for wp in old_expanded:
                        dx, dy = wp.x() - gcx, wp.y() - gcy
                        new_wps.append(QPointF(
                            gcx + dx * cos_a - dy * sin_a,
                            gcy + dx * sin_a + dy * cos_a,
                        ))
                    cmd = EditWaypointsCommand(item, [QPointF(w) for w in item.vertices], new_wps)
                    self._scene.undo_stack.push(cmd)
                    # Switch to direct routing so the rotated shape is preserved
                    if item.routing_mode != ROUTE_DIRECT:
                        item.routing_mode = ROUTE_DIRECT

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    # -------------------------------------------------------- fine rotate

    def _fine_rotate_selected(self, degrees: float) -> None:
        """Fine-rotate selected items as a rigid body around the group center.

        Uses the same rigid-body approach as _rotate_selected: each component
        rotates internally AND orbits around the pivot. Non-component items
        (annotations, shapes, junctions) orbit only.
        """
        import math
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
        from diagrammer.commands.transform_command import RotateComponentCommand
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import ShapeItem

        comp_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, ComponentItem) and i.ports]
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

        # Standalone connection rotation (closed polygons or free wires)
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

        # Include endpoint junctions from selected connections
        for conn in conn_targets:
            for port in (conn.source_port, conn.target_port):
                junc = port.component
                if isinstance(junc, JunctionItem) and junc not in junc_targets:
                    junc_targets.append(junc)

        movable = comp_targets + junc_targets + annot_targets + shape_targets

        self._scene.undo_stack.beginMacro(f"Fine rotate {len(movable)} items")

        # Use the same rigid-body rotation as _rotate_selected
        def _scene_center(item):
            if isinstance(item, ComponentItem):
                return item.mapToScene(QPointF(item._def.width / 2, item._def.height / 2))
            elif isinstance(item, AnnotationItem):
                return item.mapToScene(item.boundingRect().center())
            elif isinstance(item, ShapeItem):
                return item.mapToScene(QPointF(item.shape_width / 2, item.shape_height / 2))
            else:
                return item.mapToScene(QPointF(0, 0))

        # Determine pivot
        pivot = self._scene.rotation_pivot_port
        if pivot is not None:
            pivot_scene = pivot.scene_center()
        elif len(movable) == 1 and len(comp_targets) == 1:
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

        from diagrammer.commands.transform_command import RotateItemCommand

        # Pre-capture the FULL expanded route so the rotated shape is preserved
        pre_captured_expanded: dict[int, list[QPointF]] = {}
        item_ids = set(id(c) for c in movable)
        for citem in self._scene.items():
            if not isinstance(citem, ConnectionItem):
                continue
            if (id(citem.source_port.component) in item_ids and
                    id(citem.target_port.component) in item_ids):
                expanded = citem.all_points()
                if len(expanded) > 2:
                    pre_captured_expanded[id(citem)] = [QPointF(p) for p in expanded[1:-1]]
                elif expanded:
                    mid = QPointF(
                        (expanded[0].x() + expanded[-1].x()) / 2,
                        (expanded[0].y() + expanded[-1].y()) / 2,
                    )
                    pre_captured_expanded[id(citem)] = [mid]
                else:
                    pre_captured_expanded[id(citem)] = []

        for item in movable:
            if isinstance(item, ComponentItem):
                # Rotate internally
                cmd = RotateComponentCommand(item, degrees)
                self._scene.undo_stack.push(cmd)
            elif isinstance(item, (AnnotationItem, ShapeItem)):
                cmd = RotateItemCommand(item, degrees)
                self._scene.undo_stack.push(cmd)

            # Orbit around pivot (all item types)
            cur_sc = _scene_center(item)
            dx, dy = cur_sc.x() - px, cur_sc.y() - py
            new_sc = QPointF(px + dx * cos_a - dy * sin_a,
                             py + dx * sin_a + dy * cos_a)
            offset = new_sc - cur_sc
            new_pos = item.pos() + offset
            if hasattr(item, '_skip_snap'):
                item._skip_snap = True
            move_cmd = MoveComponentCommand(item, item.pos(), new_pos)
            self._scene.undo_stack.push(move_cmd)
            if hasattr(item, '_skip_snap'):
                item._skip_snap = False

        # Rotate internal connection expanded-route points around pivot,
        # switch to ROUTE_DIRECT to preserve the rotated shape.
        from diagrammer.items.connection_item import ROUTE_DIRECT
        for item in self._scene.items():
            if not isinstance(item, ConnectionItem):
                continue
            if id(item) not in pre_captured_expanded:
                continue
            old_expanded = pre_captured_expanded[id(item)]
            if old_expanded:
                new_wps = [QPointF(px + (w.x()-px)*cos_a - (w.y()-py)*sin_a,
                                   py + (w.x()-px)*sin_a + (w.y()-py)*cos_a)
                           for w in old_expanded]
                cmd = EditWaypointsCommand(item, [QPointF(w) for w in item.vertices], new_wps)
                self._scene.undo_stack.push(cmd)
                # Switch to direct routing so the rotated shape is preserved
                if item.routing_mode != ROUTE_DIRECT:
                    item.routing_mode = ROUTE_DIRECT

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    # -------------------------------------------------------------- flip

    def _flip_selected(self, horizontal: bool) -> None:
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
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

        # Compute group center using mapToScene for consistency with transforms.
        # For components, use the base (unstretched) center as the reference
        # since the flip transform pivots around the base center.
        def _scene_center(item):
            if isinstance(item, ComponentItem):
                return item.mapToScene(QPointF(item._width / 2, item._height / 2))
            elif isinstance(item, AnnotationItem):
                br = item.boundingRect()
                return item.mapToScene(br.center())
            elif isinstance(item, ShapeItem):
                return item.mapToScene(QPointF(item.shape_width / 2, item.shape_height / 2))
            else:
                return item.mapToScene(QPointF(0, 0))

        centers = [_scene_center(item) for item in all_movable]
        group_cx = sum(p.x() for p in centers) / len(centers)
        group_cy = sum(p.y() for p in centers) / len(centers)

        item_set = set(id(c) for c in all_movable)

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

        # Mirror connection waypoints
        for item in self._scene.items():
            if not isinstance(item, ConnectionItem):
                continue
            if (id(item.source_port.component) in item_set and
                    id(item.target_port.component) in item_set):
                old_wps = [QPointF(w) for w in item.vertices]
                if old_wps:
                    new_wps = []
                    for wp in old_wps:
                        if horizontal:
                            new_wps.append(QPointF(2 * group_cx - wp.x(), wp.y()))
                        else:
                            new_wps.append(QPointF(wp.x(), 2 * group_cy - wp.y()))
                    cmd = EditWaypointsCommand(item, old_wps, new_wps)
                    self._scene.undo_stack.push(cmd)

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
