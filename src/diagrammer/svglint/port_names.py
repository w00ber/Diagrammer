"""Directional port names versus the side the app thinks the port is on.

A port called ``left`` is a promise to whoever wires it by name: a person
reading the Properties panel, or an agent driving the headless API, which
attaches wires by port name and is told nothing else. Routing itself never
reads names; it reads the approach direction ``ComponentDef`` infers from the
port's position. So a mismatch is silent in the app and wrong everywhere
else: the quantum parts shipped ``bot`` at the top for years.

The oracle is the app's own: ``_assign_approach_directions`` via
``ComponentDef``. A port the app gives no direction (an interior port, such
as an instrument's front-panel connector) is not judged, and a corner port
may carry either of its two sides' names.

Renaming is safe for saved diagrams: the loader rebinds a saved port name
to the library port at the same position (see ``io/serializer.py``,
``_port_aliases``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_DIRECTIONAL = re.compile(r"(left|right|top|bottom|bot)(\d*)")
_SPELLING = {"bot": "bottom"}


@dataclass(frozen=True)
class Misnamed:
    pid: str            # "port:bot"
    x: float
    y: float
    sides: tuple[str, ...]   # the side(s) the app puts it on
    new: str | None          # proposed "port:top", or None if no safe rename


def _sides(dx: float, dy: float) -> tuple[str, ...]:
    out = []
    if dx < 0:
        out.append("left")
    elif dx > 0:
        out.append("right")
    if dy < 0:
        out.append("top")
    elif dy > 0:
        out.append("bottom")
    return tuple(out)


def misnamed_ports(text: str, path: Path) -> list[Misnamed]:
    """Every directional port name that disagrees with where the port sits.

    ``new`` is filled in only when the whole file can be renamed without
    two ports landing on one name; XFORMER1, with two ports on each of its
    top and bottom edges, has no rename a rule can choose.
    """
    from diagrammer.models.component_def import ComponentDef

    try:
        cdef = ComponentDef.from_svg_text(text, path=path, infer_stretch=False)
    except Exception:
        return []

    bad: list[tuple[object, tuple[str, ...], str | None, str]] = []
    final: dict[str, str] = {}  # port name -> name after the rename
    for p in cdef.ports:
        final[p.name] = p.name
        m = _DIRECTIONAL.fullmatch(p.name)
        sides = _sides(p.approach_dx, p.approach_dy)
        if m is None or not sides:
            continue
        written, suffix = m.group(1), m.group(2)
        base = _SPELLING.get(written, written)
        if base in sides and written == base:
            continue
        target = base if base in sides else (sides[0] if len(sides) == 1 else None)
        bad.append((p, sides, target, suffix))

    for p, _sides_, target, suffix in bad:
        if target is not None:
            final[p.name] = target + suffix

    # A suffix only meant something next to its siblings; drop it when the
    # port is now the only one on that side ("right1" alone on the left
    # edge becomes "left", not "left1").
    for p, _sides_, target, suffix in bad:
        if target is None or not suffix:
            continue
        others = [n for k, n in final.items()
                  if k != p.name and _DIRECTIONAL.fullmatch(n)
                  and _SPELLING.get(_DIRECTIONAL.fullmatch(n).group(1),
                                    _DIRECTIONAL.fullmatch(n).group(1)) == target]
        if not others:
            final[p.name] = target

    names = list(final.values())
    clean = len(names) == len(set(names))
    return [
        Misnamed(pid=f"port:{p.name}", x=p.x, y=p.y, sides=sides,
                 new=(f"port:{final[p.name]}"
                      if clean and target is not None else None))
        for p, sides, target, _suffix in bad
    ]


def rename_ports(text: str, mapping: dict[str, str]) -> str:
    """Rename ``id="port:a"`` to ``id="port:b"`` for every pair at once.

    Simultaneous, so a swap (``bot``->``top`` while ``top``->``bottom``)
    cannot chain.
    """
    if not mapping:
        return text
    pat = re.compile(r'(\sid=")(' + "|".join(re.escape(k) for k in mapping) + r')(")')
    return pat.sub(lambda m: m.group(1) + mapping[m.group(2)] + m.group(3), text)
