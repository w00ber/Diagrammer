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


def _internal_wires(scene, items) -> list:
    """Return ConnectionItems whose both endpoints are in *items*."""
    from diagrammer.items.connection_item import ConnectionItem
    item_ids = set(id(c) for c in items)
    out = []
    for c in scene.items():
        if not isinstance(c, ConnectionItem):
            continue
        if (id(c.source_port.component) in item_ids and
                id(c.target_port.component) in item_ids):
            out.append(c)
    return out


def _rotation_map(center: QPointF, degrees: float):
    """Return ``f(p)`` rotating scene point *p* by *degrees* about *center*.

    This is the same orbit every item's anchor follows during a group
    rotation, so applying it to wire geometry keeps the two congruent.
    Right angles use exact cos/sin so repeated 90-degree turns don't
    accumulate float drift (four of them are an exact identity).
    """
    import math
    quarter = degrees / 90.0
    if quarter == int(quarter):
        cos_a, sin_a = ((1, 0), (0, 1), (-1, 0), (0, -1))[int(quarter) % 4]
    else:
        rad = math.radians(degrees)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
    cx, cy = center.x(), center.y()

    def apply(p: QPointF) -> QPointF:
        dx, dy = p.x() - cx, p.y() - cy
        return QPointF(cx + dx * cos_a - dy * sin_a,
                       cy + dx * sin_a + dy * cos_a)
    return apply


def _mirror_map(center: QPointF, horizontal: bool):
    """Return ``f(p)`` mirroring scene point *p* through *center*
    (across the vertical axis if *horizontal*, else the horizontal one)."""
    cx, cy = center.x(), center.y()
    if horizontal:
        return lambda p: QPointF(2 * cx - p.x(), p.y())
    return lambda p: QPointF(p.x(), 2 * cy - p.y())


def _snap_spacing(scene, items) -> float | None:
    """Grid spacing that a quarter turn or flip of *items* should keep.

    None when no view shows the scene, grid snapping is off, or nothing
    in *items* has ports (text and shapes keep their exact pivot).
    """
    from diagrammer.items.component_item import ComponentItem
    from diagrammer.items.junction_item import JunctionItem

    if not any(isinstance(i, (ComponentItem, JunctionItem)) for i in items):
        return None
    for view in scene.views():
        if hasattr(view, "grid_spacing"):
            if not getattr(view, "_snap_enabled", True):
                return None
            return view.grid_spacing or None
    return None


def _grid_pivot_candidates(center: QPointF, spacing: float, degrees=None,
                           horizontal=None) -> list[QPointF]:
    """Pivots near *center* about which the transform maps the grid onto
    itself, so ports on grid points stay on grid points.

    With grid spacing g: a 90 or 270 degree turn about c does this iff c
    is a grid point or the centre of a grid cell; a 180 degree turn iff
    c lies on the half grid (multiples of g/2); a flip iff its mirror
    line lies on a half-grid line. Returns [] for turns that cannot keep
    the grid (fine rotation).
    """
    import math
    g, h = spacing, spacing / 2
    cx, cy = center.x(), center.y()
    if horizontal is not None:
        if horizontal:
            k = math.floor(cx / h)
            return [QPointF(i * h, cy) for i in (k, k + 1)]
        k = math.floor(cy / h)
        return [QPointF(cx, i * h) for i in (k, k + 1)]
    quarter = degrees / 90.0
    if quarter != int(quarter) or int(quarter) % 4 == 0:
        return []
    if int(quarter) % 2 == 0:
        i, j = math.floor(cx / h), math.floor(cy / h)
        return [QPointF(a * h, b * h) for a in (i, i + 1) for b in (j, j + 1)]
    i, j = math.floor(cx / g), math.floor(cy / g)
    corners = [QPointF(a * g, b * g) for a in (i, i + 1) for b in (j, j + 1)]
    centres = [QPointF((a + 0.5) * g, (b + 0.5) * g)
               for a in (i - 1, i, i + 1) for b in (j - 1, j, j + 1)]
    return corners + centres


def _router_redraws(wire, points: list[QPointF]) -> bool:
    """True if *wire*'s router, in its current mode, draws each pair of
    consecutive *points* as one straight segment — so with a waypoint at
    every bend, re-routing reproduces this exact polyline.

    Holds for an orthogonal route through any quarter turn or flip
    (H/V segments stay H/V, 45-degree ones stay 45-degree), and fails
    once a fine rotation tilts a segment.
    """
    pairs = list(zip(points, points[1:]))
    if wire.closed and points:
        pairs.append((points[-1], points[0]))
    return all(len(wire._expand_route(a, b)) == 2 for a, b in pairs)


