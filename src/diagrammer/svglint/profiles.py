"""Per-library convention profiles, and how one is resolved for a file.

The spec's House Conventions are explicitly *not* app-enforced, and they vary
per library: the same symbol ships into PAPER_2pt at a 2 px lead and into
CRYO_WIRING_6pt at 6 px. So a check cannot compare against a constant. It
compares against the profile of whichever library the file sits in.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from diagrammer.svglint.model import LibraryProfile

logger = logging.getLogger(__name__)

#: The sidecar a library folder may carry to declare its own conventions.
SIDECAR_NAME = "library.json"

#: Profiles for the shipped libraries. The three lead weights are the ones
#: ``ConnectCommand._get_lead_stroke_width`` hands to an attached wire, so they
#: have to be exact — a mismatch makes the wire step in thickness at the join.
#:
#: FLAIR and flowchart carry ``lead_width=None``, which *disables* the
#: lead-width check: FLAIR is illustrative artwork with no fixed weight, and
#: flowchart shapes have no leads at all.
#:
#: The ``corner_radius`` values are measured from the shipped artwork, not
#: chosen: PAPER_2pt and SLIDES_3pt use ``rx="4"`` on 25 of 28 rounded bodies
#: each, CRYO_WIRING_6pt uses ``rx="1.9"`` on 23 of 27. FLAIR's three rounded
#: shapes use 4, 6 and 10 and flowchart's two use 4 and 23, so neither has a
#: convention and both carry ``None``.
BUILTIN_PROFILES: dict[str, LibraryProfile] = {
    "PAPER_2pt": LibraryProfile(
        name="PAPER_2pt", display_name="Paper (2 pt)",
        grid=20.0, lead_width=2.0, corner_radius=4.0,
        linecap="butt", linejoin="round", source="builtin",
    ),
    "SLIDES_3pt": LibraryProfile(
        name="SLIDES_3pt", display_name="Slides (3 pt)",
        grid=20.0, lead_width=3.0, corner_radius=4.0,
        linecap="butt", linejoin="round", source="builtin",
    ),
    "CRYO_WIRING_6pt": LibraryProfile(
        name="CRYO_WIRING_6pt", display_name="Cryo Wiring (6 pt)",
        grid=20.0, lead_width=6.0, corner_radius=1.9,
        linecap="butt", linejoin="round", source="builtin",
    ),
    "FLAIR": LibraryProfile(
        name="FLAIR", display_name="Flair",
        grid=20.0, lead_width=None, corner_radius=None,
        linecap="butt", linejoin="round", source="builtin",
    ),
    "flowchart": LibraryProfile(
        name="flowchart", display_name="Flowchart",
        grid=20.0, lead_width=None, corner_radius=None,
        linecap="butt", linejoin="round", source="builtin",
    ),
}

#: What an unrecognised library gets: the app-wide defaults, and no lead-width
#: expectation, because guessing one would invent errors in a user's own folder.
GENERIC_PROFILE = LibraryProfile(name="GENERIC", display_name="Unknown library",
                                 lead_width=None, source="default")

_FLOAT_FIELDS = ("grid", "lead_width", "corner_radius")
_STR_FIELDS = ("display_name", "linecap", "linejoin")


def _coerce(name: str, raw: Mapping, base: LibraryProfile,
            source: str, origin: str) -> LibraryProfile:
    """Build a profile from ``raw``, keeping ``base`` for anything absent.

    Only declared fields are read, and a malformed one is logged and skipped
    rather than raising: a typo in a hand-edited ``library.json`` should cost
    the user that one setting, not the ability to validate the folder at all.
    """
    kw: dict[str, object] = {}
    for key in _FLOAT_FIELDS:
        if key not in raw:
            continue
        val = raw[key]
        if val is None and key in ("lead_width", "corner_radius"):
            kw[key] = None  # an explicit "this library has no convention"
            continue
        try:
            kw[key] = float(val)
        except (TypeError, ValueError):
            logger.warning("%s: ignoring %s=%r (not a number)", origin, key, val)
    for key in _STR_FIELDS:
        if key in raw and isinstance(raw[key], str):
            kw[key] = raw[key]
    ignored = raw.get("ignored_checks")
    if isinstance(ignored, (list, tuple)):
        kw["ignored_checks"] = frozenset(str(c) for c in ignored)

    if kw.get("grid", base.grid) is not None and float(kw.get("grid", base.grid)) <= 0:
        logger.warning("%s: ignoring grid=%r (must be positive)", origin, kw.get("grid"))
        kw.pop("grid", None)

    merged = {
        "name": name,
        "display_name": base.display_name,
        "grid": base.grid,
        "lead_width": base.lead_width,
        "corner_radius": base.corner_radius,
        "linecap": base.linecap,
        "linejoin": base.linejoin,
        "ignored_checks": base.ignored_checks,
        "source": source,
    }
    merged.update(kw)
    return LibraryProfile(**merged)  # type: ignore[arg-type]


def _find_sidecar(path: Path) -> tuple[Path, Mapping] | None:
    """Nearest ancestor ``library.json``, walking up from the file."""
    for parent in path.resolve().parents:
        candidate = parent / SIDECAR_NAME
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("%s is not readable JSON (%s) — ignoring it",
                           candidate, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("%s does not hold a JSON object — ignoring it",
                           candidate)
            continue
        return candidate, data
    return None


def builtin_profile_name(path: Path) -> str | None:
    """Name of the shipped library ``path`` sits in, by path component.

    The same heuristic the skill validator already uses. It works on a user's
    overlay too, because the overlay mirrors the built-in tree by relative
    path — so ``~/.diagrammer/components/PAPER_2pt/foo.svg`` is correctly read
    as a PAPER_2pt part.
    """
    for part in path.parts:
        if part in BUILTIN_PROFILES:
            return part
    return None


def profile_for(path: Path, *,
                overrides: Mapping[str, Mapping] | None = None
                ) -> LibraryProfile:
    """Resolve the conventions that apply to ``path``. Most specific wins.

    1. the nearest ancestor ``library.json``
    2. a Settings override, keyed by profile name
    3. the built-in profile for a shipped library
    4. ``GENERIC_PROFILE``

    The sidecar beats Settings on purpose: it travels with the folder, so a
    library shared between machines carries its own rules, while a per-machine
    Settings entry cannot know about a library it has never seen.
    """
    name = builtin_profile_name(path)
    base = BUILTIN_PROFILES.get(name) if name else None
    if base is None:
        base = GENERIC_PROFILE
        name = name or GENERIC_PROFILE.name

    if overrides and name in overrides:
        base = _coerce(name, overrides[name], base, "settings",
                       f"settings override for {name}")

    found = _find_sidecar(path)
    if found is not None:
        sidecar, data = found
        # A sidecar names its own library when the folder is not a shipped one.
        sidecar_name = data.get("name") if isinstance(data.get("name"), str) else None
        return _coerce(sidecar_name or name or sidecar.parent.name,
                       data, base, "library.json", str(sidecar))

    return base
