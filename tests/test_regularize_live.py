"""Regularize Wiring mode (issue #47, stage 2): what happens as you wire."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor

PAPER_CAP = "PAPER_2pt/circuits/LCR/CAP1"
SLIDES_IND = "SLIDES_3pt/circuits/LCR/IND1"
PAPER_IND = "PAPER_2pt/circuits/LCR/IND1"


@pytest.fixture
def d(isolate_app_settings):
    from diagrammer.headless import Diagram, load_library
    lib = load_library(include_overlay=False, include_custom=False)
    # A default colour that no lead uses, so "took the lead's colour" shows.
    isolate_app_settings.default_line_color = QColor("#123456")
    return Diagram(library=lib)


def draw(d, a, a_port, b, b_port):
    """Wire it the way a mouse gesture does: begin on one port, finish on
    the other through finish_connection_with_vertices."""
    scene = d.scene
    scene.begin_connection(d._port(a, a_port))
    scene._current_target_port = d._port(b, b_port)
    assert scene.finish_connection_with_vertices([])
    return [c for c in scene.connections_on_port(d._port(b, b_port))][-1]


def two_parts(d, top_key, bottom_key):
    d.add(top_key, (0, 0), anchor="top", id="A")
    d.add(bottom_key, (0, 200), anchor="top", id="B")


# ==========================================================================
# drawing
# ==========================================================================

def test_new_wire_takes_its_leads_width_and_colour(d):
    two_parts(d, PAPER_CAP, PAPER_IND)
    wire = draw(d, "A", "bottom", "B", "top")
    assert wire.line_width == 2.0
    assert wire.line_color.name() == "#000000"


def test_mode_off_keeps_the_default_colour(d, isolate_app_settings):
    isolate_app_settings.regularize_wiring = False
    two_parts(d, PAPER_CAP, PAPER_IND)
    wire = draw(d, "A", "bottom", "B", "top")
    assert wire.line_width == 2.0                 # the stage-1 fix, always on
    assert wire.line_color.name() == "#123456"


def test_a_wire_from_a_junction_inherits_its_net(d):
    from diagrammer.commands.style_command import ChangeStyleCommand
    d.add(PAPER_CAP, (0, 0), anchor="top", id="A")
    d.node((0, 200), id="N")
    d.node((200, 200), id="M")
    first = draw(d, "A", "bottom", "N", "center")
    d.undo_stack.push(ChangeStyleCommand(first, "line_color",
                                         QColor(first.line_color),
                                         QColor("#cc3333")))
    second = draw(d, "N", "center", "M", "center")   # no part at either end
    assert second.line_width == 2.0
    assert second.line_color.name() == "#cc3333"


# ==========================================================================
# disagreeing leads: the notice
# ==========================================================================

def test_disagreeing_leads_are_reported_not_blocked(d):
    two_parts(d, PAPER_CAP, SLIDES_IND)
    got = []
    d.scene.wiring_disagreement.connect(lambda f, at: got.append((f, at)))
    wire = draw(d, "A", "bottom", "B", "top")
    assert wire is not None                           # the wire exists
    [(findings, _at)] = got
    assert [f.check_id for f in findings] == ["leads-disagree"]
    assert {fx.label for fx in findings[0].fixes} == {"Use 2 px", "Use 3 px"}


def test_mode_off_reports_nothing(d, isolate_app_settings):
    isolate_app_settings.regularize_wiring = False
    two_parts(d, PAPER_CAP, SLIDES_IND)
    got = []
    d.scene.wiring_disagreement.connect(lambda f, at: got.append(f))
    draw(d, "A", "bottom", "B", "top")
    assert got == []


def test_the_notice_applies_a_choice_as_one_undo_step(d):
    from diagrammer.canvas.view import DiagramView
    from diagrammer.canvas.wiring_notice import show_wiring_notice
    two_parts(d, PAPER_CAP, SLIDES_IND)
    view = DiagramView(d.scene)
    view.resize(800, 600)
    got = []
    d.scene.wiring_disagreement.connect(lambda f, at: got.append((f, at)))
    wire = draw(d, "A", "bottom", "B", "top")
    before = (wire.line_width, wire.style_deliberate)
    notice = show_wiring_notice(view, *got[0])
    from PySide6.QtWidgets import QLabel
    text = " ".join(lbl.text() for lbl in notice.findChildren(QLabel))
    assert "CAP1:bottom 2 px" in text and "IND1:top 3 px" in text
    assert "swapping it would match" in text
    n = d.undo_stack.count()

    from PySide6.QtWidgets import QPushButton
    use2 = next(b for b in notice.findChildren(QPushButton)
                if b.text() == "Use 2 px")
    use2.click()
    assert (wire.line_width, wire.style_deliberate) == (2.0, True)
    assert d.undo_stack.count() == n + 1
    assert view._wiring_notice is None
    d.undo_stack.undo()
    assert (wire.line_width, wire.style_deliberate) == before


def test_twins_across_the_weight_libraries(d):
    from diagrammer.wirecheck.scene import twin_key
    lib = d.library
    assert twin_key(lib, SLIDES_IND) == PAPER_IND
    # Folder drift: splitters sit in different folders in the two libraries.
    assert twin_key(lib, "PAPER_2pt/circuits/µwave/splitters/SPLITTER1x2") == \
        "SLIDES_3pt/circuits/splitters/SPLITTER1x2"
    # Port names differ, so no safe swap.
    assert twin_key(lib, "PAPER_2pt/circuits/µwave/PASSIVE/BIAS_TEE0") is None


# ==========================================================================
# the deliberate mark travels
# ==========================================================================

def test_deliberate_survives_save_and_load(d, tmp_path):
    from diagrammer.items.connection_item import ConnectionItem
    two_parts(d, PAPER_CAP, PAPER_IND)
    wire = draw(d, "A", "bottom", "B", "top")
    wire.style_deliberate = True
    path = tmp_path / "x.dgm"
    d.save(path)
    assert '"style_deliberate": true' in path.read_text()
    d.load(path)
    [again] = [i for i in d.scene.items() if isinstance(i, ConnectionItem)]
    assert again.style_deliberate


def test_an_unmarked_wire_does_not_write_the_field(d, tmp_path):
    two_parts(d, PAPER_CAP, PAPER_IND)
    draw(d, "A", "bottom", "B", "top")
    d.save(tmp_path / "x.dgm")
    assert "style_deliberate" not in (tmp_path / "x.dgm").read_text()


# ==========================================================================
# Properties: a hand-set width
# ==========================================================================

@pytest.fixture
def panel(d):
    from diagrammer.panels.properties_panel import PropertiesPanel
    p = PropertiesPanel()
    p._scene = d.scene
    return p


def test_hand_set_width_warns_and_marks_deliberate(d, panel):
    two_parts(d, PAPER_CAP, PAPER_IND)
    wire = draw(d, "A", "bottom", "B", "top")
    panel._build_connection_form(wire)
    assert not panel._wire_note.isVisibleTo(panel)

    panel._push_wire_style(wire, "line_width", 4.0)
    assert wire.line_width == 4.0 and wire.style_deliberate
    assert "2 pt" in panel._wire_note.text()
    assert "on purpose" in panel._wire_note.text()

    panel._push_wire_style(wire, "line_width", 2.0)   # back to the leads
    assert not wire.style_deliberate
    assert not panel._wire_note.text()


def test_match_leads_restores_and_unmarks_in_one_step(d, panel):
    two_parts(d, PAPER_CAP, PAPER_IND)
    wire = draw(d, "A", "bottom", "B", "top")
    panel._build_connection_form(wire)
    panel._push_wire_style(wire, "line_width", 5.0)
    n = d.undo_stack.count()
    panel._wire_match_btn.click()
    assert (wire.line_width, wire.style_deliberate) == (2.0, False)
    assert d.undo_stack.count() == n + 1


def test_properties_says_when_the_leads_disagree(d, panel):
    two_parts(d, PAPER_CAP, SLIDES_IND)
    wire = draw(d, "A", "bottom", "B", "top")
    panel._build_connection_form(wire)
    assert "differ" in panel._wire_note.text()
    assert "2 pt" in panel._wire_note.text() and "3 pt" in panel._wire_note.text()
    assert not panel._wire_match_btn.isVisibleTo(panel)   # nothing to match


def test_hand_set_width_outside_the_mode_is_just_a_width(d, panel,
                                                         isolate_app_settings):
    two_parts(d, PAPER_CAP, PAPER_IND)
    wire = draw(d, "A", "bottom", "B", "top")
    isolate_app_settings.regularize_wiring = False
    panel._build_connection_form(wire)
    panel._push_wire_style(wire, "line_width", 4.0)
    assert wire.line_width == 4.0 and not wire.style_deliberate
    assert not panel._wire_note.text()


# ==========================================================================
# the switch
# ==========================================================================

@pytest.fixture
def win(isolate_component_overlay, isolate_app_settings, _ensure_qapp):
    from diagrammer.main_window import MainWindow
    w = MainWindow()
    yield w
    w.hide()
    w.deleteLater()


def test_on_by_default_and_both_controls_agree(win, isolate_app_settings):
    s = isolate_app_settings
    assert s.regularize_wiring and win._regularize_act.isChecked()
    assert win._regularize_cb.isChecked()
    win._regularize_cb.setChecked(False)
    assert not s.regularize_wiring and not win._regularize_act.isChecked()
    win._regularize_act.setChecked(True)
    assert s.regularize_wiring and win._regularize_cb.isChecked()


def test_shift_w_is_the_shortcut(win):
    from tests.test_shortcut_overlay import native
    assert win._regularize_act.shortcut().toString(
        win._regularize_act.shortcut().SequenceFormat.NativeText) == native("Shift+W")


def test_turning_the_mode_off_dismisses_a_notice(win):
    from diagrammer.canvas.wiring_notice import show_wiring_notice
    from diagrammer.wirecheck import Finding
    f = Finding("leads-disagree", "x", ("w",))
    show_wiring_notice(win._view, [f], QPointF(0, 0))
    assert win._view._wiring_notice is not None
    win._regularize_act.setChecked(False)
    assert win._view._wiring_notice is None
