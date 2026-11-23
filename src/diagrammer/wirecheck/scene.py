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


# ---------------------------------------------------------------------------
# Live mode helpers (Regularize Wiring, issue #47 stage 2)
# ---------------------------------------------------------------------------

def regularized_style(scene, source, target) -> tuple[float | None, str | None]:
    """``(width, colour)`` a new wire between *source* and *target* should take.

    A part end's lead first (source, then target). A junction end has no lead,
    so it lends the style of a wire already on it — the net's. ``None`` for
    whatever neither end can say, so the caller keeps its default.
    """
    from diagrammer.commands.connect_command import lead_style_at
    width = color = None
    for port in (source, target):
        style = lead_style_at(port)
        if style is not None:
            width = width if width is not None else style.width
            color = color if color is not None else style.color
    for port in (source, target):
        if width is not None and color is not None:
            break
        if getattr(port.component, "component_def", None) is not None:
            continue
        for conn in scene.connections_on_port(port):
            width = width if width is not None else float(conn.line_width)
            color = color if color is not None else conn.line_color.name()
    return width, color


def lead_values(scene, conn) -> dict[str, list]:
    """The distinct lead widths and colours on *conn*'s net, most common
    first: ``{"width": [...], "color": [...]}``. One value means the leads
    agree; several, that they disagree; none, that there are no leads."""
    from diagrammer.wirecheck.checks import _ATTRS, _distinct, nets
    snap = snapshot(scene)
    net = next((n for n in nets(snap) if any(w.id == conn.instance_id
                                              for w in n)), None)
    out: dict[str, list] = {"width": [], "color": []}
    if net is None:
        return out
    for attr in out:
        spec = _ATTRS[attr]
        out[attr] = _distinct(
            [spec["lead"](e.lead) for w in net for e in w.ends()
             if e.kind == "part" and e.lead is not None
             and spec["lead"](e.lead) is not None],
            spec["same"])
    return out


def expected_style(scene, conn) -> tuple[float | None, str | None]:
    """The width and colour *conn*'s leads agree on, or ``None`` per attribute
    when they disagree or there are none. What a hand-set style is measured
    against."""
    vals = lead_values(scene, conn)
    return tuple(v[0] if len(v) == 1 else None
                 for v in (vals["width"], vals["color"]))  # type: ignore[return-value]


def apply_fix(scene, fix, text: str | None = None) -> None:
    """Apply a wirecheck :class:`Fix` to the scene as one undo step."""
    from PySide6.QtGui import QColor

    from diagrammer.commands.style_command import ChangeStyleCommand
    from diagrammer.items.connection_item import ConnectionItem
    conns = {i.instance_id: i for i in scene.items()
             if isinstance(i, ConnectionItem)}
    stack = scene.undo_stack
    stack.beginMacro(text or fix.label)
    try:
        for wid, attrs in fix.changes.items():
            conn = conns.get(wid)
            if conn is None:
                continue
            for attr, value in attrs.items():
                if attr == "width":
                    stack.push(ChangeStyleCommand(conn, "line_width",
                                                  conn.line_width, float(value)))
                elif attr == "color":
                    stack.push(ChangeStyleCommand(conn, "line_color",
                                                  QColor(conn.line_color),
                                                  QColor(value)))
                elif attr == "deliberate":
                    stack.push(ChangeStyleCommand(conn, "style_deliberate",
                                                  conn.style_deliberate,
                                                  bool(value)))
    finally:
        stack.endMacro()


def disagreements_at(scene, ports) -> list:
    """``leads-disagree`` findings (width or colour) on the nets touching
    *ports*, each fix already verified. What the on-canvas notice shows
    after a wire is drawn."""
    from diagrammer.wirecheck.checks import check, verified
    wanted = {c.instance_id for p in ports for c in scene.connections_on_port(p)}
    if not wanted:
        return []
    snap = snapshot(scene)
    out = []
    for f in check(snap):
        if f.check_id.startswith("leads-disagree") and wanted & set(f.wire_ids):
            good = tuple(fx for fx in f.fixes if verified(snap, f, fx))
            out.append(type(f)(f.check_id, f.message, f.wire_ids, good, f.data))
    return out


#: Libraries a part can be swapped between, and their lead weight.
WEIGHT_LIBRARIES = {"PAPER_2pt": 2.0, "SLIDES_3pt": 3.0}


def twin_key(library, key: str) -> str | None:
    """The same part in the other weight library, if a safe swap exists.

    Same relative path, else a unique file name in the other library (the
    libraries' folders have drifted); port names must match, or the swap
    would orphan wires. Convert to Library (#47 stage 4) will act on this;
    for now the notice only names it.
    """
    top, _, rel = key.partition("/")
    if top not in WEIGHT_LIBRARIES or not rel:
        return None
    src = library.get(key)
    if src is None:
        return None
    names = {p.name for p in src.ports}
    stem = rel.rsplit("/", 1)[-1]
    for other in WEIGHT_LIBRARIES:
        if other == top:
            continue
        cand = f"{other}/{rel}"
        if library.get(cand) is None:
            same = [k for k in library._by_key
                    if k.startswith(other + "/") and k.rsplit("/", 1)[-1] == stem]
            cand = same[0] if len(same) == 1 else None
        if cand and {p.name for p in library.get(cand).ports} == names:
            return cand
    return None


def swap_hint(scene, finding) -> str:
    """One sentence naming the part whose swap would settle *finding*: a part
    from the library the rest of the diagram uses least."""
    from collections import Counter

    from diagrammer.items.component_item import ComponentItem
    library = getattr(scene, "library", None)
    if library is None:
        return ""
    parts = [i for i in scene.items() if isinstance(i, ComponentItem)]
    usage = Counter(i.library_key.split("/", 1)[0] for i in parts
                    if i.library_key.split("/", 1)[0] in WEIGHT_LIBRARIES)
    by_id = {i.instance_id: i for i in parts}
    snap = snapshot(scene)
    # A wire can be gone by the time the notice asks (deleted, merged).
    ends = {(e.ref, e.port) for wid in finding.wire_ids if wid in snap.wires
            for e in snap.wires[wid].ends() if e.kind == "part"}
    candidates = []
    for ref, _port in sorted(ends):
        item = by_id.get(ref)
        if item is None:
            continue
        lib = item.library_key.split("/", 1)[0]
        twin = twin_key(library, item.library_key)
        if twin:
            candidates.append((usage.get(lib, 0), item, twin))
    if not candidates:
        return ""
    _, item, twin = min(candidates, key=lambda c: c[0])
    return (f"{item.component_def.name} also comes in "
            f"{twin.split('/', 1)[0]}; swapping it would match.")
