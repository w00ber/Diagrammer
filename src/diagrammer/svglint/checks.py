"""The checks: one function per fault, each returning Findings.

Every check here exists because some app code path misbehaves *silently* on a
file that looks fine as a picture. That is the bar for inclusion, and it is also
why each Finding carries a ``detail`` explaining the consequence — the reader
cannot infer "wires will approach from the wrong side" from a port's
coordinates.

The bar for *severity* is the same distinction. An error means the component is
broken on the canvas. A warning means a human has to look, because the same
geometry can be deliberate: ``circuits/`` parts centre their common port off
the viewBox edge on purpose, and 127 of the 229 shipped files still declare
``stroke-linecap: round`` mid-migration. A validator that reported those as
errors would be noise, and noise gets ignored wholesale.
"""

from __future__ import annotations

import re
from pathlib import Path

from diagrammer.svglint import text_svg as T
from diagrammer.svglint.model import Finding, LibraryProfile, Marker


def _is_pure_extension(x1: float, y1: float, x2: float, y2: float,
                       ex: float, ey: float, px: float, py: float) -> bool:
    """Would snapping this endpoint to the port only *lengthen* the lead?

    The risk in moving a lead endpoint is not how far it moves — any gap the
    check reports is already past the app's 2 pt matching tolerance — it is
    whether the move skews the lead or detaches its other end. It does neither
    when the lead runs along one axis and the port sits further along that same
    axis: the endpoint then slides down the line the author already drew, the
    far end does not budge, and the result is the straight H or V lead the spec
    asks for.

    Anything else — a lead perpendicular to its port, or offset on both axes —
    would be rotated or bent by the move, so it gets no automatic fix.
    """
    vertical = abs(x1 - x2) < 1e-9
    horizontal = abs(y1 - y2) < 1e-9
    if vertical and not horizontal:
        return abs(ex - px) < 1e-9
    if horizontal and not vertical:
        return abs(ey - py) < 1e-9
    return False


def _port_markers(ports: list[tuple[str, float, float]], name: str
                  ) -> tuple[Marker, ...]:
    for pid, x, y in ports:
        if pid == name:
            return (Marker(kind="point", coords=(x, y), label=pid),)
    return ()


def check_parse(text: str, path: Path, profile: LibraryProfile) -> list[Finding]:
    """Does the app's own parser accept this file at all?

    Run first and alone: when it fails, ``ComponentLibrary.scan`` logs a warning
    and drops the component, so the user watches the part disappear from the
    panel with no explanation. Every later check would also be reading a
    document the app cannot load.
    """
    from diagrammer.models.component_def import ComponentDef

    try:
        ComponentDef.from_svg_text(text, path=path)
    except Exception as exc:  # the app would log and skip this file
        return [Finding(
            check_id="parse-failure",
            severity="error",
            message=f"does not parse: {exc}",
            detail=("ComponentLibrary.scan logs this and drops the file, so "
                    "the component silently vanishes from the Library panel "
                    "rather than reporting an error."),
        )]
    return []


def check_layer_wrapper(text: str, path: Path,
                        profile: LibraryProfile) -> list[Finding]:
    """Named layers must be direct children of ``<svg>``."""
    out: list[Finding] = []
    top = T.top_level_layer_ids(text)
    if re.search(r'<g\s+id="Layer_1"', text) and "Layer_1" in top:
        out.append(Finding(
            check_id="layer-wrapper",
            severity="error",
            message='wrapped in <g id="Layer_1"> — named layers must be '
                    "direct children of <svg>",
            detail=("Illustrator wraps an export in a layer group. The parser's "
                    "_find_group_by_id searches recursively so it still finds "
                    "artwork, but document order is what decides paint order, "
                    "and the wrapper also breaks the stretch and lead-group "
                    "conventions that read the top level."),
            element_id="Layer_1",
            fix_ids=("unwrap-layer-group",),
        ))
    return out


def check_artwork_layer(text: str, path: Path,
                        profile: LibraryProfile) -> list[Finding]:
    if re.search(r'<g id="artwork">', text):
        return []
    return [Finding(
        check_id="no-artwork-layer",
        severity="warning",
        message='no <g id="artwork"> layer',
        detail=("Legal — the whole SVG is then treated as artwork — but usually "
                "unintended, and it means lead shortening and stretch have no "
                "body to work against."),
    )]