def _route_interior(wire) -> list[QPointF]:
    """Interior vertices of *wire*'s visible route, in scene coords.

    Uses ``routed_points()``, not ``all_points()[1:-1]``: the latter
    keeps the render-only stubs' inner ends, i.e. the port positions
    themselves, as waypoints — zero-length segments that flatten the
    rounded corner at the port and pile up by two more on every
    transform. Coincident points are dropped too.
    """
    route = wire.routed_points()
    if wire._closed:
        # Closed polygons: the expanded route IS the polygon geometry
        # (source and target ports are the same junction port; there
        # is no port-endpoint pair to strip). Capture every vertex.
        return [QPointF(p) for p in route]
    if len(route) < 2:
        return []

    def near(a: QPointF, b: QPointF) -> bool:
        return abs(a.x() - b.x()) + abs(a.y() - b.y()) < 1e-3

    interior: list[QPointF] = []
    for p in route[1:-1]:
        if not near(p, interior[-1] if interior else route[0]):
            interior.append(QPointF(p))
    if interior and near(interior[-1], route[-1]):
        interior.pop()
    return interior


def _capture_internal_wire_shapes(wires) -> list:
    """Snapshot each internal wire's visible route (scene coords), plus
    the routing mode and the pre-transform anchor list (for undo).

    Auto-routed bends and closed-polygon vertices aren't stored as
    user waypoints, so a transform on the parent components would let
    the orthogonal router re-derive a different shape from the
    transformed key-points. Capturing every interior point lets
    ``_apply_internal_wire_shapes`` replay the route as a rigid body.

    Returns ``[(wire, [QPointF, ...], pre_anchors, orig_routing_mode), ...]``.
    """
    from diagrammer.items.connection_item import Waypoint

    snapshots = []
    for wire in wires:
        # Snapshot the pre-transform anchor list so undo can restore
        # exactly (Waypoint is a small dataclass-like; copy by hand).
        pre_anchors = [Waypoint(a.anchor, a.dx, a.dy) for a in wire._anchors]
        snapshots.append(
            (wire, _route_interior(wire), pre_anchors, wire.routing_mode))
    return snapshots


def _apply_internal_wire_shapes(undo_stack, snapshots, transform) -> None:
    """Map each captured route through *transform* and install it as the
    wire's anchors — all undoable.

    The wire keeps its routing mode when its router redraws the mapped
    route unchanged (an orthogonal route through a quarter turn or a
    flip), so later edits still route orthogonally. Otherwise (a fine
    rotation tilts the segments) it is pinned to ROUTE_DIRECT, which
    draws the waypoints as given.

    Call this AFTER the items have been transformed. *transform* must
    be the same rigid map the items followed (``_rotation_map`` /
    ``_mirror_map``); the mapped points are then bound to ports in
    their post-transform frames, so the wire lands congruent with the
    components regardless of which port each point binds to.

    Relying on the port frames to carry the transform instead (binding
    before, resolving after) breaks for junction ports: junctions only
    translate during a group transform, never rotate or flip, so any
    point anchored to one — e.g. the midpoint of a lead drawn from a
    free end toward a component — kept its unrotated offset (issue #34).
    """
    from diagrammer.commands.connect_command import SetRoutingModeCommand
    from diagrammer.items.connection_item import ROUTE_DIRECT
    from PySide6.QtGui import QUndoCommand

    class _SetAnchorsCommand(QUndoCommand):
        """Atomic install/restore of a wire's port-local anchors.

        EditWaypointsCommand goes scene→port-local on each redo, which
        re-binds against whatever the port frames are at that moment.
        Installing verbatim is exact: the macro's redo replays the item
        transforms first, so the frames match the ones bound here.
        """

        def __init__(self, wire, new_anchors, old_anchors):
            super().__init__()
            self.setText("Preserve wire shape through transform")
            self._wire = wire
            self._new = new_anchors
            self._old = old_anchors

        def _install(self, anchors):
            from diagrammer.items.connection_item import Waypoint
            self._wire._anchors = [Waypoint(a.anchor, a.dx, a.dy) for a in anchors]
            self._wire._waypoints = [a.to_scene() for a in self._wire._anchors]
            self._wire.update_route()

        def redo(self):
            self._install(self._new)

        def undo(self):
            self._install(self._old)

    for wire, interior, pre_anchors, orig_mode in snapshots:
        mapped = [transform(p) for p in interior]
        new_anchors = [wire._make_anchor(p) for p in mapped]
        undo_stack.push(_SetAnchorsCommand(wire, new_anchors, pre_anchors))
        # Ports have already moved, so these are the transformed ends.
        route = mapped if wire.closed else [
            wire.source_port.scene_center(), *mapped,
            wire.target_port.scene_center()]
        if orig_mode != ROUTE_DIRECT and not _router_redraws(wire, route):
            undo_stack.push(SetRoutingModeCommand(wire, ROUTE_DIRECT))


