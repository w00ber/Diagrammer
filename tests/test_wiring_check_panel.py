"""Routing ▸ Check Wiring… (issue #47, stage 3): the auditor for wires that
already exist — list, zoom-to, Accept / Reject, Show rejected."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

PAPER_CAP = "PAPER_2pt/circuits/LCR/CAP1"
PAPER_IND = "PAPER_2pt/circuits/LCR/IND1"
PAPER_RES = "PAPER_2pt/circuits/LCR/RES1"
SLIDES_IND = "SLIDES_3pt/circuits/LCR/IND1"


@pytest.fixture
def d(isolate_app_settings):
    from diagrammer.headless import Diagram, load_library
    lib = load_library(include_overlay=False, include_custom=False)
    # Black, like the leads, so only the widths we spoil are findings.
    isolate_app_settings.default_line_color = QColor("#000000")
    return Diagram(library=lib)


@pytest.fixture
def panel(d):
    from diagrammer.canvas.view import DiagramView
    from diagrammer.panels.wiring_check import WiringCheckPanel
    view = DiagramView(d.scene)
    view.resize(800, 600)
    p = WiringCheckPanel()
    p.set_document(d.scene, view)
    p.show()                     # visible: re-checks on every undo-stack change
    yield p
    p.hide()
    p.deleteLater()


def wire(d, wid):
    return d._items[wid]


def spoil(d, wid, width):
    """Set a width by hand, the way an old diagram has it."""
    from diagrammer.commands.style_command import ChangeStyleCommand
    w = wire(d, wid)
    d.undo_stack.push(ChangeStyleCommand(w, "line_width", w.line_width, width))


def chain(d, n=3):
    """n PAPER parts in a column, wired top to bottom; returns wire ids."""
    keys = [PAPER_CAP, PAPER_IND, PAPER_RES, PAPER_CAP]
    for i in range(n):
        d.add(keys[i], (0, 200 * i), anchor="top", id=f"P{i}")
    return [d.connect(f"P{i}", "bottom", f"P{i + 1}", "top", id=f"w{i}")
            for i in range(n - 1)]


def finish(panel):
    """Let the debounced re-check run now."""
    panel.recheck()


# ==========================================================================
# engine additions (Qt-free)
# ==========================================================================

def test_rejected_finds_what_deliberate_marks_hide(d):
    from diagrammer import wirecheck
    from diagrammer.wirecheck.scene import snapshot
    w0, w1 = chain(d)
    spoil(d, w0, 5.0)
    spoil(d, w1, 5.0)
    wire(d, w1).style_deliberate = True
    snap = snapshot(d.scene)
    assert [f.wire_ids for f in wirecheck.check(snap)] == [(wire(d, w0).instance_id,)]
    assert [f.wire_ids for f in wirecheck.rejected(snap)] == [(wire(d, w1).instance_id,)]


def test_combine_verifies_each_pick_in_turn(d):
    from diagrammer import wirecheck
    from diagrammer.wirecheck.scene import snapshot
    w0, w1 = chain(d)
    spoil(d, w0, 5.0)
    spoil(d, w1, 6.0)
    snap = snapshot(d.scene)
    found = wirecheck.check(snap)
    bogus = wirecheck.Fix("Use 9 pt", {found[0].wire_ids[0]: {"width": 9.0}})
    picks = [(found[0], bogus)] + [(f, f.fixes[0]) for f in found]
    fix, taken = wirecheck.combine(snap, picks, "all")
    assert taken == 2                         # the bogus one refused
    assert wirecheck.check(snap.apply(fix)) == []


# ==========================================================================
# the list
# ==========================================================================

def test_lists_findings_grouped_by_check(d, panel):
    w0, w1 = chain(d)
    spoil(d, w0, 5.0)
    finish(panel)
    [(f, rejected)] = panel.findings()
    assert f.check_id == "wire-width" and not rejected
    head = panel._tree.topLevelItem(0)
    assert head.text(0).startswith("Wire width differs from its lead — 1")
    assert "CAP1:bottom" in head.child(0).text(0)
    # Take the lead's first; making the leads match the wire is offered too
    # (stage 4), after it.
    assert [fx.label for fx in f.fixes] == [
        "Use 2 pt", "Set CAP1:bottom, IND1:top leads to 5 pt"]


def test_a_clean_diagram_says_so(d, panel):
    chain(d)
    finish(panel)
    assert panel.findings() == []
    assert "Every wire matches" in panel._summary.text()


def test_it_never_goes_stale(d, panel):
    w0, _ = chain(d)
    finish(panel)
    assert panel.findings() == []
    spoil(d, w0, 5.0)
    assert panel._timer.isActive()            # the change scheduled a check
    finish(panel)
    assert len(panel.findings()) == 1


def test_selecting_a_finding_selects_and_frames_its_wires(d, panel):
    w0, w1 = chain(d)
    spoil(d, w1, 5.0)
    finish(panel)
    [(f, _)] = panel.findings()
    assert panel.select_finding(f)
    assert wire(d, w1).isSelected() and not wire(d, w0).isSelected()
    view = panel._view
    shown = view.mapToScene(view.viewport().rect()).boundingRect()
    assert shown.contains(wire(d, w1).sceneBoundingRect())
    assert view.transform().m11() <= 2.0 + 1e-9


def test_a_recheck_does_not_move_the_view(d, panel):
    w0, w1 = chain(d)
    spoil(d, w0, 5.0)
    finish(panel)
    panel.select_finding(panel.findings()[0][0])
    d.scene.clearSelection()
    before = panel._view.transform()
    finish(panel)
    assert panel._view.transform() == before
    assert not wire(d, w0).isSelected()


# ==========================================================================
# accept / reject
# ==========================================================================

def test_accept_applies_the_fix_as_one_undo_step(d, panel):
    w0, _ = chain(d)
    spoil(d, w0, 5.0)
    finish(panel)
    panel.select_finding(panel.findings()[0][0])
    n = d.undo_stack.count()
    panel._accept_btn.click()
    assert wire(d, w0).line_width == 2.0
    assert d.undo_stack.count() == n + 1
    assert panel.findings() == []
    d.undo_stack.undo()
    finish(panel)
    assert wire(d, w0).line_width == 5.0 and len(panel.findings()) == 1


def test_accept_all_like_this_is_one_undo_step(d, panel):
    ws = chain(d, 4)
    for w, width in zip(ws, (5.0, 6.0, 7.0)):
        spoil(d, w, width)
    finish(panel)
    assert len(panel.findings()) == 3
    panel.select_finding(panel.findings()[0][0])
    n = d.undo_stack.count()
    panel._accept_all_btn.click()
    assert [wire(d, w).line_width for w in ws] == [2.0, 2.0, 2.0]
    assert d.undo_stack.count() == n + 1
    assert panel.findings() == []


def test_disagreeing_leads_offer_both_widths_recommended_first(d, panel):
    # Mostly PAPER, one SLIDES part: Use 2 pt is recommended.
    d.add(SLIDES_IND, (0, 0), anchor="top", id="S")
    d.add(PAPER_CAP, (0, 200), anchor="top", id="A")
    d.add(PAPER_RES, (300, 0), anchor="top", id="R")
    w = d.connect("S", "bottom", "A", "top")
    finish(panel)
    [(f, _)] = panel.findings()
    assert f.check_id == "leads-disagree"
    panel.select_finding(f)
    labels = [panel._fix_combo.itemText(i)
              for i in range(panel._fix_combo.count())]
    # Swaps first (stage 4), the one matching the diagram leading.
    assert labels == ["Swap IND1 to PAPER_2pt", "Swap CAP1 to SLIDES_3pt",
                      "Set IND1:bottom lead to 2 pt",
                      "Set CAP1:top lead to 3 pt", "Use 2 pt", "Use 3 pt"]
    panel._fix_combo.setCurrentIndex(5)
    panel._accept_btn.click()
    assert wire(d, w).line_width == 3.0 and wire(d, w).style_deliberate
    assert panel.findings() == []
    # The decision is a kept mark: Show rejected finds it.
    assert [f.check_id for f, rej in panel.findings(include_rejected=True)
            if rej] == ["leads-disagree"]


def test_reject_marks_deliberate_and_show_rejected_brings_it_back(d, panel):
    w0, _ = chain(d)
    spoil(d, w0, 5.0)
    finish(panel)
    panel.select_finding(panel.findings()[0][0])
    n = d.undo_stack.count()
    panel._reject_btn.click()
    assert wire(d, w0).style_deliberate and wire(d, w0).line_width == 5.0
    assert d.undo_stack.count() == n + 1
    assert panel.findings() == []
    assert "1 rejected (hidden)" in panel._summary.text()

    panel._show_rejected.setChecked(True)
    [(f, rejected)] = panel.findings(include_rejected=True)
    assert rejected
    assert "rejected" in panel._tree.topLevelItem(0).child(0).text(0)
    panel.select_finding(f)
    assert panel._accept_btn.text() == "Restore"
    assert not panel._reject_btn.isEnabled()
    panel._accept_btn.click()                 # Restore
    assert not wire(d, w0).style_deliberate
    assert [rej for _f, rej in panel.findings(include_rejected=True)] == [False]


# ==========================================================================
# the window
# ==========================================================================

@pytest.fixture
def win(isolate_component_overlay, isolate_app_settings, _ensure_qapp):
    from diagrammer.main_window import MainWindow
    w = MainWindow()
    yield w
    w.hide()
    w.deleteLater()


def test_routing_menu_opens_the_panel_on_the_active_diagram(win):
    act = win._check_wiring_act
    assert act.text().replace("&", "") == "Check Wiring…"
    assert not win._wiring_panel.isVisible()
    act.trigger()
    assert win._wiring_panel._scene is win._scene
    first = win._scene
    win._new_document()
    assert win._wiring_panel._scene is win._scene is not first
