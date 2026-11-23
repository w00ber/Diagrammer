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


def _check_attr(net: list[Wire], attr: str) -> list[Finding]:
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
            fixes=(Fix(f"Use {fmt(want)}", {w.id: {attr: want}}),),
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
            fixes=tuple(
                Fix(f"Use {fmt(v)}",
                    {w.id: {attr: v, "deliberate": True} for w in live})
                for v in values),
            data={"leads": leads},
        )]

    present = _distinct([spec["wire"](w) for w in net], spec["same"])
    if len(present) > 1:
        return [Finding(
            check_id=f"net-mixed-{attr}",
            message=(f"wires joined here have different {word}s: "
                     + ", ".join(fmt(v) for v in present)),
            wire_ids=ids,
            fixes=tuple(Fix(f"Use {fmt(v)}", {w.id: {attr: v} for w in live})
                        for v in present),
        )]
    return []


def check(snap: Snapshot) -> list[Finding]:
    """Every finding, nets in a stable order, width before colour."""
    out: list[Finding] = []
    for net in nets(snap):
        for attr in ("width", "color"):
            out.extend(_check_attr(net, attr))
    return out


def verified(snap: Snapshot, finding: Finding, fix: Fix) -> bool:
    """Whether *fix* resolves *finding* without raising anything new.

    The candidate is checked from scratch, so a fix that is right for its
    own wire but disturbs a neighbour in the same net is refused.
    """
    before = {f.key for f in check(snap)}
    after = {f.key for f in check(snap.apply(fix))}
    return finding.key not in after and after <= before
