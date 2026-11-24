"""Build a :class:`~diagrammer.wirecheck.model.Snapshot` from a live scene.

The only module in :mod:`diagrammer.wirecheck` that touches Qt items; the
checks themselves never see them.
"""

from __future__ import annotations

from diagrammer.svglint.twins import TWIN_LIBRARIES
from diagrammer.wirecheck.model import End, Snapshot, Twin, Wire


def _end(port) -> End:
    from diagrammer.commands.connect_command import lead_style_at
    comp = port.component
    cdef = getattr(comp, "component_def", None)
    if cdef is not None:
        from diagrammer.commands.lead_override import can_override
        return End("part", comp.instance_id, port.port_name,
                   lead_style_at(port), f"{cdef.name}:{port.port_name}",
                   restylable=can_override(comp, port.port_name))
    return End("node", getattr(comp, "instance_id", str(id(comp))))


def snapshot(scene) -> Snapshot:
    """Every wire in *scene*, with what each end is attached to, and the
    twin of every part that has one (so swap fixes can be offered)."""
    from diagrammer.items.component_item import ComponentItem
    from diagrammer.items.connection_item import ConnectionItem
    wires: dict[str, Wire] = {}
    twins: dict[str, Twin] = {}
    library = getattr(scene, "library", None)
    for item in scene.items():
        if isinstance(item, ComponentItem) and library is not None:
            twin = part_twin(library, item.library_key)
            if twin is not None:
                twins[item.instance_id] = twin
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
    return Snapshot(wires, twins)


def part_twin(library, key: str) -> Twin | None:
    """:func:`twin_key` with the twin's lead at each port."""
    from diagrammer.commands.connect_command import lead_style_of
    tkey = twin_key(library, key)
    if tkey is None:
        return None
    tdef = library.get(tkey)
    return Twin(tkey, tkey.split("/", 1)[0],
                {p.name: lead_style_of(tdef, p) for p in tdef.ports})


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
    """Apply a wirecheck :class:`Fix` to the scene as one undo step.

    Parts first (swaps), then wires, so a wire's new width lands on the
    wire that is by then attached to the swapped-in part.
    """
    from PySide6.QtGui import QColor

    from diagrammer.commands.style_command import ChangeStyleCommand
    from diagrammer.items.connection_item import ConnectionItem
    stack = scene.undo_stack
    stack.beginMacro(text or fix.label)
    try:
        if fix.swaps:
            _apply_swaps(scene, fix.swaps)
        overrides = {k: v for k, v in fix.leads.items()
                     if k[0] not in fix.swaps}
        if overrides:
            _apply_lead_overrides(scene, overrides)
        conns = {i.instance_id: i for i in scene.items()
                 if isinstance(i, ConnectionItem)}
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


def _apply_swaps(scene, swaps: dict[str, str]) -> list:
    """Push a :class:`SwapComponentCommand` per ``{instance id: key}``.
    Returns the swapped-in items."""
    from diagrammer.commands.swap_command import SwapComponentCommand
    from diagrammer.items.component_item import ComponentItem
    library = getattr(scene, "library", None)
    by_id = {i.instance_id: i for i in scene.items()
             if isinstance(i, ComponentItem)}
    out = []
    for ref, key in swaps.items():
        item = by_id.get(ref)
        new_def = library.get(key) if library is not None else None
        if item is None or new_def is None:
            continue
        cmd = SwapComponentCommand(scene, item, new_def)
        if cmd.ok:
            scene.undo_stack.push(cmd)
            out.append(cmd.new_item)
    return out


def _apply_lead_overrides(scene, leads: dict) -> None:
    """Per-instance lead overrides, ``{(part id, port): {attr: value}}``."""
    from diagrammer.commands.lead_override import set_lead_override
    from diagrammer.items.component_item import ComponentItem
    by_id = {i.instance_id: i for i in scene.items()
             if isinstance(i, ComponentItem)}
    for (ref, port), attrs in leads.items():
        item = by_id.get(ref)
        if item is not None and attrs:
            set_lead_override(scene.undo_stack, item, port, **attrs)


def swap_fix_for(scene, item):
    """The :class:`Fix` that swaps *item* for its twin, restyling the
    wires that follow its leads; None when it has no twin.

    What the part's context menu does. Unlike the swap fixes the checks
    offer, it is not refused for disturbing another net: the user asked
    for this part.
    """
    from diagrammer.wirecheck.checks import _follow_leads
    from diagrammer.wirecheck.model import Fix
    library = getattr(scene, "library", None)
    if library is None:
        return None
    twin = part_twin(library, item.library_key)
    if twin is None:
        return None
    snap = snapshot(scene)
    leads: dict = {}
    touched: set[str] = set()
    for w in snap.wires.values():
        for e in w.ends():
            if e.kind == "part" and e.ref == item.instance_id:
                tl = twin.leads.get(e.port)
                if tl is not None:
                    leads[(e.ref, e.port)] = {
                        k: v for k, v in (("width", tl.width),
                                          ("color", tl.color)) if v is not None}
                touched.add(w.id)
    changes = _follow_leads(snap.apply(Fix("", leads=leads)), touched)
    return Fix(f"Swap {item.component_def.name} to {twin.library}", changes,
               swaps={item.instance_id: twin.key}, leads=leads, kind="swap")


def swap_to_twin(scene, item) -> bool:
    """Swap *item* for its twin as one undo step; False when it has none."""
    fix = swap_fix_for(scene, item)
    if fix is None:
        return False
    apply_fix(scene, fix)
    return True


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
WEIGHT_LIBRARIES = TWIN_LIBRARIES


