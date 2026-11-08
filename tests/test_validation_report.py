"""The batch report and the overrides dialog.

The thing worth protecting in the report is that it stays *readable*. Grouping
by check rather than by file is what turns the 74-file linecap migration into
one row with one button, and ordering errors ahead of breadth is what stops a
wide warning from burying a narrow error.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QThreadPool

from diagrammer.models.overrides import OverrideIndex
from diagrammer.panels.library_overrides import LibraryOverridesDialog
from diagrammer.panels.validation_report import ValidationReportView

# The leads carry their own class. That matters: the lead-cap fix refuses to
# touch a class the artwork also uses, since butt-capping it would restyle the
# body — so a fixture sharing one would exercise the refusal, not the fix.
ROUND_CAP = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 100">
  <defs><style>
      .st1, .st2 { fill: none; stroke: #000; stroke-width: 2px;
      stroke-linecap: round; }
  </style></defs>
  <g id="artwork"><path class="st2" d="M10,50H50"/></g>
  <g id="leads"><line class="st1" x1="30" y1="0" x2="30" y2="45"/></g>
  <g id="ports"><circle id="port:top" cx="30" cy="0" r="3"/></g>
</svg>
'''

NO_PORTS = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 100">
  <g id="artwork"><path d="M10,50H50"/></g>
</svg>
'''

SOUND = ROUND_CAP.replace("\n      stroke-linecap: round; ", " ")
assert "stroke-linecap" not in SOUND, "the sound fixture must declare no cap"


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "PAPER_2pt"
    root.mkdir()
    for i in range(4):
        (root / f"cap{i}.svg").write_text(ROUND_CAP, encoding="utf-8")
    (root / "broken.svg").write_text(NO_PORTS, encoding="utf-8")
    (root / "fine.svg").write_text(SOUND, encoding="utf-8")
    return root


def run_report(root: Path, **kw) -> ValidationReportView:
    dlg = ValidationReportView(roots=[(root.name, root)], **kw)
    assert QThreadPool.globalInstance().waitForDone(30000)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    return dlg


def rows(dlg: ValidationReportView) -> list[str]:
    return [dlg._tree.topLevelItem(i).text(0)
            for i in range(dlg._tree.topLevelItemCount())]


def row_for(dlg: ValidationReportView, check_id: str):
    for i in range(dlg._tree.topLevelItemCount()):
        item = dlg._tree.topLevelItem(i)
        if item.data(0, Qt.ItemDataRole.UserRole + 1) == check_id:
            return item
    return None


# ==========================================================================
# the report
# ==========================================================================

def test_findings_are_grouped_by_check_not_by_file(library) -> None:
    """Four files with the same warning is one row, not four."""
    dlg = run_report(library)
    try:
        linecap = row_for(dlg, "lead-linecap-not-butt")
        assert linecap is not None
        assert "4 file(s)" in linecap.text(0)
        assert linecap.childCount() == 4
    finally:
        dlg.deleteLater()


def test_errors_are_listed_before_wider_warnings(library) -> None:
    """One broken file outranks four merely unfashionable ones."""
    dlg = run_report(library)
    try:
        order = [dlg._tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole + 1)
                 for i in range(dlg._tree.topLevelItemCount())]
        assert order.index("no-ports") < order.index("lead-linecap-not-butt")
    finally:
        dlg.deleteLater()


def test_summary_counts_files_not_findings(library) -> None:
    dlg = run_report(library)
    try:
        text = dlg._summary.text()
        assert "of 6 components" in text
        assert "1 failing" in text
        assert "4 with warnings" in text
    finally:
        dlg.deleteLater()


def test_errors_only_hides_the_warnings(library) -> None:
    dlg = run_report(library)
    try:
        assert row_for(dlg, "lead-linecap-not-butt") is not None
        dlg._errors_only.setChecked(True)
        assert row_for(dlg, "lead-linecap-not-butt") is None
        assert row_for(dlg, "no-ports") is not None
    finally:
        dlg.deleteLater()


def test_bulk_fix_is_offered_only_for_a_bulk_safe_fix(library) -> None:
    """This is where the linecap migration gets done as one deliberate act —
    and where a geometry-moving fix must never appear."""
    dlg = run_report(library)
    try:
        dlg._tree.setCurrentItem(row_for(dlg, "lead-linecap-not-butt"))
        assert dlg._bulk_btn.isEnabled()
        assert "all 4 files" in dlg._bulk_btn.text()

        dlg._tree.setCurrentItem(row_for(dlg, "no-ports"))
        assert not dlg._bulk_btn.isEnabled(), "no-ports has no fix at all"
    finally:
        dlg.deleteLater()


def test_no_geometry_moving_fix_is_ever_bulk_offered(tmp_path: Path) -> None:
    root = tmp_path / "PAPER_2pt"
    root.mkdir()
    (root / "rebased.svg").write_text(
        ROUND_CAP.replace('viewBox="0 0 60 100"', 'viewBox="-3 -3 60 100"'),
        encoding="utf-8")
    dlg = run_report(root)
    try:
        item = row_for(dlg, "viewbox-origin-nonzero")
        assert item is not None, "the finding should still be reported"
        dlg._tree.setCurrentItem(item)
        assert not dlg._bulk_btn.isEnabled(), (
            "rebase-viewbox-to-origin moves geometry, so it is not bulk-safe")
    finally:
        dlg.deleteLater()


def test_bulk_fix_rewrites_every_affected_file(library, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    dlg = run_report(library)
    try:
        dlg._tree.setCurrentItem(row_for(dlg, "lead-linecap-not-butt"))
        dlg._bulk_fix()
        from diagrammer.svglint import text_svg as T

        for i in range(4):
            text = (library / f"cap{i}.svg").read_text(encoding="utf-8")
            # Resolved value, not raw text: the fix appends an overriding rule
            # rather than editing the shared one, so "round" is still in the
            # source and correctly still applies to anything else using it.
            caps = T.css_property(text, "stroke-linecap")
            assert caps["st1"] == "butt", "the lead class changed"
            assert caps["st2"] == "round", "the artwork class did not"
        # the untouched files stay untouched
        assert (library / "broken.svg").read_text(encoding="utf-8") == NO_PORTS
    finally:
        dlg.deleteLater()


def test_double_click_opens_the_inspector_on_that_file(library) -> None:
    opened: list[Path] = []
    dlg = run_report(library, on_open=opened.append)
    try:
        head = row_for(dlg, "lead-linecap-not-butt")
        child = head.child(0)
        dlg._tree.setCurrentItem(child)
        dlg._open_selected()
        assert opened and opened[0].name.startswith("cap")
    finally:
        dlg.deleteLater()


def test_selecting_a_group_header_opens_its_first_file(library) -> None:
    opened: list[Path] = []
    dlg = run_report(library, on_open=opened.append)
    try:
        dlg._tree.setCurrentItem(row_for(dlg, "no-ports"))
        dlg._open_selected()
        assert opened == [library / "broken.svg"]
    finally:
        dlg.deleteLater()


def test_an_unreadable_file_does_not_abort_the_sweep(library) -> None:
    (library / "unreadable.svg").mkdir()  # a directory named like an SVG
    dlg = run_report(library)
    try:
        assert "of 6 components" in dlg._summary.text() or \
               "of 7 components" in dlg._summary.text()
    finally:
        dlg.deleteLater()


# ==========================================================================
# the overrides dialog
# ==========================================================================

@pytest.fixture
def forked(tmp_path: Path):
    builtin = tmp_path / "builtin"
    overlay = tmp_path / "overlay"
    (builtin / "PAPER_2pt").mkdir(parents=True)
    (builtin / "PAPER_2pt" / "cap.svg").write_text(SOUND, encoding="utf-8")
    (overlay / "PAPER_2pt").mkdir(parents=True)
    (overlay / "PAPER_2pt" / "cap.svg").write_text(ROUND_CAP, encoding="utf-8")
    index = OverrideIndex.load(overlay, builtin)
    index.record_fork("PAPER_2pt/cap.svg",
                      origin=builtin / "PAPER_2pt" / "cap.svg",
                      app_version="0.12.1")
    return index, builtin, overlay


def test_overrides_dialog_lists_forks_hidden_and_deleted(forked) -> None:
    index, builtin, _overlay = forked
    index.hide("builtin", "PAPER_2pt/gone.svg")
    index.record_delete(rel="PAPER_2pt/old.svg", trash=Path("/tmp/old.svg"))
    dlg = LibraryOverridesDialog(index=index, builtin_root=builtin)
    try:
        assert dlg._tabs.tabText(0).startswith("My Copies (1)")
        assert dlg._tabs.tabText(1) == "Hidden (1)"
        assert dlg._tabs.tabText(2) == "Deleted (1)"
        assert dlg._forks.topLevelItem(0).text(1) == "Up to date"
    finally:
        dlg.deleteLater()


def test_drift_is_flagged_and_keep_mine_clears_it(forked) -> None:
    """The question copy-on-write raises: the shipped version moved on."""
    index, builtin, _overlay = forked
    (builtin / "PAPER_2pt" / "cap.svg").write_text(
        SOUND.replace("M10,50H50", "M10,50H52"), encoding="utf-8")

    dlg = LibraryOverridesDialog(index=index, builtin_root=builtin)
    try:
        assert "to review" in dlg._tabs.tabText(0)
        assert "changed" in dlg._forks.topLevelItem(0).text(1)

        dlg._forks.setCurrentItem(dlg._forks.topLevelItem(0))
        dlg._keep_mine()
        assert dlg._forks.topLevelItem(0).text(1) == "Up to date"
        assert "to review" not in dlg._tabs.tabText(0)
    finally:
        dlg.deleteLater()


def test_a_redundant_fork_is_called_out(forked) -> None:
    """An edit that was reverted, or an upstream that caught up."""
    index, builtin, overlay = forked
    (overlay / "PAPER_2pt" / "cap.svg").write_text(SOUND, encoding="utf-8")
    index.accept_upstream_change("PAPER_2pt/cap.svg")
    dlg = LibraryOverridesDialog(index=index, builtin_root=builtin)
    try:
        assert "no longer needed" in dlg._forks.topLevelItem(0).text(1)
    finally:
        dlg.deleteLater()


def test_restore_unhides_a_tombstoned_component(forked) -> None:
    index, builtin, _overlay = forked
    index.hide("builtin", "PAPER_2pt/cap.svg")
    changed: list[int] = []
    dlg = LibraryOverridesDialog(index=index, builtin_root=builtin,
                                 on_changed=lambda: changed.append(1))
    try:
        dlg._hidden.setCurrentItem(dlg._hidden.topLevelItem(0))
        dlg._restore()
        assert not index.is_hidden("builtin", "PAPER_2pt/cap.svg")
        assert changed, "the library must be told to rebuild"
        assert dlg._tabs.tabText(1) == "Hidden (0)"
    finally:
        dlg.deleteLater()


def test_tidy_drops_only_the_dead_tombstones(forked, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    index, builtin, _overlay = forked
    index.hide("builtin", "PAPER_2pt/cap.svg")       # exists
    index.hide("builtin", "PAPER_2pt/vanished.svg")  # does not
    dlg = LibraryOverridesDialog(index=index, builtin_root=builtin)
    try:
        dlg._tidy()
        assert index.is_hidden("builtin", "PAPER_2pt/cap.svg")
        assert not index.is_hidden("builtin", "PAPER_2pt/vanished.svg")
    finally:
        dlg.deleteLater()


def test_compare_opens_the_users_copy(forked) -> None:
    index, builtin, overlay = forked
    opened: list[Path] = []
    dlg = LibraryOverridesDialog(index=index, builtin_root=builtin,
                                 on_compare=opened.append)
    try:
        dlg._forks.setCurrentItem(dlg._forks.topLevelItem(0))
        dlg._compare()
        assert opened == [overlay / "PAPER_2pt" / "cap.svg"]
    finally:
        dlg.deleteLater()


def test_a_read_only_index_disables_the_mutating_buttons(forked) -> None:
    """A newer app wrote it, so changing it here could drop what it recorded."""
    index, builtin, _overlay = forked
    index.read_only = True
    dlg = LibraryOverridesDialog(index=index, builtin_root=builtin)
    try:
        dlg._forks.setCurrentItem(dlg._forks.topLevelItem(0))
        assert not dlg._keep_btn.isEnabled()
        assert not dlg._revert_btn.isEnabled()
        assert dlg._compare_btn.isEnabled(), "looking is always allowed"
    finally:
        dlg.deleteLater()


# ==========================================================================
# staying current: the report re-checks what the inspector (or anyone) saved
# ==========================================================================

def _children_paths(item) -> list[str]:
    return sorted(Path(item.child(i).data(0, Qt.ItemDataRole.UserRole)).name
                  for i in range(item.childCount()))


def test_a_save_from_the_inspector_updates_the_listed_file(library) -> None:
    dlg = run_report(library)
    try:
        assert "4 file(s)" in row_for(dlg, "lead-linecap-not-butt").text(0)

        (library / "cap0.svg").write_text(SOUND, encoding="utf-8")
        dlg.file_saved(library / "cap0.svg")

        row = row_for(dlg, "lead-linecap-not-butt")
        assert "3 file(s)" in row.text(0)
        assert "cap0.svg" not in _children_paths(row)
        assert "Re-checked cap0.svg after it was saved: now clean" in \
            dlg._summary.text()
        assert "3 with warnings" in dlg._summary.text()
    finally:
        dlg.deleteLater()


def test_a_save_keeps_the_users_place_in_the_tree(library) -> None:
    dlg = run_report(library)
    try:
        row = row_for(dlg, "lead-linecap-not-butt")
        target = next(row.child(i) for i in range(row.childCount())
                      if "cap2.svg" in row.child(i).text(0))
        dlg._tree.setCurrentItem(target)

        (library / "cap0.svg").write_text(SOUND, encoding="utf-8")
        dlg.file_saved(library / "cap0.svg")

        assert "cap2.svg" in dlg._tree.currentItem().text(0)
    finally:
        dlg.deleteLater()


def test_a_new_file_saved_into_scope_appears(library) -> None:
    dlg = run_report(library)
    try:
        (library / "new.svg").write_text(NO_PORTS, encoding="utf-8")
        dlg.file_saved(library / "new.svg")
        assert _children_paths(row_for(dlg, "no-ports")) == [
            "broken.svg", "new.svg"]
        assert "of 7 components" in dlg._summary.text()
    finally:
        dlg.deleteLater()


def test_a_save_outside_the_scope_changes_nothing(library, tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere.svg"
    elsewhere.write_text(NO_PORTS, encoding="utf-8")
    dlg = run_report(library)
    try:
        dlg.file_saved(elsewhere)
        assert _children_paths(row_for(dlg, "no-ports")) == ["broken.svg"]
        assert "of 6 components" in dlg._summary.text()
    finally:
        dlg.deleteLater()


def test_a_fork_marks_the_shipped_file_as_replaced(library, tmp_path) -> None:
    """Saving a shipped part writes the user's copy elsewhere. The shipped
    file's findings stay (it did not change), but the report must say the
    canvas no longer uses it — otherwise the save looks like it did nothing."""
    copy = tmp_path / "overlay" / "PAPER_2pt" / "broken.svg"
    copy.parent.mkdir(parents=True)
    forks: dict[Path, Path] = {}
    opened: list[Path] = []
    dlg = run_report(library, on_open=opened.append,
                     shadowed_by=lambda p: forks.get(p.resolve()))
    try:
        shipped = library / "broken.svg"
        copy.write_text(SOUND, encoding="utf-8")
        forks[shipped.resolve()] = copy
        dlg.file_saved(copy, replaces=shipped)

        row = row_for(dlg, "no-ports")
        assert row is not None, "the shipped file still has its fault"
        assert "replaced by your copy" in row.child(0).text(2)
        assert "Saved your copy of broken.svg" in dlg._summary.text()

        dlg._tree.setCurrentItem(row.child(0))
        dlg._open_selected()
        assert opened == [copy], "opening the row opens what the canvas uses"
    finally:
        dlg.deleteLater()


def test_an_edit_made_elsewhere_is_picked_up_on_return(library) -> None:
    """An external editor, a git checkout: no save notification, but the
    file's stamp moved, and showing the tab again re-checks it."""
    dlg = run_report(library)
    try:
        (library / "broken.svg").write_text(SOUND, encoding="utf-8")
        dlg.refresh_stale()
        assert row_for(dlg, "no-ports") is None
        assert "Re-checked 1 file(s) that changed on disk" in \
            dlg._summary.text()
    finally:
        dlg.deleteLater()


