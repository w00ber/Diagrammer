"""One drag pipeline: snapshot → move → commit.

Every way of moving things on the canvas — a plain drag, a multi-select
drag, a grouped-wire drag, an Alt-drag of fresh clones, a unified drag
of components plus selected waypoints, an arrow-key nudge, a wire's own
waypoint or segment drag — is the same operation with different inputs:
a set of movers (from the closure), an optional set of waypoints driven
directly, an optional reshaper that drives one wire's whole route, and a
delta. ``DragSession`` is that operation. The caller only decides
*which* items and *what* delta; see
``docs/wiring-interaction-redesign.md``, invariant I5.

The session takes one snapshot at construction (every mover's position
and every wire's waypoints), applies each delta idempotently against
that snapshot, and on commit diffs the scene against it to build a
single undo macro. Undo entries are therefore derived from what
actually changed, never assembled by hand per call site.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF

from diagrammer.canvas.closure import Closure


def delta_landing_on_grid(
    anchor: QPointF, raw: QPointF, spacing: float, *, enabled: bool = True,
) -> QPointF:
    """The delta to apply so that *anchor* + delta lands on the grid.

    The one snapping rule for a drag: the gesture moves by whatever puts
    the thing the user grabbed — a component's nearest port, the
    waypoint handle under the cursor — on a grid line. Anchoring the
    snap on the grabbed point rather than on the mouse is what lets a
    drag pull an off-grid item *onto* the grid, instead of sliding it in
    rigid grid-sized steps forever.
    """
    from diagrammer.canvas.grid import snap_to_grid

    if not enabled:
        return QPointF(raw)
    target = QPointF(anchor.x() + raw.x(), anchor.y() + raw.y())
    snapped = snap_to_grid(target, spacing)
    return QPointF(snapped.x() - anchor.x(), snapped.y() - anchor.y())


class SegmentReshape:
    """One wire's route, driven by a dragged segment (the KiCad mechanic).

    A segment drag is not a translation: a horizontal run moves only in
    y, its neighbours stretch to stay connected, and a free end follows
    the route rather than the delta. So this reshaper positions its own
    wire and its own junction endpoints, and the session only snapshots
    them — so the commit diff still records everything that moved.
    """

    def __init__(self, conn, seg_idx: int) -> None:
        self.conn = conn
        self.seg_idx = seg_idx
        self.base_expanded = [QPointF(p) for p in conn.all_points()]
        self.wires = [conn]
        self.items = conn.endpoint_junctions()

    def apply(self, delta: QPointF) -> None:
        self.conn.reshape_from_segment(self.seg_idx, delta, self.base_expanded)


class DragSession:
    def __init__(
        self,
        scene,
        closure: Closure,
        *,
        anchor=None,
        waypoint_selection: dict | None = None,
        wires_by_id: dict | None = None,
        reshape=None,
    ) -> None:
        """
        *anchor* is the item whose snapping decides the delta (the view
        consults it; the session only records it). *waypoint_selection*
        maps a connection's ``instance_id`` to the set of waypoint
        indices the user selected; those wires are driven by that
        selection instead of being translated as internal wires.
        *wires_by_id* resolves those ids to items. *reshape* is an
        object with ``items``, ``wires`` and ``apply(delta)`` that
        positions part of the scene itself — see ``SegmentReshape``.
        """
        from diagrammer.items.connection_item import ConnectionItem

        self.scene = scene
        self.closure = closure
        self.anchor = anchor
        self.reshape = reshape
        self.delta = QPointF(0, 0)

        # Movers the delta translates, versus movers the reshaper places
        # itself. Both are snapshotted, so ``commit`` records either.
        self._delta_movers = list(closure.movers)
        self.movers = list(self._delta_movers)
        known = {id(m) for m in self.movers}
        for it in (reshape.items if reshape else ()):
            if id(it) not in known:
                self.movers.append(it)
                known.add(id(it))

        self.item_starts: dict[int, QPointF] = {
            id(m): QPointF(m.pos()) for m in self.movers
        }
        self._wires: list = [
            i for i in scene.items() if isinstance(i, ConnectionItem)
        ]
        self.wire_starts: dict[int, list[QPointF]] = {
            id(w): [QPointF(p) for p in w.vertices] for w in self._wires
        }

        driven: dict[int, tuple] = {}
        for conn_id, idxs in (waypoint_selection or {}).items():
            conn = (wires_by_id or {}).get(conn_id)
            if conn is not None and idxs:
                driven[id(conn)] = (conn, set(idxs))
        self._driven = driven
        reshaped = {id(w) for w in (reshape.wires if reshape else ())}
        self._rigid = [
            w for w in closure.internal_wires
            if id(w) not in driven and id(w) not in reshaped
        ]
        # Wires the reshaper moves, plus the ones hanging off the
        # junctions it drags: those re-route even though nothing in the
        # closure moved.
        reshape_items = {id(i) for i in (reshape.items if reshape else ())}
        self._reshaped: list = list(reshape.wires) if reshape else []
        if reshape_items:
            for w in self._wires:
                if id(w) in reshaped:
                    continue
                for port in (w.source_port, w.target_port):
                    comp = port.component if port else None
                    if comp is not None and id(comp) in reshape_items:
                        self._reshaped.append(w)
                        break
        self._tidied = False

    # ------------------------------------------------------------ moving

    def move_to(self, delta: QPointF) -> None:
        """Place everything at its start position plus *delta*."""
        self.delta = QPointF(delta)
        if not self._tidied and (delta.x() or delta.y()):
            # A waypoint sitting on a port that is about to move would
            # stay behind as a stub. It is redundant by construction;
            # prune it now that this is a real move, after the snapshot
            # so the undo entry puts it back.
            for w in self.closure.boundary_wires:
                if id(w) not in self._driven and w.simplify_waypoints():
                    w.update_route()
            self._tidied = True

        for m in self._delta_movers:
            start = self.item_starts[id(m)]
            m.setPos(QPointF(start.x() + delta.x(), start.y() + delta.y()))

        for w in self._rigid:
            start = self.wire_starts[id(w)]
            w._set_waypoints_from_scene(
                [QPointF(p.x() + delta.x(), p.y() + delta.y()) for p in start]
            )
        for conn, idxs in self._driven.values():
            start = self.wire_starts[id(conn)]
            for idx in idxs:
                if 0 <= idx < len(conn._waypoints) and idx < len(start):
                    conn._set_waypoint_at(idx, QPointF(
                        start[idx].x() + delta.x(), start[idx].y() + delta.y(),
                    ))
        if self.reshape is not None:
            self.reshape.apply(delta)
        # Re-route only what this drag touches; the full two-pass rebuild
        # (which also refreshes crossover hops on untouched wires) runs
        # once at commit. On a large diagram a per-move rebuild of every
        # wire is what made dragging sluggish.
        touched = list(self._rigid) + list(self.closure.boundary_wires) + [
            conn for conn, _ in self._driven.values()
        ] + self._reshaped
        for w in touched:
            w.rebuild_expanded()
        for w in touched:
            w.rebuild_path()
        for m in self.movers:
            if hasattr(m, "refresh_lead_shortening"):
                m.refresh_lead_shortening()

    # ----------------------------------------------------------- committing

    @property
    def changed(self) -> bool:
        for m in self.movers:
            if self.item_starts[id(m)] != m.pos():
                return True
        for w in self._wires:
            if self.wire_starts[id(w)] != list(w.vertices):
                return True
        return False

    def commit(self, label: str = "Move") -> bool:
        """Record the move as one undo macro. Returns False for a no-op.

        A click that moved nothing must not touch the undo stack: Qt
        keeps empty macros, and ``beginMacro`` wipes the redo history.
        """
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
        from diagrammer.items.component_item import ComponentItem

        if not self.changed:
            self.finish()
            return False

        undo = self.scene.undo_stack
        undo.beginMacro(label)
        try:
            moved_components = []
            for m in self.movers:
                start = self.item_starts[id(m)]
                if start != m.pos():
                    undo.push(MoveComponentCommand(m, start, QPointF(m.pos())))
                    if isinstance(m, ComponentItem):
                        moved_components.append(m)
            # Ports dropped onto other ports join — inside the macro so
            # the new wire undoes with the move.
            mover_ids = frozenset(id(m) for m in self.movers)
            for comp in moved_components:
                self.scene.auto_join_overlapping_ports(
                    comp, exclude_ids=mover_ids, moved_by=self.delta)
            # Heal wires the move straightened out: a waypoint that no
            # longer bends the route is invisible but still splits a
            # draggable run and pins a point the grid snap can't move.
            for w in (self.closure.internal_wires + self.closure.boundary_wires
                      + [c for c, _ in self._driven.values()] + self._reshaped):
                if w.simplify_waypoints():
                    w.update_route()
            for w in self._wires:
                before = self.wire_starts[id(w)]
                after = [QPointF(p) for p in w.vertices]
                if before != after:
                    undo.push(EditWaypointsCommand(w, before, after))
        finally:
            undo.endMacro()
            self.finish()
        self.scene.update_connections()
        return True

    def finish(self) -> None:
        """Release the anchor's snap choice. Idempotent."""
        for m in self.movers:
            if hasattr(m, "clear_snap_anchor"):
                m.clear_snap_anchor()
