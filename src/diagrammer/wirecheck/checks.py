"""The wiring checks. Pure functions of a :class:`Snapshot`.

Width and colour are checked the same way, per *net* (wires joined through
junctions; a wire between two parts is a net of its own):

* the part leads at the net's ends agree -> each wire that differs gets a
  ``wire-<attr>`` finding, fixed by taking the lead's value;
* they disagree (a 2 px lead wired to a 3 px one) -> one ``leads-disagree``
  (width) or ``leads-disagree-color`` finding for the net. No value is right
  at both ends, so each fix is a *decision*: use this value for the net and
  mark its wires deliberate, which is what clears the finding;
* no part leads at all (junction-to-junction runs) but the wires differ ->
  ``net-mixed-<attr>``, one fix per value present.

Deliberate wires are neither flagged nor changed.

Where the fault is in a part rather than a wire, two more kinds of fix are
offered ahead of the wire fixes, in this order (issue #47 stage 4):

* **swap** a part for its twin in the other weight library, when the
  snapshot knows one (``Snapshot.twins``) and its lead gives the value;
* **lead**: override that one part's lead at that port (stage 4, step 4).

Each is checked like any other fix: the candidate snapshot is re-checked and
the fix is offered only if it clears the finding and raises nothing new. A
swap changes the part's leads at every port, so its wires on other nets are
restyled with it, and a swap that would make another net disagree is refused.
"""

from __future__ import annotations

from collections import Counter

from diagrammer.wirecheck.model import (
    WIDTH_TOL,
    Finding,
    Fix,
    Snapshot,
    Wire,
    colors_match,
)


def nets(snap: Snapshot) -> list[list[Wire]]:
    """Wires grouped by connectivity through junction ("node") ends."""
    parent: dict[str, str] = {w: w for w in snap.wires}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_node: dict[str, list[str]] = {}
    for w in snap.wires.values():
        for e in w.ends():
            if e.kind == "node":
                by_node.setdefault(e.ref, []).append(w.id)
    for ids in by_node.values():
        for other in ids[1:]:
            parent[find(other)] = find(ids[0])
    groups: dict[str, list[Wire]] = {}
    for w in sorted(snap.wires.values(), key=lambda w: w.id):
        groups.setdefault(find(w.id), []).append(w)
    return list(groups.values())


def _fmt_w(w: float) -> str:
    return f"{w:g} pt"


# Per attribute: how to read it off a lead and a wire, compare, and print.
_ATTRS = {
    "width": dict(
        lead=lambda lead: lead.width,
        wire=lambda w: w.width,
        same=lambda a, b: abs(a - b) <= WIDTH_TOL,
        fmt=_fmt_w,
        word="width",
    ),
    "color": dict(
        lead=lambda lead: lead.color,
        wire=lambda w: w.color,
        same=colors_match,
        fmt=lambda c: c,
        word="colour",
    ),
}


def _distinct(values: list, same) -> list:
    """Values clustered by *same*, each cluster's most common member, most
    common cluster first."""
    clusters: list[list] = []
    for v in values:
        for c in clusters:
            if same(c[0], v):
                c.append(v)
                break
        else:
            clusters.append([v])
    clusters.sort(key=len, reverse=True)
    return [Counter(c).most_common(1)[0][0] for c in clusters]


def _follow_leads(snap: Snapshot, wire_ids: set[str]) -> dict[str, dict]:
    """Wire changes that make every live wire in the nets holding
    *wire_ids* take the value its net's leads agree on."""
    changes: dict[str, dict] = {}
    for net in nets(snap):
        if not any(w.id in wire_ids for w in net):
            continue
        for attr, spec in _ATTRS.items():
            vals = _distinct([spec["lead"](e.lead) for w in net
                              for e in w.ends()
                              if e.kind == "part" and e.lead is not None
                              and spec["lead"](e.lead) is not None],
                             spec["same"])
            if len(vals) != 1:
                continue
            for w in net:
                if not w.deliberate and not spec["same"](spec["wire"](w), vals[0]):
                    changes.setdefault(w.id, {})[attr] = vals[0]
    return changes


def _part_name(end) -> str:
    return (end.label or end.ref).split(":", 1)[0]


def _swap_fix(snap: Snapshot, net: list[Wire], attr: str, v) -> Fix | None:
    """Swap every part in *net* whose lead is not *v* for its twin, when
    each has a twin drawing *v* at that port."""
    spec = _ATTRS[attr]
    movers: dict[str, str] = {}             # ref -> part name
    for w in net:
        for e in w.ends():
            if e.kind != "part" or e.lead is None:
                continue
            have = spec["lead"](e.lead)
            if have is None or spec["same"](have, v):
                continue
            twin = snap.twins.get(e.ref)
            tl = twin.leads.get(e.port) if twin is not None else None
            got = spec["lead"](tl) if tl is not None else None
            if got is None or not spec["same"](got, v):
                return None
            movers[e.ref] = _part_name(e)
    if not movers:
        return None
    leads: dict[tuple[str, str], dict] = {}
    touched: set[str] = set()
    for w in snap.wires.values():
        for e in w.ends():
            if e.kind == "part" and e.ref in movers:
                tl = snap.twins[e.ref].leads.get(e.port)
                if tl is not None:
                    leads[(e.ref, e.port)] = {
                        k: val for k, val in (("width", tl.width),
                                              ("color", tl.color))
                        if val is not None}
                touched.add(w.id)
    libs = sorted({snap.twins[r].library for r in movers})
    probe = Fix("", leads=leads)
    changes = _follow_leads(snap.apply(probe), touched)
    names = ", ".join(sorted(set(movers.values())))
    return Fix(f"Swap {names} to {'/'.join(libs)}", changes,
               swaps={r: snap.twins[r].key for r in movers}, leads=leads,
               kind="swap", target=v)


