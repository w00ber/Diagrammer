"""Convert to Library (issue #47, stage 4)."""

from __future__ import annotations

import base64
import json

import pytest

TEE0 = "PAPER_2pt/circuits/µwave/PASSIVE/BIAS_TEE0"
TEE1 = "PAPER_2pt/circuits/µwave/PASSIVE/BIAS_TEE1"
PAPER_CAP = "PAPER_2pt/circuits/LCR/CAP1"


@pytest.fixture
def d(isolate_app_settings):
    from diagrammer.headless import Diagram, load_library
    lib = load_library(include_overlay=False, include_custom=False)
    return Diagram(library=lib)


def _wired_ports(diagram):
    """{library key: sorted port names with a wire on them}, parts only."""
    from diagrammer.items.connection_item import ConnectionItem
    out: dict[str, list] = {}
    for c in diagram.scene.items():
        if isinstance(c, ConnectionItem):
            for p in (c.source_port, c.target_port):
                key = getattr(p.component, "library_key", None)
                if key:
                    out.setdefault(key, []).append(p.port_name)
    return {k: sorted(v) for k, v in out.items()}


# ==========================================================================
# 1. bias-tee port names
# ==========================================================================

def test_bias_tees_use_their_twins_port_names(d):
    for key, names in ((TEE0, {"RF", "COM", "DC"}), (TEE1, {"RF", "COM", "DC"})):
        paper = d.library.get(key)
        slides = d.library.get(key.replace("PAPER_2pt", "SLIDES_3pt"))
        assert {p.name for p in paper.ports} == names
        assert {(p.name, p.x, p.y) for p in paper.ports} == \
            {(p.name, p.x, p.y) for p in slides.ports}


OLD = {TEE0: {"RF": "top", "COM": "bottom", "DC": "dc"},
       TEE1: {"RF": "top", "COM": "bottom", "DC": "left"}}


def _old_file(d, tmp_path, *, embed: bool):
    """A .dgm as it was saved before the rename: old port names on the
    wires, and (with *embed*) the old SVG embedded."""
    d.add(TEE0, (0, 0), id="T0")
    d.add(TEE1, (400, 0), id="T1")
    for i, (tee, ports) in enumerate((("T0", ("RF", "COM", "DC")),
                                      ("T1", ("RF", "COM", "DC")))):
        for j, port in enumerate(ports):
            nid = d.node((i * 400 - 100 + j * 50, 300), id=f"N{i}{j}")
            d.connect(tee, port, nid, "center")
    path = tmp_path / "new.dgm"
    d.save(path)
    data = json.loads(path.read_text())
    keys = {c["id"]: c["def_key"] for c in data["components"]}
    for conn in data["connections"]:
        for end in ("source", "target"):
            key = keys.get(conn[end]["component_id"])
            if key in OLD:
                conn[end]["port_name"] = OLD[key][conn[end]["port_name"]]
    for comp in data["components"]:
        if not embed:
            comp.pop("embedded_svg", None)
            continue
        svg = base64.b64decode(comp["embedded_svg"]).decode()
        for new, old in OLD[comp["def_key"]].items():
            svg = svg.replace(f'id="port:{new}"', f'id="port:{old}"')
        comp["embedded_svg"] = base64.b64encode(svg.encode()).decode()
    old = tmp_path / "old.dgm"
    old.write_text(json.dumps(data))
    return old


@pytest.mark.parametrize("embed", [True, False], ids=["embedded", "bare"])
def test_old_bias_tee_names_migrate_on_load(d, tmp_path, embed):
    from diagrammer.headless import Diagram
    old = _old_file(d, tmp_path, embed=embed)
    text = old.read_text()
    assert '"port_name": "top"' in text and '"port_name": "RF"' not in text
    fresh = Diagram(library=d.library)
    fresh.load(old)
    assert _wired_ports(fresh) == {TEE0: ["COM", "DC", "RF"],
                                   TEE1: ["COM", "DC", "RF"]}


# ==========================================================================
# 2. the cross-library twin check
# ==========================================================================

from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
COMPONENTS = REPO / "src" / "diagrammer" / "components"


def test_shipped_twins_agree():
    from collections import Counter

    from diagrammer.svglint.twins import check_twins
    got = check_twins(COMPONENTS)
    assert [t for t in got if t.finding.severity == "error"] == []
    no_twin = Counter(t.path.relative_to(COMPONENTS).parts[0] for t in got
                      if t.finding.check_id == "no-twin")
    assert no_twin == {"PAPER_2pt": 4, "SLIDES_3pt": 1}
    names = sorted(t.path.stem for t in got)
    assert names == ["COAX1_FAT_LCUT", "COAX1_FAT_RCUT", "COAX1_MED_LCUT",
                     "COAX1_MED_RCUT", "COAX_ELBOW1_MED"]