def test_a_save_during_a_sweep_is_applied_when_it_lands(library) -> None:
    dlg = run_report(library)
    try:
        dlg._start()
        (library / "broken.svg").write_text(SOUND, encoding="utf-8")
        dlg.file_saved(library / "broken.svg")
        assert QThreadPool.globalInstance().waitForDone(30000)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        assert row_for(dlg, "no-ports") is None
    finally:
        dlg.deleteLater()


# ==========================================================================
# the report as a tab in the main window
# ==========================================================================

@pytest.fixture
def win(isolate_component_overlay, _ensure_qapp):
    from diagrammer.main_window import MainWindow

    w = MainWindow()
    yield w
    w.hide()
    w.deleteLater()


def _settle():
    from PySide6.QtWidgets import QApplication
    assert QThreadPool.globalInstance().waitForDone(30000)
    QApplication.processEvents()


def test_the_report_opens_once_as_a_tab(win, library) -> None:
    win._show_validation_report([(library.name, library)])
    win._show_validation_report([(library.name, library)])
    _settle()
    labels = [win._tab_widget.tabText(i) for i in range(win._tab_widget.count())]
    assert labels.count("Validation") == 1
    assert win._tab_widget.currentWidget() is win._validation_report_tab()
    # Edit/Draw still target the diagram behind it.
    assert win._scene is win._documents()[0].scene


def test_an_inspector_save_reaches_the_report_tab(win, library) -> None:
    from diagrammer.io.component_writer import SaveResult

    win._show_validation_report([(library.name, library)])
    _settle()
    report = win._validation_report_tab()
    assert row_for(report, "no-ports") is not None

    (library / "broken.svg").write_text(SOUND, encoding="utf-8")
    win._on_component_saved(SaveResult(path=library / "broken.svg"))

    assert row_for(report, "no-ports") is None


def test_validating_a_library_category_uses_the_same_tab(win) -> None:
    category, defs = next(iter(win._library.categories.items()))
    win._component_actions.validate_category(category, defs)
    win._validate_libraries()
    _settle()
    labels = [win._tab_widget.tabText(i) for i in range(win._tab_widget.count())]
    assert labels.count("Validation") == 1
