"""The one connectivity rule: what moves with a selection.

Every operation that acts on a set of items and the wiring between them
— drag, nudge, copy, cut, duplicate, transform — answers the same
question first: *given what the user selected, which junctions and
which wires come along?* This module is the single answer. See
``docs/wiring-interaction-redesign.md``, invariant I4.

The rule, as a user would state it:

    The connected sub-circuit you selected moves, up to the first
    component you did not select.

Junctions are not selectable today (``JunctionItem``), so they are
inferred from the wire graph in two sweeps:

1. *Reach* — every junction wired to a selected item, then every
   junction wired to one of those, and so on along junction-to-junction
   wires (a rail, a bend chain).
2. *Prune* — drop any reached junction with a wire to an unselected
   component, then any junction whose neighbour was just dropped, until
   nothing changes. A rail that runs on to something the user left out
   stays put; one that ends on the selection comes along, free ends
   included.

Wires are then classified: *internal* when both ends are on movers,
*boundary* when exactly one is. A drag translates internal wires
rigidly and re-routes boundary wires; a copy takes internal wires only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Closure:
    """Result of :func:`movable_closure`."""

    items: list = field(default_factory=list)       # what the user selected
    junctions: list = field(default_factory=list)   # inferred to travel too
    internal_wires: list = field(default_factory=list)
    boundary_wires: list = field(default_factory=list)

    @property
    def movers(self) -> list:
        """Everything whose position changes: selected items + junctions."""
        return self.items + self.junctions

    def mover_ids(self) -> set[int]:
        return {id(m) for m in self.movers}


def movable_closure(scene_items, selection, *, include_touching: bool = False) -> Closure:
    """Compute the junctions and wires that belong to *selection*.

    *scene_items* is any iterable of scene items (``scene.items()``);
    *selection* the movable items the user selected — components,
    annotations, shapes, images, and any explicitly selected junctions.
    Explicitly selected wires may also be passed: they are not movers
    themselves, but their endpoint junctions seed the reach.

    ``include_touching``: also take every junction wired directly to a
    selected item even when the prune would drop it. For a copy or a
    duplicate nothing stays behind to pin that junction, and the user
    expects the wire joining two copied components to be copied with
    them; for a drag the same junction is held by the rail it sits on
    and must not move.
    """
    from diagrammer.items.connection_item import ConnectionItem
    from diagrammer.items.junction_item import JunctionItem

    items = [i for i in selection if not isinstance(i, ConnectionItem)]
    selected_ids = {id(i) for i in items}
    seed_junctions: dict[int, JunctionItem] = {}
    for wire in selection:
        if isinstance(wire, ConnectionItem):
            for port in (wire.source_port, wire.target_port):
                comp = port.component if port else None
                if isinstance(comp, JunctionItem) and id(comp) not in selected_ids:
                    seed_junctions[id(comp)] = comp

    # junction id -> (junction, [neighbour items])
    neighbours: dict[int, tuple] = {}
    wires: list[ConnectionItem] = []
    for it in scene_items:
        if not isinstance(it, ConnectionItem):
            continue
        wires.append(it)
        a = it.source_port.component if it.source_port else None
        b = it.target_port.component if it.target_port else None
        for jct, other in ((a, b), (b, a)):
            if not isinstance(jct, JunctionItem) or id(jct) in selected_ids:
                continue
            neighbours.setdefault(id(jct), (jct, []))[1].append(other)

    # 1. Reach outward from the selection along junction chains.
    reached: set[int] = {
        jid for jid, (_, others) in neighbours.items()
        if any(id(o) in selected_ids for o in others)
    }
    reached |= {jid for jid in seed_junctions if jid in neighbours}
    frontier = set(reached)
    while frontier:
        nxt = set()
        for jid in frontier:
            for o in neighbours[jid][1]:
                oid = id(o)
                if oid in neighbours and oid not in reached:
                    reached.add(oid)
                    nxt.add(oid)
        frontier = nxt

    # 2. Prune anything whose neighbourhood is not entirely moving.
    touching = {
        jid for jid, (_, others) in neighbours.items()
        if any(id(o) in selected_ids for o in others)
    } | set(seed_junctions)
    changed = True
    while changed:
        changed = False
        for jid in list(reached):
            if include_touching and jid in touching:
                continue
            others = neighbours[jid][1]
            if not all(id(o) in selected_ids or id(o) in reached for o in others):
                reached.discard(jid)
                changed = True

    junctions = [neighbours[jid][0] for jid in reached]
    # Seeds from selected wires that have no other wiring (a lone free
    # wire) never entered ``neighbours``; they still travel.
    for jid, jct in seed_junctions.items():
        if jid not in neighbours:
            junctions.append(jct)

    mover_ids = selected_ids | {id(j) for j in junctions}
    internal, boundary = [], []
    for wire in wires:
        a = wire.source_port.component if wire.source_port else None
        b = wire.target_port.component if wire.target_port else None
        n = (id(a) in mover_ids) + (id(b) in mover_ids)
        if n == 2:
            internal.append(wire)
        elif n == 1:
            boundary.append(wire)
    return Closure(items, junctions, internal, boundary)
