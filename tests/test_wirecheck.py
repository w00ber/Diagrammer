"""Wiring checks (issue #47, stage 1): per-port lead styles, the engine, and
wire creation taking the lead it actually continues."""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from diagrammer import wirecheck
from diagrammer.models.component_def import ComponentDef, PortDef
from diagrammer.models.lead_style import LeadStyle, assign_lead_styles
from diagrammer.wirecheck import End, Fix, Snapshot, Wire

REPO = Path(__file__).parent.parent
COMPONENTS = REPO / "src" / "diagrammer" / "components"

L2 = LeadStyle(2.0, "#000000")
L3 = LeadStyle(3.0, "#000000")
NEAR_BLACK = LeadStyle(2.0, "#231f20")
RED = LeadStyle(2.0, "#cc3333")


def part(ref, lead, port="p"):
    return End("part", ref, port, lead, f"{ref}:{port}")


def node(ref):
    return End("node", ref)


def snap(*wires):
    return Snapshot({w.id: w for w in wires})


def ids(findings):
    return [f.check_id for f in findings]


# ==========================================================================
# the lead reader
# ==========================================================================

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 100">
  <defs><style>
    .st1 { fill: none; stroke: #000; stroke-width: 2px; }
    .thick { stroke-width: 3px; }
  </style></defs>
  <g id="artwork"><path class="st1" d="M10,50H50"/></g>
  <g id="leads">
    <g id="leads1"><g id="leads2">
      <path class="st1 thick" d="M30,0v45"/>
    </g></g>
    <line style="stroke:#cc3333;stroke-width:1.5" x1="30" y1="100" x2="30" y2="55"/>
  </g>
  <g id="ports">
    <circle id="port:top" cx="30" cy="0" r="3"/>
    <circle id="port:bottom" cx="30" cy="100" r="3"/>
    <circle id="port:side" cx="60" cy="50" r="3"/>
  </g>
</svg>"""


def test_each_port_gets_the_lead_that_ends_on_it() -> None:
    root = ET.fromstring(SVG)
    ports = [PortDef("top", 30, 0), PortDef("bottom", 30, 100),
             PortDef("side", 60, 50)]
    fallback = assign_lead_styles(root, ports)
    top, bottom, side = ports
    # Nested two groups deep, two classes (the later rule wins), relative path.
    assert (top.lead_width, top.lead_color) == (3.0, "#000000")
    # Inline style beats everything.
    assert (bottom.lead_width, bottom.lead_color) == (1.5, "#cc3333")
    # No lead ends here.
    assert (side.lead_width, side.lead_color) == (None, None)
    assert fallback == LeadStyle(3.0, "#000000")


def test_shipped_leads_match_their_library_weight() -> None:
    """Every port of every weight library reads its house lead width."""
    from diagrammer.models.library import ComponentLibrary
    lib = ComponentLibrary()
    lib.scan(COMPONENTS)
    weight = {"PAPER_2pt": 2.0, "SLIDES_3pt": 3.0, "CRYO_WIRING_6pt": 6.0}
    seen = 0
    for key in lib._by_key:
        top = key.split("/", 1)[0]
        if top not in weight:
            continue
        for p in lib.get(key).ports:
            if p.lead_width is not None:
                seen += 1
                assert p.lead_width == weight[top], (key, p.name)
    assert seen > 600


def test_nested_illustrator_leads_are_found() -> None:
    """The circulator keeps a lead inside leads1/leads2; a regex reader
    stopping at the first </g> missed it."""
    cdef = ComponentDef.from_svg(
        COMPONENTS / "SLIDES_3pt/circuits/µwave/PASSIVE/CIRC2.svg")
    assert {p.name: p.lead_width for p in cdef.ports} == \
        {"1": 3.0, "2": 3.0, "3": 3.0}


# ==========================================================================
# the engine
# ==========================================================================

def test_a_wire_matching_its_leads_is_clean() -> None:
    s = snap(Wire("w", part("A", L2), part("B", L2), 2.0, "#000000"))
    assert wirecheck.check(s) == []


def test_wire_width_off_its_lead_is_flagged_and_fixable() -> None:
    s = snap(Wire("w", part("A", L3), part("B", L3), 2.0, "#000000"))
    [f] = wirecheck.check(s)
    assert f.check_id == "wire-width" and f.data["expected"] == 3.0
    [fix] = f.fixes
    assert wirecheck.verified(s, f, fix)
    assert wirecheck.check(s.apply(fix)) == []


def test_leads_that_disagree_offer_a_decision_for_each_side() -> None:
    s = snap(Wire("w", part("CAP", L2), part("IND", L3), 2.0, "#000000"))
    [f] = wirecheck.check(s)
    assert f.check_id == "leads-disagree"
    assert {fx.label for fx in f.fixes} == {"Use 2 pt", "Use 3 pt"}
    for fx in f.fixes:
        assert wirecheck.verified(s, f, fx)
        after = s.apply(fx)
        assert wirecheck.check(after) == []
        assert after.wires["w"].deliberate


def test_a_net_through_junctions_takes_its_part_leads() -> None:
    s = snap(
        Wire("a", part("A", L3), node("j1"), 3.0, "#000000"),
        Wire("mid", node("j1"), node("j2"), 2.0, "#000000"),   # odd one out
        Wire("b", node("j2"), part("B", L3), 3.0, "#000000"),
    )
    [f] = wirecheck.check(s)
    assert f.check_id == "wire-width" and f.wire_ids == ("mid",)


def test_disagreeing_leads_across_a_net_are_one_finding() -> None:
    s = snap(
        Wire("a", part("A", L2), node("j"), 2.0, "#000000"),
        Wire("b", node("j"), part("B", L3), 3.0, "#000000"),
        Wire("c", node("j"), part("C", L3), 3.0, "#000000"),
    )
    [f] = wirecheck.check(s)
    assert f.check_id == "leads-disagree"
    assert set(f.wire_ids) == {"a", "b", "c"}
    assert [fx.label for fx in f.fixes] == ["Use 3 pt", "Use 2 pt"]  # majority first


def test_junction_only_runs_are_checked_against_each_other() -> None:
    s = snap(Wire("x", node("j1"), node("j2"), 2.0, "#000000"),
             Wire("y", node("j2"), node("j3"), 3.0, "#000000"))
    [f] = wirecheck.check(s)
    assert f.check_id == "net-mixed-width"


def test_deliberate_wires_are_never_flagged() -> None:
    s = snap(Wire("w", part("A", L3), part("B", L3), 5.0, "#000000",
                  deliberate=True))
    assert wirecheck.check(s) == []


def test_illustrator_near_black_counts_as_black() -> None:
    s = snap(Wire("w", part("A", NEAR_BLACK), part("B", L2), 2.0, "#000000"))
    assert wirecheck.check(s) == []


def test_a_coloured_lead_is_followed() -> None:
    s = snap(Wire("w", part("A", RED), part("B", RED), 2.0, "#000000"))
    [f] = wirecheck.check(s)
    assert f.check_id == "wire-color"
    assert f.fixes[0].changes == {"w": {"color": "#cc3333"}}


def test_a_fix_that_disturbs_a_neighbour_is_not_verified() -> None:
    s = snap(Wire("a", part("A", L3), part("B", L3), 2.0, "#000000"),
             Wire("b", part("C", L3), part("D", L3), 3.0, "#000000"))
    [f] = wirecheck.check(s)
    sloppy = Fix("also b", {"a": {"width": 3.0}, "b": {"width": 2.0}})
    assert not wirecheck.verified(s, f, sloppy)
    assert wirecheck.verified(s, f, f.fixes[0])


def test_engine_imports_without_pyside6() -> None:
    """The checks must run where Qt cannot: a hook, a bare python3."""
    script = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'PySide6' or name.startswith('PySide6.'):\n"
        "            raise ImportError('blocked')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from diagrammer import wirecheck\n"
        "from diagrammer.models.lead_style import assign_lead_styles\n"
        "assert 'PySide6' not in sys.modules\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", script],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


# ==========================================================================
# wire creation and a live scene
# ==========================================================================

@pytest.fixture
def d():
    from diagrammer.headless import Diagram, load_library
    lib = load_library(include_overlay=False, include_custom=False)
    return Diagram(library=lib)


def test_a_wire_drawn_from_a_junction_takes_the_parts_lead(d) -> None:
    """The command read only the source end, so a wire started on a junction
    got the global default (3 px) even with a 2 px lead at its other end.
    A PAPER part on purpose: a SLIDES lead equals the default and would pass
    by coincidence."""
    from diagrammer.commands.connect_command import CreateConnectionCommand
    from diagrammer.panels.settings_dialog import app_settings
    assert app_settings.default_line_width != 2.0
    d.add("PAPER_2pt/circuits/LCR/CAP1", (0, 0), anchor="top", id="C")
    d.node((0, -80), id="N")
    cmd = CreateConnectionCommand(d.scene, d._port("N", "center"),
                                  d._port("C", "top"))
    d.undo_stack.push(cmd)
    assert cmd.connection.line_width == 2.0


def test_snapshot_of_a_mixed_library_diagram(d) -> None:
    from diagrammer.wirecheck.scene import snapshot
    d.add("PAPER_2pt/circuits/LCR/CAP1", (0, 0), anchor="top", id="C")
    d.attach("SLIDES_3pt/circuits/LCR/IND1", "top", to=("C", "bottom"),
             direction="down", id="L")
    findings = wirecheck.check(snapshot(d.scene))
    assert ids(findings) == ["leads-disagree"]
    assert "CAP1:bottom 2 pt" in findings[0].message
    assert "IND1:top 3 pt" in findings[0].message


def test_a_regular_diagram_is_clean(d) -> None:
    from diagrammer.wirecheck.scene import snapshot
    d.load(REPO / "src/diagrammer/examples/JPA-setup.dgm")
    assert wirecheck.check(snapshot(d.scene)) == []
