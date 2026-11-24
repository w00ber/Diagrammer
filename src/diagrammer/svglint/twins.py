"""Cross-library twin check: PAPER_2pt and SLIDES_3pt, side by side.

Every other check here reads one file. A twin pair can drift apart while each
file stays valid on its own — the bias tees did, ``top``/``bottom``/``dc`` in
one library and ``RF``/``COM``/``DC`` in the other — so this reads both
libraries together. Convert to Library (issue #47) swaps a placed part for its
twin and moves each wire to the port *of the same name*, so:

* ``twin-port-names`` (error): the twins' port names differ. The swap is
  refused for that part; a wire on a missing name would have nowhere to go.
* ``twin-port-positions`` (error): a port of the same name sits elsewhere.
  The swap would go through and drag every wire on that port.
* ``no-twin`` (info): nothing in the other library. Some parts are
  deliberately one-library (the coax cut variants), so this never fails.

Twins are matched the way the app matches them (:func:`find_twin`, which
``wirecheck.scene.twin_key`` also uses): the same path under the library root,
else a file name that is unique in the other library, because the libraries'
folders have drifted (``circuits/µwave/splitters`` vs ``circuits/splitters``).
Keys compare case-insensitively, like ``ComponentLibrary.get``.

No PySide6, like the rest of :mod:`diagrammer.svglint`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from diagrammer.svglint.model import Finding, Marker

#: The libraries that come in twins, and their lead weight. CRYO_WIRING_6pt
#: shares no parts with these and is not checked.
TWIN_LIBRARIES: dict[str, float] = {"PAPER_2pt": 2.0, "SLIDES_3pt": 3.0}

#: Port positions closer than this are the same position (viewBox units).
POSITION_TOL = 0.05


def find_twin(rel: str, others: Iterable[str]) -> str | None:
    """The key in *others* that is *rel*'s twin, or None.

    *rel* and *others* are keys relative to their library roots, without
    ``.svg`` (``circuits/LCR/CAP1``). The same path first; else the one key
    with the same file name, when exactly one has it. Case-insensitive.
    """
    others = list(others)
    folded = rel.casefold()
    for o in others:
        if o.casefold() == folded:
            return o
    stem = folded.rsplit("/", 1)[-1]
    same = [o for o in others if o.casefold().rsplit("/", 1)[-1] == stem]
    return same[0] if len(same) == 1 else None


@dataclass(frozen=True)
class TwinFinding:
    """A :class:`Finding` about one part, with the files it compares."""

    path: Path
    finding: Finding
    twin: Path | None = None


def _ports(path: Path) -> dict[str, tuple[float, float]] | None:
    from diagrammer.models.component_def import ComponentDef
    try:
        cdef = ComponentDef.from_svg_text(path.read_text(encoding="utf-8"),
                                          path=path, infer_stretch=False)
    except Exception:
        return None             # the per-file checks report a broken file
    return {p.name: (p.x, p.y) for p in cdef.ports}


def _keys(root: Path) -> dict[str, Path]:
    """``{relative key: file}`` for a library, skipping dot-folders as the
    library scan does."""
    out: dict[str, Path] = {}
    for p in sorted(root.rglob("*.svg")):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        out[rel.with_suffix("").as_posix()] = p
    return out


def _compare(a: Path, b: Path, other_lib: str) -> list[Finding]:
    pa, pb = _ports(a), _ports(b)
    if pa is None or pb is None:
        return []
    if set(pa) != set(pb):
        only_a = sorted(set(pa) - set(pb))
        only_b = sorted(set(pb) - set(pa))
        return [Finding(
            check_id="twin-port-names",
            severity="error",
            message=(f"port names differ from its {other_lib} twin: "
                     f"only here {', '.join(only_a) or '-'}; "
                     f"only there {', '.join(only_b) or '-'}"),
            detail=("Convert to Library moves each wire to the twin's port of "
                    "the same name, so it refuses to swap this part. Rename "
                    "the ports to match (by position), in the same change, "
                    "and add the old names to RENAMED_PORTS in "
                    "io/serializer.py so saved diagrams keep their wires."),
            markers=tuple(Marker("point", pa[n], label=f"port:{n}")
                          for n in only_a),
            data={"twin": b.as_posix()},
        )]
    moved = [n for n in sorted(pa)
             if abs(pa[n][0] - pb[n][0]) > POSITION_TOL
             or abs(pa[n][1] - pb[n][1]) > POSITION_TOL]
    if not moved:
        return []
    return [Finding(
        check_id="twin-port-positions",
        severity="error",
        message=(f"ports sit elsewhere in its {other_lib} twin: "
                 + "; ".join(f"{n} ({pa[n][0]:g}, {pa[n][1]:g}) vs "
                             f"({pb[n][0]:g}, {pb[n][1]:g})" for n in moved)),
        detail=("Convert to Library keeps the part where it is and moves "
                "each wire to the twin's port of the same name, so every "
                "wire on these ports would be dragged. Twins differ only in "
                "line weight."),
        markers=tuple(Marker("point", pa[n], label=f"port:{n}") for n in moved),
        data={"twin": b.as_posix()},
    )]


def check_twins(components: Path,
                libraries: Sequence[str] = tuple(TWIN_LIBRARIES)
                ) -> list[TwinFinding]:
    """Check every part of each library in *libraries* against its twin.

    *components* holds the library folders (``src/diagrammer/components``).
    A drifting pair is reported once, against the first library's file; a
    part with no twin is reported from its own side.
    """
    roots = {lib: components / lib for lib in libraries
             if (components / lib).is_dir()}
    keys = {lib: _keys(root) for lib, root in roots.items()}
    out: list[TwinFinding] = []
    order = list(roots)
    for i, lib in enumerate(order):
        for rel, path in keys[lib].items():
            twinned = False
            for other in order:
                if other == lib:
                    continue
                match = find_twin(rel, keys[other])
                if match is None:
                    continue
                twinned = True
                if order.index(other) > i:        # each pair once
                    tpath = keys[other][match]
                    out.extend(TwinFinding(path, f, tpath)
                               for f in _compare(path, tpath, other))
            if not twinned:
                out.append(TwinFinding(path, Finding(
                    check_id="no-twin",
                    severity="info",
                    message=("no twin in "
                             + ", ".join(o for o in order if o != lib)),
                    detail=("Convert to Library leaves this part as it is. "
                            "Some parts exist in one library on purpose."),
                )))
    return out
