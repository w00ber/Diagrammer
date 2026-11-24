"""The snapshot the wiring checks read, and what they report. No Qt."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from diagrammer.models.lead_style import LeadStyle

#: Widths closer than this are equal (the SVGs write 2, 2px, 2.0).
WIDTH_TOL = 0.01

#: Largest per-channel difference (0-255) still "the same colour". The
#: shipped libraries are pure #000 now (svglint's ``near-black`` check found
#: Illustrator's #231f20 on 80 ports, and its fix replaced it), but a user's
#: own library exported from Illustrator will carry it, and flagging every
#: wire on such a part would bury real findings. 48 keeps the two blacks
#: together and still separates black from the darkest colours the libraries
#: use (#0d72ba blue differs by 186, #cc3333 red by 204).
COLOR_TOL = 48


def colors_match(a: str | None, b: str | None) -> bool:
    """Same colour within :data:`COLOR_TOL`. ``None`` (unknown) matches all."""
    if a is None or b is None:
        return True
    try:
        ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return a.lower() == b.lower()
    return max(abs(x - y) for x, y in zip(ca, cb)) <= COLOR_TOL


@dataclass(frozen=True)
class End:
    """One end of a wire.

    ``kind`` is ``"part"`` (a component port: ``ref`` is the component's
    instance id, ``port`` its port name, ``lead`` the lead drawn there) or
    ``"node"`` (a junction: ``ref`` is the junction id, no lead).
    """

    kind: str
    ref: str
    port: str = ""
    lead: LeadStyle | None = None
    label: str = ""          # human name for messages, e.g. "CAP1:top"


@dataclass(frozen=True)
class Wire:
    id: str
    a: End
    b: End
    width: float
    color: str               # "#rrggbb"
    #: The user chose this style on purpose (kept a hand-set width, or
    #: rejected a suggestion). Never flagged, never changed by a fix.
    deliberate: bool = False

    def ends(self) -> tuple[End, End]:
        return self.a, self.b


@dataclass(frozen=True)
class Fix:
    """A proposed correction.

    ``changes`` maps wire id -> {attr: value}, attrs being ``width``,
    ``color`` or ``deliberate``. A fix can also act on parts:

    * ``swaps`` maps a part's instance id -> the library key of its twin
      (Convert to Library: the same drawing at the other weight);
    * ``leads`` maps ``(part id, port)`` -> {"width"/"color": value}: what
      the lead at that port becomes. For a swap it is the twin's lead there,
      recorded so the candidate can be re-checked without a library; on its
      own it is a per-instance lead override.

    ``kind`` is ``"swap"``, ``"lead"`` or ``"wire"`` (the order the notice
    and the Check Wiring panel offer them in) and ``target`` the value the
    fix makes the net agree on, which is how a fix is matched to the rest of
    the diagram.
    """

    label: str
    changes: dict[str, dict] = field(default_factory=dict)
    swaps: dict[str, str] = field(default_factory=dict)
    leads: dict[tuple[str, str], dict] = field(default_factory=dict)
    kind: str = "wire"
    target: object = None


@dataclass(frozen=True)
class Twin:
    """A part's twin in the other weight library: its key, that library's
    name, and the lead the twin draws at each port."""

    key: str
    library: str
    leads: dict[str, LeadStyle | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    check_id: str            # "wire-width", "leads-disagree", "net-mixed-width", "wire-color", ...
    message: str
    wire_ids: tuple[str, ...]
    fixes: tuple[Fix, ...] = ()
    data: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        """Identity across re-checks: the same fault on the same wires."""
        return self.check_id, tuple(sorted(self.wire_ids))


@dataclass(frozen=True)
class Snapshot:
    wires: dict[str, Wire]
    #: part instance id -> its twin, for parts that have one. Filled by
    #: ``wirecheck.scene.snapshot`` from the library; empty means no swap
    #: fixes are offered.
    twins: dict[str, Twin] = field(default_factory=dict)

    def apply(self, fix: Fix) -> Snapshot:
        """A new snapshot with *fix* applied; this one is untouched."""
        wires = dict(self.wires)
        if fix.leads:
            for wid, w in wires.items():
                wires[wid] = replace(w, a=_relead(w.a, fix.leads),
                                     b=_relead(w.b, fix.leads))
        for wid, attrs in fix.changes.items():
            if wid in wires:
                wires[wid] = replace(wires[wid], **attrs)
        twins = self.twins
        if fix.swaps:
            # A swapped part's twin is the part it came from; leave it out
            # rather than guess the way back.
            twins = {r: t for r, t in twins.items() if r not in fix.swaps}
        return Snapshot(wires, twins)


def _relead(end: End, leads: dict[tuple[str, str], dict]) -> End:
    attrs = leads.get((end.ref, end.port)) if end.kind == "part" else None
    if attrs is None:
        return end
    base = end.lead or LeadStyle(None, None)
    return replace(end, lead=LeadStyle(attrs.get("width", base.width),
                                       attrs.get("color", base.color)))
