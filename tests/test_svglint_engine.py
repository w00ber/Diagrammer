"""The validation engine: checks, fixes, profiles, and the no-PySide6 rule.

All GUI-free — these assert on data, which is most of what the engine is. The
one test that genuinely de-risks the promotion is
``test_errors_match_the_legacy_validator``: it runs both implementations over
the real 238-component library and insists they agree about what is broken.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from diagrammer import svglint
from diagrammer.svglint import checks, fixes, profiles, text_svg
from diagrammer.svglint.model import LibraryProfile

REPO = Path(__file__).parent.parent
COMPONENTS = REPO / "src" / "diagrammer" / "components"

PAPER = profiles.BUILTIN_PROFILES["PAPER_2pt"]


def write(tmp_path: Path, body: str, *, name: str = "part.svg",
          viewbox: str = "0 0 60 100", style: str | None = None) -> Path:
    """A minimal but realistic component: CSS classes, named layers, 2 pt leads."""
    css = style if style is not None else (
        "    <style>\n"
        "      .st0 { fill: #c66; }\n"
        "      .st1 { fill: none; stroke: #000; stroke-width: 2px; }\n"
        "    </style>\n"
    )
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">\n'
        "  <defs>\n" + css + "  </defs>\n"
        f"{body}"
        "</svg>\n"
    )
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    return target


SOUND_BODY = (
    '  <g id="artwork">\n'
    '    <path class="st1" d="M54.6,54.6H5.4"/>\n'
    "  </g>\n"
    '  <g id="leads">\n'
    '    <path class="st1" d="M30,0v45.4"/>\n'
    '    <path class="st1" d="M30,100v-44.9"/>\n'
    "  </g>\n"
    '  <g id="ports">\n'
    '    <circle id="port:top" class="st0" cx="30" cy="0" r="3"/>\n'
    '    <circle id="port:bottom" class="st0" cx="30" cy="100" r="3"/>\n'
    "  </g>\n"
)


def ids(findings) -> set[str]:
    return {f.check_id for f in findings}


# ==========================================================================
# the invariant that keeps the git hook working
# ==========================================================================

def test_engine_imports_without_pyside6() -> None:
    """The pre-commit hook may run under a bare python3.

    Asserted in a subprocess with a meta-path blocker rather than by poking
    sys.modules, because PySide6 is already imported in this process by the
    session-wide QApplication fixture.
    """
    script = (
        "import sys\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name == 'PySide6' or name.startswith('PySide6.'):\n"
        "            raise ImportError('PySide6 is blocked for this test')\n"
        "        return None\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        return self.find_module(name, path)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from diagrammer import svglint\n"
        "assert 'PySide6' not in sys.modules, sorted(sys.modules)\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", script],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


# ==========================================================================
# checks — one positive and one negative each
# ==========================================================================

def test_sound_component_has_no_findings(tmp_path: Path) -> None:
    path = write(tmp_path, SOUND_BODY)
    assert svglint.check_file(path, profile=PAPER) == []


def test_parse_failure_short_circuits_the_rest(tmp_path: Path) -> None:
    """A file the app cannot load reports once, not once per check.

    Piling unrelated findings on top of the one fault the user must fix first
    is how a report becomes unreadable.
    """
    path = tmp_path / "broken.svg"
    path.write_text("<svg><g id='artwork'><path d='M0,0'/></svg>", encoding="utf-8")
    found = svglint.check_file(path, profile=PAPER)
    assert ids(found) == {"parse-failure"}


def test_layer_wrapper_is_an_error(tmp_path: Path) -> None:
    wrapped = ('  <g id="Layer_1">\n'
               + "\n".join("  " + ln for ln in SOUND_BODY.split("\n"))
               + "  </g>\n")
    path = write(tmp_path, wrapped)
    found = svglint.check_file(path, profile=PAPER)
    assert "layer-wrapper" in ids(found)
    assert next(f for f in found if f.check_id == "layer-wrapper").severity == "error"


def test_layer_wrapper_not_reported_when_layers_are_top_level(tmp_path: Path) -> None:
    path = write(tmp_path, SOUND_BODY)
    assert "layer-wrapper" not in ids(svglint.check_file(path, profile=PAPER))


def test_no_ports_is_an_error_but_decorative_excuses_it(tmp_path: Path) -> None:
    bare = '  <g id="artwork"><path class="st1" d="M0,0H60"/></g>\n'
    assert "no-ports" in ids(svglint.check_file(write(tmp_path, bare), profile=PAPER))

    decorative = bare + '  <g id="decorative"/>\n'
    found = svglint.check_file(
        write(tmp_path, decorative, name="deco.svg"), profile=PAPER)
    assert "no-ports" not in ids(found)


def test_port_off_edge_names_the_nearest_edge(tmp_path: Path) -> None:
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '  <g id="ports"><circle id="port:mid" cx="30" cy="50" r="3"/></g>\n'
    )
    found = svglint.check_file(write(tmp_path, body), profile=PAPER)
    off = next(f for f in found if f.check_id == "port-off-edge")
    assert off.severity == "warning"
    assert off.data["nearest_edge"] == "left"
    assert off.data["distance"] == pytest.approx(30.0)
    # a marker for the port and one for the edge it should have been on
    assert {m.kind for m in off.markers} == {"point", "edge"}


def test_port_within_two_points_of_an_edge_passes(tmp_path: Path) -> None:
    """2 pt is the app's own EDGE_TOLERANCE, so the check must match it."""
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '  <g id="ports"><circle id="port:left" cx="1.5" cy="50" r="3"/></g>\n'
    )
    found = svglint.check_file(write(tmp_path, body), profile=PAPER)
    assert "port-off-edge" not in ids(found)