def _part_fixes(snap: Snapshot, net: list[Wire], attr: str,
                values: list) -> list[Fix]:
    """Swap fixes toward each of *values*, then lead fixes (step 4)."""
    out: list[Fix] = []
    for v in values:
        fx = _swap_fix(snap, net, attr, v)
        if fx is not None:
            out.append(fx)
    return out


def _check_attr(net: list[Wire], attr: str,
                snap: Snapshot | None = None) -> list[Finding]:
    """Findings for one net and attribute. With *snap*, findings also carry
    the part fixes (swap, lead); without it only wire fixes, which is all a
    re-check needs and keeps it from recursing."""
    spec = _ATTRS[attr]
    live = [w for w in net if not w.deliberate]
    if not live:
        return []
    leads = []          # (value, end label)
    for w in net:
        for e in w.ends():
            if e.kind == "part" and e.lead is not None:
                v = spec["lead"](e.lead)
                if v is not None:
                    leads.append((v, e.label or f"{e.ref}:{e.port}"))
    values = _distinct([v for v, _ in leads], spec["same"])
    fmt, word = spec["fmt"], spec["word"]

    if len(values) == 1:
        want = values[0]
        return [Finding(
            check_id=f"wire-{attr}",
            message=(f"wire is {fmt(spec['wire'](w))}; the lead it continues "
                     f"is {fmt(want)}"),
            wire_ids=(w.id,),
            fixes=(*(_part_fixes(snap, net, attr, [spec["wire"](w)])
                     if snap is not None else ()),
                   Fix(f"Use {fmt(want)}", {w.id: {attr: want}},
                       target=want)),
            data={"expected": want, "actual": spec["wire"](w)},
        ) for w in live if not spec["same"](spec["wire"](w), want)]

    ids = tuple(w.id for w in net)
    if len(values) > 1:
        sides = ", ".join(f"{label} {fmt(v)}" for v, label in leads)
        suffix = "" if attr == "width" else "-color"
        return [Finding(
            check_id=f"leads-disagree{suffix}",
            message=f"leads at this wire's ends differ in {word}: {sides}",
            wire_ids=ids,
            fixes=(*(_part_fixes(snap, net, attr, values)
                     if snap is not None else ()),
                   *(Fix(f"Use {fmt(v)}",
                         {w.id: {attr: v, "deliberate": True} for w in live},
                         target=v)
                     for v in values)),
            data={"leads": leads},
        )]

    present = _distinct([spec["wire"](w) for w in net], spec["same"])
    if len(present) > 1:
        return [Finding(
            check_id=f"net-mixed-{attr}",
            message=(f"wires joined here have different {word}s: "
                     + ", ".join(fmt(v) for v in present)),
            wire_ids=ids,
            fixes=tuple(Fix(f"Use {fmt(v)}", {w.id: {attr: v} for w in live},
                            target=v)
                        for v in present),
        )]
    return []


def check(snap: Snapshot, *, part_fixes: bool = True) -> list[Finding]:
    """Every finding, nets in a stable order, width before colour.

    *part_fixes* False leaves out the swap and lead fixes: what a re-check
    compares is the findings' keys, and building those fixes re-checks
    candidates of its own.
    """
    out: list[Finding] = []
    for net in nets(snap):
        for attr in ("width", "color"):
            out.extend(_check_attr(net, attr, snap if part_fixes else None))
    return out


def _keys(snap: Snapshot) -> set:
    return {f.key for f in check(snap, part_fixes=False)}


def verified(snap: Snapshot, finding: Finding, fix: Fix) -> bool:
    """Whether *fix* resolves *finding* without raising anything new.

    The candidate is checked from scratch, so a fix that is right for its
    own wire but disturbs a neighbour in the same net is refused.
    """
    before = _keys(snap)
    after = _keys(snap.apply(fix))
    return finding.key not in after and after <= before


def rejected(snap: Snapshot) -> list[Finding]:
    """Findings that are hidden only because their wires are deliberate.

    The check is run again as if no wire were deliberate; what that finds
    and the real check does not is what a "kept on purpose" mark is
    suppressing — a rejected suggestion, or a decision already taken on
    disagreeing leads. The Check Wiring panel lists these under
    *Show rejected* so a mark can be found and undone.
    """
    from dataclasses import replace
    live = _keys(snap)
    bare = Snapshot({wid: replace(w, deliberate=False)
                     for wid, w in snap.wires.items()}, snap.twins)
    return [f for f in check(bare) if f.key not in live]


def combine(snap: Snapshot, picks: list[tuple[Finding, Fix]],
            label: str) -> tuple[Fix, int]:
    """Fold several (finding, fix) choices into one :class:`Fix`.

    Each is verified against the snapshot as the earlier ones leave it, so
    two fixes that are each fine alone but fight over a shared net cannot
    both go in. Returns the combined fix and how many picks it took.
    """
    changes: dict[str, dict] = {}
    swaps: dict[str, str] = {}
    leads: dict[tuple[str, str], dict] = {}
    cur = snap
    taken = 0
    for finding, fix in picks:
        if finding.key not in _keys(cur):
            taken += 1          # an earlier pick already cleared it
            continue
        if not verified(cur, finding, fix):
            continue
        for wid, attrs in fix.changes.items():
            changes.setdefault(wid, {}).update(attrs)
        swaps.update(fix.swaps)
        leads.update(fix.leads)
        cur = cur.apply(fix)
        taken += 1
    return Fix(label, changes, swaps=swaps, leads=leads), taken