def twin_key(library, key: str) -> str | None:
    """The same part in the other weight library, if a safe swap exists.

    Matched by :func:`diagrammer.svglint.twins.find_twin` — the same
    relative path, else a unique file name (the libraries' folders have
    drifted) — and only when the port names agree, or the swap would orphan
    wires. The cross-library twin check (``python -m diagrammer.svglint
    --twins``) keeps the shipped pairs that way. Convert to Library swaps a
    part for this key (:func:`swap_to_twin`).
    """
    from diagrammer.svglint.twins import find_twin
    top, _, rel = key.partition("/")
    if top not in WEIGHT_LIBRARIES or not rel:
        return None
    src = library.get(key)
    if src is None:
        return None
    names = {p.name for p in src.ports}
    for other in WEIGHT_LIBRARIES:
        if other == top:
            continue
        rels = {k.partition("/")[2]: k for k in library._by_key
                if k.partition("/")[0].casefold() == other.casefold()}
        match = find_twin(rel, rels)
        cand = rels[match] if match is not None else None
        if cand and {p.name for p in library.get(cand).ports} == names:
            return cand
    return None


def diagram_library(scene) -> str | None:
    """The weight library most of the diagram's parts come from, when one
    holds a strict majority over the others; None for a tie or no parts."""
    from collections import Counter

    from diagrammer.items.component_item import ComponentItem
    usage = Counter(i.library_key.split("/", 1)[0] for i in scene.items()
                    if isinstance(i, ComponentItem)
                    and i.library_key.split("/", 1)[0] in WEIGHT_LIBRARIES)
    if not usage:
        return None
    ranked = usage.most_common(2)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


#: The order fixes are offered in: the part's own fault first.
FIX_KIND_ORDER = {"swap": 0, "lead": 1, "wire": 2}


def _matches_diagram(fix, weight: float) -> bool:
    t = fix.target
    return isinstance(t, (int, float)) and abs(float(t) - weight) <= 0.01


def recommended_fix(scene, finding, fixes=None):
    """The fix to offer first, or None.

    The first, in :data:`FIX_KIND_ORDER`, whose result is the width most of
    the diagram's library uses. With no such fix, a ``wire-<attr>`` finding
    recommends its wire fix — take the lead's value — since the wire is
    the odd one out; a finding about disagreeing leads recommends nothing.
    """
    fixes = list(finding.fixes if fixes is None else fixes)
    by_kind = sorted(fixes, key=lambda fx: FIX_KIND_ORDER.get(fx.kind, 9))
    lib = diagram_library(scene)
    if lib is not None:
        weight = WEIGHT_LIBRARIES[lib]
        best = next((fx for fx in by_kind if _matches_diagram(fx, weight)),
                    None)
        if best is not None:
            return best
    if finding.check_id in ("wire-width", "wire-color"):
        return next((fx for fx in fixes if fx.kind == "wire"), None)
    return None


def ordered_fixes(scene, finding, fixes=None) -> list:
    """*finding*'s fixes (or the subset *fixes*) in the order the notice
    and the Check Wiring panel offer them: the recommended one first
    (:func:`recommended_fix`), then swap to twin, match this part's lead,
    set the wire; within a kind, the one matching the diagram first."""
    fixes = list(finding.fixes if fixes is None else fixes)
    best = recommended_fix(scene, finding, fixes)
    lib = diagram_library(scene)
    weight = WEIGHT_LIBRARIES[lib] if lib is not None else None

    def rank(ix):
        i, fx = ix
        near = weight is not None and _matches_diagram(fx, weight)
        return (fx is not best, FIX_KIND_ORDER.get(fx.kind, 9), not near, i)

    return [fx for _i, fx in sorted(enumerate(fixes), key=rank)]


def swap_hint(scene, finding) -> str:
    """Say which way the diagram leans, and which part would swap to match.

    A recommendation the user can't see the reason for reads as arbitrary,
    so the sentence leads with the reason (what most of the diagram uses).
    With no clear majority it says so and names every part that has a
    version in the other library, rather than guessing.
    """
    from diagrammer.items.component_item import ComponentItem
    library = getattr(scene, "library", None)
    if library is None:
        return ""
    by_id = {i.instance_id: i for i in scene.items()
             if isinstance(i, ComponentItem)}
    snap = snapshot(scene)
    # A wire can be gone by the time the notice asks (deleted, merged).
    refs = sorted({e.ref for wid in finding.wire_ids if wid in snap.wires
                   for e in snap.wires[wid].ends() if e.kind == "part"})
    twins = []                       # (item, its library, twin's library)
    for ref in refs:
        item = by_id.get(ref)
        if item is None:
            continue
        twin = twin_key(library, item.library_key)
        if twin:
            twins.append((item, item.library_key.split("/", 1)[0],
                          twin.split("/", 1)[0]))
    lead = diagram_library(scene)
    if lead is not None:
        reason = (f"Most of this diagram is {lead} "
                  f"({WEIGHT_LIBRARIES[lead]:g} pt).")
        movers = [it.component_def.name for it, src, dst in twins
                  if src != lead and dst == lead]
        if movers:
            verb = "comes" if len(movers) == 1 else "come"
            return (f"{reason} {', '.join(movers)} {verb} in {lead} too; "
                    "swapping would match.")
        return reason
    if not twins:
        return ""
    return ("No library dominates this diagram. "
            + "; ".join(f"{it.component_def.name} also comes in {dst}"
                        for it, _src, dst in twins) + ".")