def _pair(tmp_path, paper_rel, slides_rel, edit):
    """Copy a shipped twin pair into a scratch components tree, editing
    the SLIDES file's text with *edit*."""
    src_p = COMPONENTS / "PAPER_2pt" / paper_rel
    src_s = COMPONENTS / "SLIDES_3pt" / slides_rel
    dst_p = tmp_path / "PAPER_2pt" / paper_rel
    dst_s = tmp_path / "SLIDES_3pt" / slides_rel
    for a, b, f in ((src_p, dst_p, lambda t: t), (src_s, dst_s, edit)):
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text(f(a.read_text(encoding="utf-8")), encoding="utf-8")
    return dst_p


def test_twin_check_catches_renamed_ports(tmp_path):
    from diagrammer.svglint.twins import check_twins
    tee = "circuits/µwave/PASSIVE/BIAS_TEE0.svg"
    paper = _pair(tmp_path, tee, tee,
                  lambda t: t.replace('id="port:RF"', 'id="port:top"'))
    (f,) = check_twins(tmp_path)
    assert f.finding.check_id == "twin-port-names"
    assert f.finding.severity == "error"
    assert f.path == paper
    assert "RF" in f.finding.message and "top" in f.finding.message


def test_twin_check_catches_moved_ports_across_folder_drift(tmp_path):
    from diagrammer.svglint.twins import check_twins
    # Splitters sit in different folders in the two libraries.
    paper = _pair(tmp_path, "circuits/µwave/splitters/SPLITTER1x2.svg",
                  "circuits/splitters/SPLITTER1x2.svg",
                  lambda t: t.replace('cx="80" cy="30"', 'cx="80" cy="35"'))
    got = check_twins(tmp_path)
    assert [t.finding.check_id for t in got] == ["twin-port-positions"]
    assert got[0].path == paper


def test_find_twin_matches_like_the_library():
    from diagrammer.svglint.twins import find_twin
    others = ["circuits/splitters/SPLITTER1x2", "a/X", "b/X", "c/Lcr/CAP1"]
    assert find_twin("c/LCR/CAP1", others) == "c/Lcr/CAP1"      # folder case
    assert find_twin("circuits/µwave/splitters/SPLITTER1x2", others) == \
        "circuits/splitters/SPLITTER1x2"                          # unique name
    assert find_twin("z/X", others) is None                       # ambiguous