def check_ports_exist(text: str, path: Path,
                      profile: LibraryProfile) -> list[Finding]:
    from diagrammer.models.component_def import ComponentDef

    try:
        cdef = ComponentDef.from_svg_text(text, path=path)
    except Exception:
        return []  # check_parse already reported it
    if cdef.ports or cdef.decorative:
        return []
    return [Finding(
        check_id="no-ports",
        severity="error",
        message="no ports, and not marked decorative",
        detail=("Nothing can connect to this component. Add <g id=\"ports\"> "
                "markers, or an empty <g id=\"decorative\"/> if it is meant to "
                "be free-floating artwork."),
    )]


def check_port_on_edge(text: str, path: Path,
                       profile: LibraryProfile) -> list[Finding]:
    """A port's approach direction is inferred from its distance to an edge."""
    from diagrammer.models.component_def import ComponentDef

    try:
        cdef = ComponentDef.from_svg_text(text, path=path)
    except Exception:
        return []
    w, h = cdef.width, cdef.height
    out: list[Finding] = []
    for p in cdef.ports:
        dists = {
            "left": abs(p.x), "right": abs(p.x - w),
            "top": abs(p.y), "bottom": abs(p.y - h),
        }
        nearest = min(dists, key=lambda k: dists[k])
        if dists[nearest] <= T.EDGE_TOL:
            continue
        out.append(Finding(
            check_id="port-off-edge",
            severity="warning",
            message=(f"port '{p.name}' at ({p.x:g},{p.y:g}) is not on a viewBox "
                     f"edge (0..{w:g} x 0..{h:g})"),
            detail=(f"Nearest edge is {nearest}, {dists[nearest]:g} pt away; "
                    f"the tolerance is {T.EDGE_TOL:g} pt. Approach direction is "
                    "inferred from position, so an interior port gets no "
                    "preferred direction and wires may attach from a side you "
                    "did not intend. This can be deliberate — circuits/ parts "
                    "centre their common port at cy=height/2 — so check it "
                    "rather than assume it is wrong."),
            markers=(Marker(kind="point", coords=(p.x, p.y), label=f"port:{p.name}"),
                     Marker(kind="edge", edge=nearest)),
            element_id=f"port:{p.name}",
            data={"nearest_edge": nearest, "distance": dists[nearest]},
        ))
    return out


def check_lead_endpoints(text: str, path: Path,
                         profile: LibraryProfile) -> list[Finding]:
    """Every lead must end on a port, or it is never shortened."""
    if not re.search(r'<g id="leads', text):
        return []
    ports = [(x, y) for _, x, y in T.ports_from_text(text)]
    if not ports:
        return []
    out: list[Finding] = []
    for x1, y1, x2, y2 in T.lead_endpoints(text):
        best = None
        for px, py in ports:
            for ex, ey in ((x1, y1), (x2, y2)):
                d = ((ex - px) ** 2 + (ey - py) ** 2) ** 0.5
                if best is None or d < best[0]:
                    best = (d, ex, ey, px, py)
        if best is None or best[0] < T.ENDPOINT_TOL:
            continue
        gap, ex, ey, px, py = best
        snappable = _is_pure_extension(x1, y1, x2, y2, ex, ey, px, py)
        out.append(Finding(
            check_id="lead-endpoint-off-port",
            severity="error",
            message=(f"lead ({x1:g},{y1:g})-({x2:g},{y2:g}) has no endpoint on "
                     f"a port — nearest is {gap:g} pt away"),
            detail=("_shorten_leads_in_svg only shortens a lead whose endpoint "
                    f"coincides with a port, within {T.ENDPOINT_TOL:g} pt. This "
                    "one is skipped, so the lead keeps its full length and the "
                    "wire's corner rounding overlaps the component."
                    + ("  The lead runs along the axis the port sits on, so "
                       "extending it to reach is a safe repair — review it in "
                       "Overlay mode first, since it does move geometry."
                       if snappable else
                       "  No automatic fix: the lead is not aligned with the "
                       "port, so moving its end would bend or rotate it.")),
            markers=(Marker(kind="segment", coords=(x1, y1, x2, y2)),
                     Marker(kind="point", coords=(ex, ey), label=f"{gap:g} pt off")),
            fix_ids=("snap-lead-end-to-port",) if snappable else (),
            data={"gap": gap, "end_x": ex, "end_y": ey,
                  "port_x": px, "port_y": py},
        ))
    return out