class TransformMixin:
    """Mixin providing rotate, flip, and align operations.

    Expects the host class to have ``_scene`` and ``_gather_connected_junctions()``
    (provided by :class:`ClipboardMixin`).
    """

    # ------------------------------------------------------- grid pivot

    def _grid_pivot(self, items, center: QPointF, *, degrees=None,
                    horizontal=None) -> QPointF:
        """Pivot for a quarter/half turn (*degrees*) or a flip
        (*horizontal*) of *items* that keeps on-grid ports on the grid:
        the allowed pivot nearest *center* (see _grid_pivot_candidates).
        Unchanged when snapping is off or the turn can't keep the grid.

        Ties are common — a symmetric part's centre often sits exactly
        between allowed pivots — and go to the previous pivot of the same
        kind when it is among the nearest. After a turn about p, p is
        still among the nearest pivots, so a run of ±90 turns (and their
        undos) keeps one pivot: +90 then -90, or four +90s, restore the
        original placement exactly.
        """
        spacing = _snap_spacing(self._scene, items)
        if spacing is None:
            return center
        cands = _grid_pivot_candidates(center, spacing, degrees, horizontal)
        if not cands:
            return center

        def d2(p: QPointF) -> float:
            return (p.x() - center.x()) ** 2 + (p.y() - center.y()) ** 2

        def key(p: QPointF) -> tuple:
            # A flip only fixes its mirror coordinate; a turn the point.
            if horizontal is None:
                return (round(p.x(), 6), round(p.y(), 6))
            return (round(p.x() if horizontal else p.y(), 6),)

        best = min(d2(p) for p in cands)
        nearest = [p for p in cands if d2(p) <= best + 1e-9 * spacing * spacing]
        if horizontal is not None:
            kind = "flip_h" if horizontal else "flip_v"
        else:
            kind = "half" if int(degrees / 90.0) % 2 == 0 else "quarter"
        memory = getattr(self, "_grid_pivot_memory", None)
        if memory is None:
            memory = self._grid_pivot_memory = {}
        last = memory.get(kind)
        pick = next((p for p in nearest if key(p) == last), nearest[0])
        memory[kind] = key(pick)
        return pick

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

        Each item rotates internally AND orbits around the group center.
        For internal wires (both endpoints in the rotated set) we
        snapshot the full expanded route — including auto-routed bends
        that aren't stored as user waypoints — rotate the snapshot
        around the group center, and replay it as new port-local
        waypoints. Without that snapshot, the orthogonal router would
        re-derive its bends from the rotated key-points and pick a
        different L-shape than the rotated original.

        With grid snapping on, the pivot is moved to the nearest point
        that maps grid points onto grid points (see ``_grid_pivot``), so
        ports that were on the grid stay on it.
        """
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

        if len(movable) == 1 and isinstance(movable[0], (AnnotationItem, ShapeItem)):
            self._scene.undo_stack.push(RotateItemCommand(movable[0], degrees))
        else:
            # Each item rotates internally AND orbits the pivot: the
            # group centre (a lone component's own centre), moved to the
            # nearest point that keeps on-grid ports on the grid. One
            # scene-center formula across every item type via
            # _scene_center.
            scene_centers = [_scene_center(item) for item in movable]
            centroid = QPointF(
                sum(p.x() for p in scene_centers) / len(scene_centers),
                sum(p.y() for p in scene_centers) / len(scene_centers),
            )
            pivot = self._grid_pivot(movable, centroid, degrees=degrees)
            orbit = _rotation_map(pivot, degrees)
            target_centers = [orbit(sc) for sc in scene_centers]

            # Capture internal-wire shapes BEFORE rotating components,
            # so the snapshot reflects the user-visible route as drawn.
            wire_snapshots = _capture_internal_wire_shapes(
                _internal_wires(self._scene, movable),
            )

            for i, item in enumerate(movable):
                if isinstance(item, ComponentItem):
                    self._scene.undo_stack.push(RotateComponentCommand(item, degrees))
                elif isinstance(item, (AnnotationItem, ShapeItem)):
                    self._scene.undo_stack.push(RotateItemCommand(item, degrees))
                cur_sc = _scene_center(item)
                offset = target_centers[i] - cur_sc
                if offset.manhattanLength() < 1e-9:
                    continue  # a lone component turning about its centre
                new_pos = item.pos() + offset
                if hasattr(item, '_skip_snap'):
                    item._skip_snap = True
                self._scene.undo_stack.push(MoveComponentCommand(item, item.pos(), new_pos))
                if hasattr(item, '_skip_snap'):
                    item._skip_snap = False

            # Rotate the captured routes through the same orbit the
            # items followed and bind them to the now-rotated ports.
            _apply_internal_wire_shapes(
                self._scene.undo_stack, wire_snapshots, orbit,
            )

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    # -------------------------------------------------------- fine rotate

    def _fine_rotate_selected(self, degrees: float) -> None:
        """Fine-rotate the selection by an arbitrary *degrees* around the
        rotation pivot port (or group center if no pivot is set).

        For non-90° rotations the orthogonal router can't represent the
        rotated wire shape, so internal wires (both endpoints in the
        rotated set) switch to ROUTE_DIRECT — wrapped in
        ``SetRoutingModeCommand`` so undo restores the previous mode —
        and their captured routes are rotated around the same pivot.
        """
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

        orbit = _rotation_map(pivot_scene, degrees)

        # Capture internal-wire shapes BEFORE moving anything so the
        # snapshot reflects the user-visible route (including
        # auto-routed bends).
        wire_snapshots = _capture_internal_wire_shapes(
            _internal_wires(self._scene, movable),
        )

        for item in movable:
            if isinstance(item, ComponentItem):
                self._scene.undo_stack.push(RotateComponentCommand(item, degrees))
            elif isinstance(item, (AnnotationItem, ShapeItem)):
                self._scene.undo_stack.push(RotateItemCommand(item, degrees))

            cur_sc = _scene_center(item)
            offset = orbit(cur_sc) - cur_sc
            new_pos = item.pos() + offset
            if hasattr(item, '_skip_snap'):
                item._skip_snap = True
            self._scene.undo_stack.push(MoveComponentCommand(item, item.pos(), new_pos))
            if hasattr(item, '_skip_snap'):
                item._skip_snap = False

        # Replay the captured wire shapes through the rotation around
        # the pivot, rebound to port-local offsets and pinned to
        # ROUTE_DIRECT so the orthogonal router doesn't re-derive
        # bends.
        _apply_internal_wire_shapes(
            self._scene.undo_stack, wire_snapshots, orbit,
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

        # A lone annotation or shape flips in place; a lone component goes
        # through the group path so its mirror axis can keep the grid.
        if len(all_movable) <= 1 and not selected_comps:
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
        mirror = self._grid_pivot(
            all_movable,
            QPointF(sum(p.x() for p in centers) / len(centers),
                    sum(p.y() for p in centers) / len(centers)),
            horizontal=horizontal,
        )
        group_cx, group_cy = mirror.x(), mirror.y()

        # Capture internal-wire shapes BEFORE any per-item flip so the
        # snapshot reflects the user-visible route. Auto-routed bends
        # and closed-polygon vertices don't survive a flip on their own
        # (auto-routing rederives a different shape from the mirrored
        # key-points; closed-polygon waypoints anchor to a junction
        # whose own transform doesn't carry the flip). Replaying the
        # mirrored snapshot through ``_apply_internal_wire_shapes``
        # rebinds each point to a port-local offset under the now-
        # flipped transforms and pins ROUTE_DIRECT.
        wire_snapshots = _capture_internal_wire_shapes(
            _internal_wires(self._scene, all_movable),
        )

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

        # Mirror the captured routes through the same axis the items
        # used, so the wire shape flips rigidly along with them.
        _apply_internal_wire_shapes(
            self._scene.undo_stack, wire_snapshots,
            _mirror_map(QPointF(group_cx, group_cy), horizontal),
        )

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