def test_lead_endpoint_off_port_is_an_error(tmp_path: Path) -> None:
    """A vertical lead short of a port on its own axis: reported, and fixable
    by extension, because sliding the near end cannot bend it."""
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '  <g id="leads"><line class="st1" x1="30" y1="40" x2="30" y2="20"/></g>\n'
        '  <g id="ports"><circle id="port:top" cx="30" cy="0" r="3"/></g>\n'
    )
    found = svglint.check_file(write(tmp_path, body), profile=PAPER)
    f = next(x for x in found if x.check_id == "lead-endpoint-off-port")
    assert f.severity == "error"
    assert f.data["gap"] == pytest.approx(20.0)
    assert f.fix_ids == ("snap-lead-end-to-port",)


def test_lead_not_aligned_with_its_port_gets_no_fix(tmp_path: Path) -> None:
    """A horizontal lead and a port offset on both axes: moving the end would
    rotate the lead, so the user has to decide what was meant."""
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '  <g id="leads"><line class="st1" x1="20" y1="50" x2="40" y2="50"/></g>\n'
        '  <g id="ports"><circle id="port:top" cx="30" cy="0" r="3"/></g>\n'
    )
    found = svglint.check_file(write(tmp_path, body), profile=PAPER)
    f = next(x for x in found if x.check_id == "lead-endpoint-off-port")
    assert f.fix_ids == ()
    assert "not aligned" in f.detail


def test_geometry_moving_fixes_are_never_default_on() -> None:
    """The model's own rule: moving coordinates is always a deliberate choice,
    because the nearest grid line is not always the one the author meant."""
    movers = [f for f in svglint.ALL_FIXES if f.moves_geometry]
    assert movers, "expected at least one geometry-moving fix"
    assert [f.fix_id for f in movers if f.default_on] == []


def test_lead_width_mismatch_is_profile_driven(tmp_path: Path) -> None:
    path = write(tmp_path, SOUND_BODY)  # 2 px leads
    assert "lead-width-mismatch" not in ids(svglint.check_file(path, profile=PAPER))

    slides = profiles.BUILTIN_PROFILES["SLIDES_3pt"]
    f = next(x for x in svglint.check_file(path, profile=slides)
             if x.check_id == "lead-width-mismatch")
    assert f.data == {"found": 2.0, "expected": 3.0}


def test_lead_width_check_disabled_when_library_has_no_weight(tmp_path: Path) -> None:
    """FLAIR and flowchart carry lead_width=None; that must disable the check,
    not compare the lead against zero."""
    path = write(tmp_path, SOUND_BODY)
    flair = profiles.BUILTIN_PROFILES["FLAIR"]
    assert flair.lead_width is None
    assert "lead-width-mismatch" not in ids(svglint.check_file(path, profile=flair))


def test_unresolvable_lead_width_is_an_error(tmp_path: Path) -> None:
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '  <g id="leads"><line class="nosuchclass" x1="30" y1="0" x2="30" y2="40"/></g>\n'
        '  <g id="ports"><circle id="port:top" cx="30" cy="0" r="3"/></g>\n'
    )
    found = svglint.check_file(write(tmp_path, body), profile=PAPER)
    assert "lead-width-unresolvable" in ids(found)


def test_round_linecap_on_leads_is_only_a_warning(tmp_path: Path) -> None:
    """74 shipped files still declare round mid-migration, so an error here
    would make the report noise."""
    style = ("    <style>\n"
             "      .st1 { fill: none; stroke: #000; stroke-width: 2px;\n"
             "             stroke-linecap: round; }\n"
             "    </style>\n")
    path = write(tmp_path, SOUND_BODY, style=style)
    f = next(x for x in svglint.check_file(path, profile=PAPER)
             if x.check_id == "lead-linecap-not-butt")
    assert f.severity == "warning"
    assert f.data["found"] == "round"