def check_lead_width(text: str, path: Path,
                     profile: LibraryProfile) -> list[Finding]:
    """The lead weight becomes the attached wire's weight, so it must be exact."""
    if not re.search(r'<g id="leads', text):
        return []
    found = T.lead_width(text)
    if found is None:
        return [Finding(
            check_id="lead-width-unresolvable",
            severity="error",
            message="lead layer present but its stroke width is unresolvable",
            detail=("ConnectCommand._get_lead_stroke_width reads this to size "
                    "the attached wire. With nothing to read it falls back to "
                    "the app default, so the wire will not match the lead."),
        )]
    if profile.lead_width is None:
        return []  # library has no fixed weight; nothing to conform to
    if abs(found - profile.lead_width) <= 0.01:
        return []
    return [Finding(
        check_id="lead-width-mismatch",
        severity="error",
        message=(f"lead width {found:g}px but {profile.label} draws leads at "
                 f"{profile.lead_width:g}px"),
        detail=("ConnectCommand._get_lead_stroke_width gives the attached wire "
                "the lead's width, so the wire will visibly step in thickness "
                "where it meets this component."),
        fix_ids=("set-lead-width",),
        data={"found": found, "expected": profile.lead_width},
    )]


def check_lead_linecap(text: str, path: Path,
                       profile: LibraryProfile) -> list[Finding]:
    """Leads want butt caps: a round or square cap overhangs the port."""
    if not re.search(r'<g id="leads', text):
        return []
    caps = T.css_property(text, "stroke-linecap")
    used = {caps[c] for c in T.lead_classes(text) if c in caps}
    offenders = sorted(c for c in used if c in ("round", "square"))
    if not offenders:
        return []
    width = T.lead_width(text) or 0.0
    return [Finding(
        check_id="lead-linecap-not-butt",
        severity="warning",
        message=f"leads declare stroke-linecap: {', '.join(offenders)}",
        detail=(f"A {offenders[0]} cap overhangs the endpoint by half the "
                f"stroke width ({width / 2:g} pt here), so the lead reaches "
                "past its port and the join with the wire thickens. The "
                "shipped libraries are mid-migration on this — 127 of 229 "
                "files still declare round — so it is a warning, not an "
                "error."),
        fix_ids=("set-lead-linecap-butt",),
        data={"found": offenders[0], "expected": profile.linecap},
    )]


def check_viewbox(text: str, path: Path,
                  profile: LibraryProfile) -> list[Finding]:
    """A missing or re-based viewBox breaks edge detection outright."""
    vb = T.viewbox(text)
    if vb is None:
        return [Finding(
            check_id="viewbox-missing",
            severity="error",
            message="no viewBox — the parser falls back to 100x100",
            detail=("Component geometry is 1:1 with the viewBox, so without one "
                    "the symbol is placed and scaled against a frame that has "
                    "nothing to do with its artwork."),
        )]
    x, y, w, h = vb
    out: list[Finding] = []
    if abs(x) > 1e-9 or abs(y) > 1e-9:
        out.append(Finding(
            check_id="viewbox-origin-nonzero",
            severity="warning",
            message=f"viewBox origin is ({x:g},{y:g}), not (0,0)",
            detail=("_assign_approach_directions compares port coordinates "
                    "against the viewBox width and height but ignores its "
                    "origin, so every port's approach direction is computed "
                    "against the wrong frame and wires attach from the wrong "
                    "sides. This is the signature of an Illustrator export "
                    'made without "Use Artboards".'),
            markers=(Marker(kind="rect", coords=(x, y, w, h)),),
            fix_ids=("rebase-viewbox-to-origin",),
            data={"x": x, "y": y, "width": w, "height": h},
        ))
    return out


def check_stray_anchors(text: str, path: Path,
                        profile: LibraryProfile) -> list[Finding]:
    """Illustrator leaves a path behind wherever an anchor outlived its shape."""
    count = 0
    for tag in re.finditer(r"<path\b[^>]*/>", text):
        t = tag.group(0)
        if "id=" in t:
            continue  # addressed by id; never touch one
        dm = re.search(r'\bd="([^"]*)"', t)
        if dm and not any(c in "LlHhVvCcSsQqTtAa" for c in dm.group(1)):
            count += 1
    if not count:
        return []
    return [Finding(
        check_id="stray-anchor-point",
        severity="error",
        message=f"{count} stray anchor point(s): <path> with no drawing command",
        detail=("Invisible in Diagrammer — the app strips these at load — but "
                "they survive into the file itself, so every SVG export and "
                "clipboard paste carries them and a vector editor renders each "
                "as a visible stray anchor point."),
        fix_ids=("strip-stray-anchors",),
        data={"count": count},
    )]


