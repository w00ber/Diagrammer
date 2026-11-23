"""Build a :class:`~diagrammer.wirecheck.model.Snapshot` from a live scene.

The only module in :mod:`diagrammer.wirecheck` that touches Qt items; the
checks themselves never see them.
"""

from __future__ import annotations

from diagrammer.wirecheck.model import End, Snapshot, Wire


def _end(port) -> End:
    from diagrammer.commands.connect_command import lead_style_at
    comp = port.component
    cdef = getattr(comp, "component_def", None)
    if cdef is not None:
        return End("part", comp.instance_id, port.port_name,
                   lead_style_at(port), f"{cdef.name}:{port.port_name}")
    return End("node", getattr(comp, "instance_id", str(id(comp))))


def snapshot(scene) -> Snapshot:
    """Every wire in *scene*, with what each end is attached to."""
    from diagrammer.items.connection_item import ConnectionItem
    wires: dict[str, Wire] = {}
    for item in scene.items():
        if not isinstance(item, ConnectionItem):
            continue
        wires[item.instance_id] = Wire(
            id=item.instance_id,
            a=_end(item.source_port),
            b=_end(item.target_port),
            width=float(item.line_width),
            color=item.line_color.name(),
            deliberate=bool(getattr(item, "style_deliberate", False)),
        )
    return Snapshot(wires)
