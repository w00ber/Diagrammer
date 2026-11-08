"""Core types for component validation: Finding, Fix, LibraryProfile.

Deliberately free of PySide6 and of any import from the rest of the app beyond
``ComponentDef``, so the same engine runs in the GUI, in the ``svg-component``
skill script, and in a git hook under a bare ``python3``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["error", "warning", "info"]

#: Order, worst first, for sorting findings and for picking a file's verdict.
SEVERITY_RANK: dict[str, int] = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Marker:
    """Offending geometry, in viewBox units.

    A component's viewBox units *are* scene points, 1:1 and with no autoscaling
    (see the spec's coordinate section), so a marker needs no transform to land
    on the artwork in the inspector — it is a child of the component item and
    paints in the same coordinates the SVG was authored in.
    """

    kind: Literal["point", "segment", "rect", "edge"]
    #: point (x, y) | segment (x1, y1, x2, y2) | rect (x, y, w, h) | edge ()
    coords: tuple[float, ...] = ()
    #: "left" | "right" | "top" | "bottom", for ``kind="edge"``
    edge: str = ""
    #: short tag drawn beside the marker, e.g. "port:in"
    label: str = ""


@dataclass(frozen=True)
class Finding:
    """One thing wrong with one component.

    ``message`` is the single line a report row shows; ``detail`` is the
    tooltip body and says *why it matters*, since every check here exists
    because some app code path misbehaves silently — which is exactly what the
    reader cannot infer from the geometry.
    """

    check_id: str
    severity: Severity
    message: str
    detail: str = ""
    markers: tuple[Marker, ...] = ()
    #: the offender's ``id`` when it has one, e.g. "port:left"
    element_id: str = ""
    #: character offsets into the document text, for the diff box to scroll to
    source_span: tuple[int, int] | None = None
    #: ids of fixes that address *this* finding
    fix_ids: tuple[str, ...] = ()
    #: numbers a finding-scoped fix needs, so it never re-derives geometry:
    #: {"dx": 0.4, "dy": 0} or {"found": 2.0, "expected": 3.0}
    data: Mapping[str, float | str] = field(default_factory=dict)

    @property
    def rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 99)


@dataclass(frozen=True)
class FixResult:
    """What a fix did. ``notes`` are shown verbatim in the Changes box."""

    text: str
    changed: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FixContext:
    """Everything a fix may read.

    ``original_text`` is always the pristine document, never the candidate
    being built. Fixes compose from the original every time, which is what
    makes unticking a checkbox exact without an inverse transform and makes the
    result a pure function of the *set* of selected fixes rather than of the
    order they were clicked in.
    """

    profile: LibraryProfile
    path: Path
    original_text: str
    #: set when ``Fix.scope == "finding"``
    finding: Finding | None = None


FixFn = Callable[[str, FixContext], FixResult]


# Application-order bands. The ordering is load-bearing, not cosmetic:
#
#  STRUCTURAL first, because every later regex assumes named layers are direct
#  children of <svg> and that ports and leads are real <circle>/<line>
#  primitives rather than path approximations.
#
#  STYLE next: geometry-preserving rewrites (stroke width, linecap) that want
#  to find elements where the author left them.
#
#  GEOMETRY after that, because it rewrites the very coordinates the earlier
#  passes located.
#
#  COSMETIC last — whitespace and dead elements, which nothing else reads.
ORDER_STRUCTURAL = 10
ORDER_STYLE = 20
ORDER_GEOMETRY = 30
ORDER_COSMETIC = 40


@dataclass(frozen=True)
class Fix:
    """A composable text -> text repair, offered to the user as a checkbox."""

    fix_id: str
    title: str
    description: str
    order: int
    scope: Literal["file", "finding"]
    apply: FixFn = field(compare=False, repr=False)
    #: moves coordinates rather than restyling. Never ``default_on``, and the
    #: inspector flags it, because the nearest grid line is not always the one
    #: the author meant.
    moves_geometry: bool = False
    default_on: bool = False
    #: safe to offer as "fix in all N files" from the batch report
    bulk_safe: bool = False


@dataclass(frozen=True)
class LibraryProfile:
    """The house conventions of one component library.

    These are the spec's "House Conventions": not app-enforced, but a part that
    ignores them looks wrong next to its neighbours. They vary per library, so
    they cannot be constants — ``lead_width`` is the clearest case, because the
    same symbol ships into PAPER_2pt at 2 px and CRYO_WIRING_6pt at 6 px, and
    the attached wire takes its width from whichever it came from.
    """

    name: str
    display_name: str = ""
    grid: float = 20.0
    #: ``None`` means "this library has no fixed lead weight", which *disables*
    #: the lead-width check rather than comparing against zero. FLAIR and
    #: flowchart are both like this by design.
    lead_width: float | None = None
    #: ``rx`` the library's rounded bodies use. ``None`` where the library has
    #: no settled value — FLAIR's three rounded shapes use 4, 6 and 10, so
    #: there is nothing to conform to and no check to run.
    corner_radius: float | None = None
    linecap: str = "butt"
    linejoin: str = "round"
    #: check ids this library opts out of. Needed from day one: circuits/ parts
    #: deliberately centre their common port at cy=height/2, which is off the
    #: viewBox edge on purpose.
    ignored_checks: frozenset[str] = frozenset()
    source: Literal["library.json", "settings", "builtin", "default"] = "default"

    @property
    def label(self) -> str:
        return self.display_name or self.name
