"""One drag pipeline: snapshot → move → commit.

Every way of moving things on the canvas — a plain drag, a multi-select
drag, a grouped-wire drag, an Alt-drag of fresh clones, a unified drag
of components plus selected waypoints, an arrow-key nudge — is the same
operation with different inputs: a set of movers (from the closure), an
optional set of waypoints driven directly, and a delta. ``DragSession``
is that operation. The view only decides *which* items and *what*
delta; see ``docs/wiring-interaction-redesign.md``, invariant I5.

The session takes one snapshot at construction (every mover's position
and every wire's waypoints), applies each delta idempotently against
that snapshot, and on commit diffs the scene against it to build a
single undo macro. Undo entries are therefore derived from what
actually changed, never assembled by hand per call site.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF

from diagrammer.canvas.closure import Closure


class DragSession:
    def __init__(
        self,
        scene,
        closure: Closure,
        *,
        anchor=None,
        waypoint_selection: dict | None = None,
        wires_by_id: dict | None = None,
    ) -> None:
        """
        *anchor* is the item whose snapping decides the delta (the view
        consults it; the session only records it). *waypoint_selection*
        maps a connection's ``instance_id`` to the set of waypoint
        indices the user selected; those wires are driven by that
        selection instead of being translated as internal wires.
        *wires_by_id* resolves those ids to items.
        """
        from diagrammer.items.connection_item import ConnectionItem

        self.scene = scene
        self.closure = closure
        self.movers = list(closure.movers)
        self.anchor = anchor
        self.delta = QPointF(0, 0)

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
        self._rigid = [
            w for w in closure.internal_wires if id(w) not in driven
        ]
        self._tidied = False

        for m in self.movers:
            if hasattr(m, "_skip_snap"):
                m._skip_snap = True

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

        for m in self.movers:
            start = self.item_starts[id(m)]
            m._skip_snap = True
            m.setPos(QPointF(start.x() + delta.x(), start.y() + delta.y()))
            m._skip_snap = False

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
        # Boundary wires and everything else re-route from the new
        # endpoint positions; crossovers need every route current
        # before any path is rebuilt, which update_connections does.
        self.scene.update_connections()

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
            for comp in moved_components:
                self.scene.auto_join_overlapping_ports(comp)
            # Heal wires the move straightened out: a waypoint that no
            # longer bends the route is invisible but still splits a
            # draggable run and pins a point the grid snap can't move.
            for w in self.closure.internal_wires + self.closure.boundary_wires:
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
        """Release per-item drag flags. Idempotent."""
        for m in self.movers:
            if hasattr(m, "_skip_snap"):
                m._skip_snap = False
            if hasattr(m, "clear_snap_anchor"):
                m.clear_snap_anchor()