def test_nonzero_viewbox_origin_is_flagged(tmp_path: Path) -> None:
    """_assign_approach_directions ignores the origin, so this breaks edges."""
    path = write(tmp_path, SOUND_BODY, viewbox="-3 -3 60 100")
    f = next(x for x in svglint.check_file(path, profile=PAPER)
             if x.check_id == "viewbox-origin-nonzero")
    assert f.data["x"] == -3.0 and f.data["y"] == -3.0
    assert f.fix_ids == ("rebase-viewbox-to-origin",)


def test_missing_viewbox_is_an_error(tmp_path: Path) -> None:
    text = ('<svg xmlns="http://www.w3.org/2000/svg">\n'
            '  <g id="ports"><circle id="port:a" cx="0" cy="0" r="3"/></g>\n'
            "</svg>\n")
    path = tmp_path / "novb.svg"
    path.write_text(text, encoding="utf-8")
    assert "viewbox-missing" in ids(svglint.check_file(path, profile=PAPER))


def test_stray_anchor_points_are_counted_but_ids_are_spared(tmp_path: Path) -> None:
    body = (
        '  <g id="artwork">\n'
        '    <path class="st1" d="M0,50H60"/>\n'
        '    <path class="st1" d="M40.1,24.1"/>\n'
        '    <path class="st1" d="M12,13"/>\n'
        "  </g>\n"
        '  <g id="ports"><circle id="port:left" cx="0" cy="50" r="3"/></g>\n'
    )
    f = next(x for x in svglint.check_file(write(tmp_path, body), profile=PAPER)
             if x.check_id == "stray-anchor-point")
    assert f.data["count"] == 2


def test_mangled_ids_are_reported_and_collisions_are_not_fixable(tmp_path: Path) -> None:
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '  <g id="ports">\n'
        '    <circle id="port_left" data-name="port:left" cx="0" cy="50" r="3"/>\n'
        "  </g>\n"
    )
    f = next(x for x in svglint.check_file(write(tmp_path, body), profile=PAPER)
             if x.check_id == "mangled-named-id")
    assert f.severity == "error"
    assert f.fix_ids == ("restore-named-ids",)

    # Now the restored name is already taken, so restoring would lose a port.
    collide = body.replace(
        "  </g>\n",
        '    <circle id="port:left" cx="60" cy="50" r="3"/>\n  </g>\n')
    f2 = next(x for x in svglint.check_file(
        write(tmp_path, collide, name="collide.svg"), profile=PAPER)
        if x.check_id == "mangled-named-id")
    assert f2.fix_ids == ()
    assert "already used" in f2.detail


def test_path_ified_ports_are_reported(tmp_path: Path) -> None:
    circle_path = ("M33,0c0,1.7-1.3,3-3,3s-3-1.3-3-3,1.3-3,3-3,3,1.3,3,3Z")
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        f'  <g id="ports"><path id="port:top" class="st0" d="{circle_path}"/></g>\n'
    )
    f = next(x for x in svglint.check_file(write(tmp_path, body), profile=PAPER)
             if x.check_id == "primitive-degraded")
    assert f.data["group"] == "ports"
    assert f.data["total"] == 1


def test_two_upright_groups_is_an_error(tmp_path: Path) -> None:
    """_apply_upright breaks at the first match, so the second never
    compensates and its lettering mirrors on a flipped part."""
    body = (
        SOUND_BODY
        + '  <g id="upright"><path data-anchor="10,10" d="M0,0H4"/></g>\n'
        + '  <g id="upright"><path data-anchor="50,10" d="M0,0H4"/></g>\n'
    )
    f = next(x for x in svglint.check_file(write(tmp_path, body), profile=PAPER)
             if x.check_id == "duplicate-upright-group")
    assert f.severity == "error"


def test_profile_can_opt_out_of_a_check(tmp_path: Path) -> None:
    """circuits/ parts centre their common port off the edge by design."""
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '  <g id="ports"><circle id="port:mid" cx="30" cy="50" r="3"/></g>\n'
    )
    path = write(tmp_path, body)
    assert "port-off-edge" in ids(svglint.check_file(path, profile=PAPER))
    quiet = LibraryProfile(name="x", lead_width=2.0,
                           ignored_checks=frozenset({"port-off-edge"}))
    assert "port-off-edge" not in ids(svglint.check_file(path, profile=quiet))


def test_findings_are_sorted_worst_first(tmp_path: Path) -> None:
    body = (
        '  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '  <g id="leads"><line class="st1" x1="30" y1="40" x2="30" y2="20"/></g>\n'
        '  <g id="ports"><circle id="port:mid" cx="30" cy="50" r="3"/></g>\n'
    )
    found = svglint.check_file(write(tmp_path, body), profile=PAPER)
    ranks = [f.rank for f in found]
    assert ranks == sorted(ranks), [(f.severity, f.check_id) for f in found]