def check_mangled_ids(text: str, path: Path,
                      profile: LibraryProfile) -> list[Finding]:
    """An editor that sanitizes colons out of ``id`` erases every port."""
    out: list[Finding] = []
    existing = set(re.findall(r'\sid="([^"]*)"', text))
    for tag in re.finditer(r"<\w+\b[^>]*>", text):
        t = tag.group(0)
        dn = re.search(r'\sdata-name="([^"]*)"', t)
        if dn is None or not dn.group(1).startswith(T.NAMED_PREFIXES):
            continue
        name = dn.group(1)
        cur = re.search(r'\sid="([^"]*)"', t)
        if cur is None or cur.group(1) != name.replace(":", "_"):
            continue
        collides = name in existing
        out.append(Finding(
            check_id="mangled-named-id",
            severity="error",
            message=(f'id="{cur.group(1)}" was sanitized from '
                     f'data-name="{name}"'),
            detail=("_parse_ports and _parse_stretch read only id and require "
                    "the port:/stretch:/label: prefix, so this element is "
                    "invisible to the parser and the port, stretch axis or "
                    "label it defines does not exist."
                    + ("  Cannot be restored automatically: that id is already "
                       "used elsewhere in the file, so two elements would "
                       "collide." if collides else "")),
            element_id=cur.group(1),
            source_span=(tag.start(), tag.end()),
            fix_ids=() if collides else ("restore-named-ids",),
            data={"sanitized": cur.group(1), "original": name},
        ))
    return out


def check_degraded_primitives(text: str, path: Path,
                              profile: LibraryProfile) -> list[Finding]:
    """Ports must be ``<circle>``/``<rect>`` and stretch axes ``<line>``."""
    out: list[Finding] = []
    for group_id, want in (("ports", "circle"), ("stretch", "line")):
        span = T.group_body(text, group_id)
        if span is None:
            continue
        body = text[span[0]:span[1]]
        recoverable = 0
        total = 0
        for tag in re.finditer(r"<path\b[^>]*/>", body):
            dm = re.search(r'\sd="([^"]*)"', tag.group(0))
            if dm is None:
                continue
            total += 1
            got = (T.path_as_circle(dm.group(1)) if want == "circle"
                   else T.path_as_line(dm.group(1)))
            if got is not None:
                recoverable += 1
        if not total:
            continue
        out.append(Finding(
            check_id="primitive-degraded",
            severity="error",
            message=(f'<g id="{group_id}"> holds {total} <path> element(s); '
                     f"only <{want}> is parsed"),
            detail=(f"_parse_{'ports' if group_id == 'ports' else 'stretch'} "
                    f"accepts {'<circle> and <rect>' if group_id == 'ports' else '<line>'}"
                    " only, so these are ignored entirely. An editor converted "
                    "the primitives to path approximations on export."
                    + (f"  {recoverable} of {total} can be converted back."
                       if recoverable else
                       "  None can be converted back automatically — the "
                       "geometry is not a recognisable primitive.")),
            fix_ids=("restore-primitives",) if recoverable else (),
            data={"group": group_id, "total": total, "recoverable": recoverable},
        ))
    return out


def check_duplicate_upright(text: str, path: Path,
                            profile: LibraryProfile) -> list[Finding]:
    """``_apply_upright`` compensates only the *first* ``<g id="upright">``."""
    n = len(re.findall(r'<g\s+id="upright"[^>]*>', text))
    if n < 2:
        return []
    return [Finding(
        check_id="duplicate-upright-group",
        severity="error",
        message=f'{n} <g id="upright"> groups; only the first is compensated',
        detail=("ComponentItem._apply_upright breaks out of its search at the "
                "first match, so every later upright group is treated as plain "
                "artwork and its lettering mirrors on a flipped part. Merge "
                "them into one group — compensation is per child, so one group "
                "handles any number of labels."),
    )]


#: Run order. ``check_parse`` is first because a file the app cannot load makes
#: every other result meaningless; the rest are grouped structure, then ports,
#: then leads, then style, which is also roughly how a reader wants them.
ALL_CHECKS = (
    check_parse,
    check_layer_wrapper,
    check_mangled_ids,
    check_degraded_primitives,
    check_duplicate_upright,
    check_viewbox,
    check_artwork_layer,
    check_ports_exist,
    check_port_on_edge,
    check_lead_endpoints,
    check_lead_width,
    check_lead_linecap,
    check_stray_anchors,
)


def run_checks(text: str, *, path: Path,
               profile: LibraryProfile) -> list[Finding]:
    """Run every check, worst findings first.

    A ``parse-failure`` short-circuits the rest: the remaining checks read the
    same document and would pile unrelated noise on top of the one fault the
    user has to fix first.
    """
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        got = check(text, path, profile)
        findings.extend(got)
        if check is check_parse and got:
            break
    findings = [f for f in findings if f.check_id not in profile.ignored_checks]
    return sorted(findings, key=lambda f: (f.rank, f.check_id, f.message))