def test_twin_check_runs_without_pyside6():
    import subprocess
    import sys
    script = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'PySide6' or name.startswith('PySide6.'):\n"
        "            raise ImportError('PySide6 is blocked for this test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from pathlib import Path\n"
        "from diagrammer.svglint.twins import check_twins\n"
        f"r = check_twins(Path({str(COMPONENTS)!r}))\n"
        "assert 'PySide6' not in sys.modules\n"
        "print(len(r))\n"
    )
    out = subprocess.run([sys.executable, "-c", script],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "5"


# ==========================================================================
# 3. single-part swap
# ==========================================================================

SLIDES_IND = "SLIDES_3pt/circuits/LCR/IND1"
PAPER_IND = "PAPER_2pt/circuits/LCR/IND1"


def _part(diagram, cid):
    return diagram._item(cid)


def _ends(conn):
    return (conn.source_port.scene_center().toTuple(),
            conn.target_port.scene_center().toTuple())


def test_swap_keeps_the_instance_and_moves_each_wire(d):
    from diagrammer.wirecheck.scene import swap_to_twin
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    d.add(SLIDES_IND, (0, 200), anchor="top", id="B")
    d.add(PAPER_IND, (300, 0), anchor="top", id="C")
    w1 = d._items[d.connect("A", "bottom", "B", "top")]
    w2 = d._items[d.connect("B", "bottom", "C", "top")]
    b = _part(d, "B")
    b.rotate_by(90)
    b.set_flip_h(True)
    b._layer_index = 0
    b._group_ids, b._group_id = ["g1"], "g1"
    d.scene.update_connections()
    before = {"pos": b.pos().toTuple(), "rot": b.rotation_angle,
              "flip": (b.flip_h, b.flip_v), "id": b.instance_id,
              "stretch": (b.stretch_dx, b.stretch_dy), "z": b.zValue(),
              "w1": _ends(w1), "w2": _ends(w2)}
    n = d.undo_stack.count()

    assert swap_to_twin(d.scene, b)
    assert d.undo_stack.count() == n + 1                 # one undo step
    new = _part(d, "B")
    assert new is not b and b.scene() is None
    assert new.library_key == PAPER_IND
    assert {"pos": new.pos().toTuple(), "rot": new.rotation_angle,
            "flip": (new.flip_h, new.flip_v), "id": new.instance_id,
            "stretch": (new.stretch_dx, new.stretch_dy), "z": new.zValue(),
            "w1": _ends(w1), "w2": _ends(w2)} == before
    assert new._group_ids == ["g1"]
    assert w1.target_port.component is new and w1.target_port.port_name == "top"
    assert w2.source_port.component is new and w2.source_port.port_name == "bottom"
    assert d.scene.connections_on_port(new.port_by_name("top")) == [w1]
    assert d.scene.connections_on_port(b.port_by_name("top")) == []
    assert w1.line_width == 2.0 and w2.line_width == 2.0

    d.undo_stack.undo()
    assert b.scene() is d.scene and new.scene() is None
    assert w1.target_port.component is b
    assert d.scene.connections_on_port(b.port_by_name("top")) == [w1]
    assert _ends(w1) == before["w1"]
    d.undo_stack.redo()
    assert w1.target_port.component is new


def test_swap_restyles_the_wires_that_follow_its_leads(d):
    from diagrammer.commands.style_command import ChangeStyleCommand
    from diagrammer.wirecheck.scene import swap_to_twin
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    d.add(PAPER_IND, (0, 200), anchor="top", id="B")
    w = d._items[d.connect("A", "bottom", "B", "top")]
    keep = d._items[d.connect("A", "top", "B", "bottom")]
    d.undo_stack.push(ChangeStyleCommand(keep, "style_deliberate", False, True))
    assert w.line_width == 2.0
    swap_to_twin(d.scene, _part(d, "A"))
    # A 3 pt lead now meets a 2 pt one: the wire is left for the user.
    assert w.line_width == 2.0
    swap_to_twin(d.scene, _part(d, "B"))
    assert w.line_width == 3.0                           # both 3 pt: follows
    assert keep.line_width == 2.0                        # kept on purpose


def test_swap_survives_save_and_load(d, tmp_path):
    from diagrammer.headless import Diagram
    from diagrammer.wirecheck.scene import swap_to_twin
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    d.add(SLIDES_IND, (0, 200), anchor="top", id="B")
    d.connect("A", "bottom", "B", "top")
    swap_to_twin(d.scene, _part(d, "B"))
    d.save(tmp_path / "s.dgm")
    fresh = Diagram(library=d.library)
    fresh.load(tmp_path / "s.dgm")
    assert _wired_ports(fresh) == {PAPER_CAP: ["bottom"], PAPER_IND: ["top"]}


def test_swap_is_refused_when_a_wired_port_is_missing(d):
    from diagrammer.commands.swap_command import SwapComponentCommand
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    d.add(PAPER_IND, (0, 200), anchor="top", id="B")
    d.connect("A", "bottom", "B", "top")
    a = _part(d, "A")
    cmd = SwapComponentCommand(d.scene, a, d.library.get(TEE0))
    assert not cmd.ok
    d.undo_stack.push(cmd)
    assert a.scene() is d.scene


def test_a_part_without_a_twin_cannot_swap(d):
    from diagrammer.wirecheck.scene import swap_to_twin
    key = "PAPER_2pt/circuits/µwave/coax and twp/COAX1_FAT_LCUT"
    d.add(key, (0, 0), id="X")
    n = d.undo_stack.count()
    assert not swap_to_twin(d.scene, _part(d, "X"))
    assert d.undo_stack.count() == n


def test_context_menu_offers_the_swap(d):
    from PySide6.QtWidgets import QMenu

    from diagrammer.canvas.view import DiagramView
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    d.add("PAPER_2pt/circuits/µwave/coax and twp/COAX1_FAT_LCUT", (300, 0),
          id="X")
    view = DiagramView(d.scene)
    menu = QMenu()
    view._add_swap_action(menu, _part(d, "A"))
    (act,) = [a for a in menu.actions() if not a.isSeparator()]
    assert act.text() == "Swap to SLIDES_3pt Twin" and act.isEnabled()
    act.trigger()
    assert _part(d, "A").library_key == "SLIDES_3pt/circuits/LCR/CAP1"
    menu = QMenu()
    view._add_swap_action(menu, _part(d, "X"))
    (act,) = [a for a in menu.actions() if not a.isSeparator()]
    assert not act.isEnabled()


def _disagreement(d):
    d.add(SLIDES_IND, (0, 0), anchor="top", id="B")          # the odd one
    d.add(PAPER_CAP, (0, 200), anchor="top", id="A")
    d.add(PAPER_IND, (300, 0), anchor="top", id="C")
    return d._items[d.connect("B", "bottom", "A", "top")]


def test_notice_offers_swap_first(d):
    from PySide6.QtWidgets import QPushButton

    from diagrammer.canvas.view import DiagramView
    from diagrammer.canvas.wiring_notice import show_wiring_notice
    from diagrammer.wirecheck.scene import disagreements_at
    w = _disagreement(d)
    view = DiagramView(d.scene)
    view.resize(800, 600)
    found = disagreements_at(d.scene, [w.source_port])
    notice = show_wiring_notice(view, found, QPointF_(0, 100))
    labels = [b.text() for b in notice.findChildren(QPushButton)]
    assert labels[0] == "Swap IND1 to PAPER_2pt"       # matches the diagram
    assert labels[1] == "Swap CAP1 to SLIDES_3pt"
    assert labels[-3:] == ["Use 2 pt", "Use 3 pt", "Ignore"]
    first = notice.findChildren(QPushButton)[0]
    assert "bold" in first.styleSheet()
    n = d.undo_stack.count()
    first.click()
    assert d.undo_stack.count() == n + 1
    assert _part(d, "B").library_key == PAPER_IND
    assert w.line_width == 2.0 and not w.style_deliberate


def QPointF_(x, y):
    from PySide6.QtCore import QPointF
    return QPointF(x, y)


def test_check_wiring_panel_offers_and_accepts_swap(d):
    from diagrammer.canvas.view import DiagramView
    from diagrammer.panels.wiring_check import WiringCheckPanel
    _disagreement(d)
    view = DiagramView(d.scene)
    p = WiringCheckPanel()
    p.set_document(d.scene, view)
    p.recheck()
    ((f, _rej),) = p.findings()
    assert [fx.kind for fx in f.fixes] == ["swap", "swap", "lead", "lead",
                                          "wire", "wire"]
    assert f.fixes[0].label == "Swap IND1 to PAPER_2pt"
    p._apply(f.fixes[0], "Wiring: " + f.fixes[0].label)
    p.recheck()
    assert p.findings() == []
    assert _part(d, "B").library_key == PAPER_IND
    p.deleteLater()


# ==========================================================================
# 4. per-instance lead override
# ==========================================================================

def test_lead_paths_are_read_per_port(d):
    cap = d.library.get(PAPER_CAP)
    assert {p.name: p.lead_paths for p in cap.ports} == {
        "top": ("leads/0",), "bottom": ("leads/1",)}
    tee = d.library.get(TEE0)                 # Illustrator's nested sub-group
    assert dict((p.name, p.lead_paths) for p in tee.ports)["DC"] == ("leads/2/0",)


def test_override_changes_one_port_and_never_the_file(d):
    from diagrammer.commands.connect_command import lead_style_at
    from diagrammer.commands.lead_override import (
        clear_lead_override,
        lead_override_at,
        set_lead_override,
    )
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    a = _part(d, "A")
    path = a.component_def.svg_path
    before_file = path.read_bytes()
    n = d.undo_stack.count()
    assert set_lead_override(d.undo_stack, a, "bottom", width=3.0,
                             color="#cc3333")
    assert d.undo_stack.count() == n + 1                       # one step
    assert lead_override_at(a, "bottom") == {"width": 3.0, "color": "#cc3333"}
    style = lead_style_at(a.port_by_name("bottom"))
    assert (style.width, style.color) == (3.0, "#cc3333")
    other = lead_style_at(a.port_by_name("top"))
    assert (other.width, other.color) == (2.0, "#000000")
    # Drawn that way, too.
    svg = a._prepare_svg_bytes(path, style_overrides=a.style_overrides).decode()
    assert "stroke-width:3.0px" in svg and "#cc3333" in svg
    assert path.read_bytes() == before_file
    # A new wire from that port takes the overridden lead.
    d.add(PAPER_IND, (0, 200), anchor="top", id="B")
    w = d._items[d.connect("A", "bottom", "B", "top")]
    assert w.line_width == 3.0
    clear_lead_override(d.undo_stack, a, "bottom")
    assert lead_override_at(a, "bottom") == {}
    d.undo_stack.undo()
    assert lead_override_at(a, "bottom")["width"] == 3.0


def test_override_survives_save_load_paste_and_duplicate(d, tmp_path):
    from diagrammer.canvas.view import DiagramView
    from diagrammer.clipboard_ops import ClipboardMixin
    from diagrammer.commands.lead_override import (
        lead_override_at,
        set_lead_override,
    )
    from diagrammer.headless import Diagram
    from diagrammer.items.component_item import ComponentItem
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    a = _part(d, "A")
    set_lead_override(d.undo_stack, a, "top", width=4.0)

    d.save(tmp_path / "o.dgm")
    fresh = Diagram(library=d.library)
    fresh.load(tmp_path / "o.dgm")
    (b,) = [i for i in fresh.scene.items() if isinstance(i, ComponentItem)]
    assert lead_override_at(b, "top") == {"width": 4.0}

    class Host(ClipboardMixin):
        def __init__(self, scene, library):
            self._scene, self._view, self._library = scene, None, library
            self._clipboard = []

        def _try_paste_external_svg(self):
            return False

    host = Host(d.scene, d.library)
    a.setSelected(True)
    host._copy()
    d.scene.clearSelection()
    host._paste()
    pasted = [i for i in d.scene.items()
              if isinstance(i, ComponentItem) and i is not a]
    assert [lead_override_at(p, "top") for p in pasted] == [{"width": 4.0}]

    view = DiagramView(d.scene)
    clone = view._duplicate_item(a)
    assert lead_override_at(clone, "top") == {"width": 4.0}


def test_properties_shows_and_reverses_the_override(d):
    from PySide6.QtWidgets import QDoubleSpinBox, QLabel, QPushButton

    from diagrammer.commands.lead_override import (
        lead_override_at,
        set_lead_override,
    )
    from diagrammer.panels.properties_panel import PropertiesPanel
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    a = _part(d, "A")
    set_lead_override(d.undo_stack, a, "bottom", width=3.0)
    a.setSelected(True)
    panel = PropertiesPanel()
    panel.update_for_selection(d.scene)
    labels = [lb.text() for lb in panel.findChildren(QLabel)]
    assert "<b>Leads</b>" in labels
    assert "top" in labels and any(t.startswith("<b>bottom</b>") for t in labels)
    spins = [s.value() for s in panel.findChildren(QDoubleSpinBox)]
    assert 3.0 in spins and 2.0 in spins
    resets = [b for b in panel.findChildren(QPushButton) if b.text() == "Reset"]
    assert [b.isEnabled() for b in resets] == [False, True]
    resets[1].click()
    assert lead_override_at(a, "bottom") == {}
    panel.deleteLater()


def test_match_this_parts_lead_is_offered_between_swap_and_wire(d):
    from diagrammer import wirecheck
    from diagrammer.commands.lead_override import lead_override_at
    from diagrammer.wirecheck.scene import apply_fix, ordered_fixes, snapshot
    w = _disagreement(d)
    snap = snapshot(d.scene)
    (f,) = wirecheck.check(snap)
    fixes = ordered_fixes(d.scene, f, [fx for fx in f.fixes
                                       if wirecheck.verified(snap, f, fx)])
    assert [fx.label for fx in fixes] == [
        "Swap IND1 to PAPER_2pt", "Swap CAP1 to SLIDES_3pt",
        "Set IND1:bottom lead to 2 pt", "Set CAP1:top lead to 3 pt",
        "Use 2 pt", "Use 3 pt"]
    lead = fixes[2]
    apply_fix(d.scene, lead)
    assert lead_override_at(_part(d, "B"), "bottom") == {"width": 2.0}
    assert _part(d, "B").library_key == SLIDES_IND             # not swapped
    assert w.line_width == 2.0 and not w.style_deliberate
    assert wirecheck.check(snapshot(d.scene)) == []


def test_a_wire_set_by_hand_can_take_the_lead_with_it(d):
    from diagrammer import wirecheck
    from diagrammer.commands.style_command import ChangeStyleCommand
    from diagrammer.wirecheck.scene import apply_fix, snapshot
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    d.add(PAPER_IND, (0, 200), anchor="top", id="B")
    w = d._items[d.connect("A", "bottom", "B", "top")]
    d.undo_stack.push(ChangeStyleCommand(w, "line_width", 2.0, 3.0))
    snap = snapshot(d.scene)
    (f,) = wirecheck.check(snap)
    assert f.check_id == "wire-width"
    kinds = {fx.kind: fx for fx in f.fixes if wirecheck.verified(snap, f, fx)}
    assert set(kinds) == {"swap", "lead", "wire"}
    assert kinds["swap"].label == "Swap CAP1, IND1 to SLIDES_3pt"
    apply_fix(d.scene, kinds["lead"])
    assert wirecheck.check(snapshot(d.scene)) == []
    assert w.line_width == 3.0
