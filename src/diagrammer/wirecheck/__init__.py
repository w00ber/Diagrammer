"""Wiring checks: does each wire look like the leads it connects?

Issue #47. A wire continues a part's lead, so its width and colour should be
the lead's. A junction has no lead of its own; it belongs to a *net* — every
wire reachable through junctions — and the net takes its style from the part
leads at its ends. These checks compute what each wire should be and report
where it isn't, each finding carrying the fixes that would resolve it.

The same shape as ``svglint``: a check reads a model, a fix is proposed as a
candidate, and the candidate is checked again. A fix is offered only if that
re-check clears its finding and raises nothing new (see :func:`verified`).

**No Qt in the engine.** :mod:`diagrammer.wirecheck.model` and
:mod:`diagrammer.wirecheck.checks` run on a plain :class:`Snapshot`, so they
are testable headless and usable from the MCP server. Building a snapshot from
a live scene is :func:`diagrammer.wirecheck.scene.snapshot`, the one place
that touches Qt, imported only by callers that have a scene.

Typical use::

    from diagrammer import wirecheck
    from diagrammer.wirecheck.scene import snapshot

    snap = snapshot(scene)
    for f in wirecheck.check(snap):
        good = [fix for fix in f.fixes if wirecheck.verified(snap, f, fix)]
"""

from __future__ import annotations

from diagrammer.wirecheck.checks import check, combine, rejected, verified
from diagrammer.wirecheck.model import (
    COLOR_TOL,
    WIDTH_TOL,
    End,
    Finding,
    Fix,
    Snapshot,
    Wire,
    colors_match,
)

__all__ = [
    "COLOR_TOL", "WIDTH_TOL", "End", "Finding", "Fix", "Snapshot", "Wire",
    "check", "colors_match", "combine", "rejected", "verified",
]
