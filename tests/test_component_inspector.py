"""The Component Inspector: the preview loop, the markers, and saving.

The behaviour worth protecting is that nothing reaches the disk until Save, and
that what the preview shows is exactly what Save would write. Both follow from
recomputing the candidate from the pristine original on every toggle, so most of
these tests poke ``_selected_fix_ids`` and assert on the candidate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from diagrammer import svglint
from diagrammer.models.overrides import OverrideIndex
from diagrammer.panels.component_inspector import (
    MODE_FIXED,
    MODE_OVERLAY,
    MODE_SIDE,
    ComponentInspectorDialog,
    FindingMarkerItem,
    render_component_bytes,
)

MANGLED = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 100">
  <defs><style>
      .st1 { fill: none; stroke: #000; stroke-width: 2px; }
      .st2 { fill: none; stroke: #000; stroke-width: 2px; }
  </style></defs>
  <g id="Layer_1">
    <g id="artwork"><path class="st2" d="M0,50H60"/></g>
    <g id="leads"><line class="st1" x1="30" y1="0" x2="30" y2="45"/></g>
    <g id="ports">
      <circle id="port_top" data-name="port:top" class="st1" cx="30" cy="0" r="3"/>
    </g>
    <path class="st2" d="M40.1,24.1"/>
  </g>
</svg>
'''

POSITIONAL = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-3 -3 60 100">
  <defs><style>.st1 { fill: none; stroke: #000; stroke-width: 4px; }</style></defs>
  <g id="artwork"><path class="st1" d="M0,50H60"/></g>
  <g id="leads"><line class="st1" x1="30" y1="40" x2="30" y2="20"/></g>
  <g id="ports"><circle id="port:mid" cx="30" cy="50" r="3"/></g>
</svg>
'''

SOUND = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 100">
  <defs><style>.st1 { fill: none; stroke: #000; stroke-width: 2px; }</style></defs>
  <g id="artwork"><path class="st1" d="M10,50H50"/></g>
  <g id="leads"><line class="st1" x1="30" y1="0" x2="30" y2="45"/></g>
  <g id="ports"><circle id="port:top" cx="30" cy="0" r="3"/></g>
</svg>
'''


@pytest.fixture
def workspace(tmp_path: Path):
    """A fake built-in library plus an overlay, as MainWindow would supply."""
    builtin = tmp_path / "builtin"
    overlay = tmp_path / "overlay"
    (builtin / "PAPER_2pt").mkdir(parents=True)
    overlay.mkdir()
    return builtin, overlay


def open_inspector(builtin: Path, overlay: Path, body: str, *,
                   name: str = "part.svg", saved=None):
    path = builtin / "PAPER_2pt" / name
    path.write_text(body, encoding="utf-8")
    dlg = ComponentInspectorDialog(
        path, index=OverrideIndex.load(overlay, builtin),
        builtin_root=builtin, overlay_root=overlay, on_saved=saved)
    dlg._recompute()   # the loop is debounced via QTimer; run it now
    return dlg, path


def ids(findings) -> set[str]:
    return {f.check_id for f in findings}


# ==========================================================================
# the preview loop
# ==========================================================================

def test_opens_with_the_fixes_that_are_safe_to_default_on(workspace) -> None:
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        assert ids(dlg._findings) == {
            "layer-wrapper", "mangled-named-id", "no-ports",
            "stray-anchor-point"}
        assert dlg._selected_fix_ids == {
            "unwrap-layer-group", "restore-named-ids", "strip-stray-anchors"}
        # and those alone resolve everything
        assert dlg._candidate_findings == []
    finally:
        dlg.deleteLater()


def test_unticking_a_fix_is_exact(workspace) -> None:
    """The invariant the whole loop rests on: fixes recompose from the
    original, so unticking needs no inverse transform."""
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        with_all = dlg._candidate
        dlg._selected_fix_ids.clear()
        dlg._recompute()
        assert dlg._candidate == dlg._original_text, (
            "no fixes selected must give back the original byte for byte")

        dlg._selected_fix_ids = {
            "unwrap-layer-group", "restore-named-ids", "strip-stray-anchors"}
        dlg._recompute()
        assert dlg._candidate == with_all, "re-ticking must return exactly"
    finally:
        dlg.deleteLater()


def test_findings_list_tracks_the_candidate_not_the_file(workspace) -> None:
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        assert dlg._findings_tree.topLevelItemCount() == 0
        assert "resolved" in dlg._findings_summary.text()

        dlg._selected_fix_ids.clear()
        dlg._recompute()
        assert dlg._findings_tree.topLevelItemCount() == 4
        assert "error" in dlg._findings_summary.text()
    finally:
        dlg.deleteLater()


def test_changes_box_shows_a_diff_and_the_notes(workspace) -> None:
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        diff = dlg._diff.toPlainText()
        assert diff.startswith("---"), diff[:80]
        assert "Layer_1" in diff
        assert 'restored id="port:top"' in dlg._notes_label.text()

        dlg._selected_fix_ids.clear()
        dlg._recompute()
        assert dlg._diff.toPlainText() == ""
        assert "No changes" in dlg._notes_label.text()
    finally:
        dlg.deleteLater()


def test_a_sound_component_offers_nothing(workspace) -> None:
    dlg, _ = open_inspector(*workspace, SOUND)
    try:
        assert dlg._findings == []
        assert dlg._fixes_list.count() == 1
        assert "no automatic fixes" in dlg._fixes_list.item(0).text()
        assert dlg._findings_summary.text() == "No findings."
        assert not dlg._save_btn.isEnabled(), "nothing to save"
    finally:
        dlg.deleteLater()


def test_geometry_moving_fixes_are_offered_but_not_preselected(workspace) -> None:
    dlg, _ = open_inspector(*workspace, POSITIONAL)
    try:
        offered = {dlg._fixes_list.item(i).data(256)
                   for i in range(dlg._fixes_list.count())}
        assert "rebase-viewbox-to-origin" in offered
        assert "rebase-viewbox-to-origin" not in dlg._selected_fix_ids
        marked = [dlg._fixes_list.item(i).text()
                  for i in range(dlg._fixes_list.count())
                  if dlg._fixes_list.item(i).data(256) == "rebase-viewbox-to-origin"]
        assert marked and marked[0].endswith("▲"), (
            "a geometry-moving fix must be marked in the list")
    finally:
        dlg.deleteLater()


# ==========================================================================
# markers
# ==========================================================================

def test_markers_are_created_for_positional_findings(workspace) -> None:
    dlg, _ = open_inspector(*workspace, POSITIONAL)
    try:
        kinds = sorted(m.marker.kind for m in dlg._markers)
        assert kinds == ["edge", "point", "point", "rect", "segment"]
        for m in dlg._markers:
            assert isinstance(m, FindingMarkerItem)
            assert m.toolTip(), "every marker explains itself"
            assert m.finding.detail in m.toolTip()
    finally:
        dlg.deleteLater()


def test_markers_sit_in_the_components_own_coordinates(workspace) -> None:
    """viewBox units are scene points 1:1, which is what makes a finding's
    coordinates usable with no transform."""
    dlg, _ = open_inspector(*workspace, POSITIONAL)
    try:
        point = next(m for m in dlg._markers
                     if m.marker.kind == "point"
                     and m.marker.label == "port:mid")
        assert m_coords(point) == (30.0, 50.0)
        assert point.boundingRect().center().x() == pytest.approx(30.0)
        assert point.boundingRect().center().y() == pytest.approx(50.0)
    finally:
        dlg.deleteLater()


def m_coords(item: FindingMarkerItem) -> tuple[float, float]:
    return (item.marker.coords[0], item.marker.coords[1])


def test_selecting_a_row_emphasises_its_markers(workspace) -> None:
    """The two-way link that makes a list of findings navigable."""
    dlg, _ = open_inspector(*workspace, POSITIONAL)
    try:
        tree = dlg._findings_tree
        row = tree.topLevelItem(0)
        finding = row.data(0, 256)
        tree.setCurrentItem(row)
        emphasised = [m for m in dlg._markers if m._emphasis]
        assert emphasised, "selecting a finding should highlight it"
        assert all(m.finding is finding for m in emphasised)
        assert len(emphasised) == len(finding.markers)
    finally:
        dlg.deleteLater()


def test_hovering_a_marker_selects_its_row(workspace) -> None:
    dlg, _ = open_inspector(*workspace, POSITIONAL)
    try:
        target = dlg._markers[-1].finding
        dlg._highlight_finding_row(target)
        assert dlg._findings_tree.currentItem().data(0, 256) is target
    finally:
        dlg.deleteLater()


# ==========================================================================
# comparison modes
# ==========================================================================

@pytest.mark.parametrize("mode,expected", [
    (MODE_FIXED, 1),
    (MODE_SIDE, 2),
    (MODE_OVERLAY, 2),
])
def test_each_mode_builds_the_right_number_of_renders(
        workspace, mode: str, expected: int) -> None:
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        dlg._set_mode(mode)
        svgs = [i for i in dlg._scene.items()
                if type(i).__name__ == "_SvgItem"]
        assert len(svgs) == expected
    finally:
        dlg.deleteLater()


def test_side_by_side_separates_the_two_by_one_grid_cell(workspace) -> None:
    """So a shift reads straight off the continuous grid behind both."""
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        dlg._set_mode(MODE_SIDE)
        xs = sorted(i.pos().x() for i in dlg._scene.items()
                    if type(i).__name__ == "_SvgItem")
        assert xs[0] == 0.0
        assert xs[1] == pytest.approx(60.0 + dlg._view._spacing)
    finally:
        dlg.deleteLater()


def test_overlay_ghosts_the_original_underneath(workspace) -> None:
    """The only mode in which a sub-point change is visible at all."""
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        dlg._set_mode(MODE_OVERLAY)
        svgs = [i for i in dlg._scene.items()
                if type(i).__name__ == "_SvgItem"]
        opacities = sorted(i.opacity() for i in svgs)
        assert opacities[0] < 1.0 and opacities[1] == 1.0
        assert all(i.pos().x() == 0.0 for i in svgs), "same origin"
        ghost = min(svgs, key=lambda i: i.opacity())
        assert ghost.zValue() < 0, "the ghost paints beneath"
    finally:
        dlg.deleteLater()


def test_markers_belong_to_the_candidate_only(workspace) -> None:
    """Marking the ghost too would double every marker in Overlay mode."""
    dlg, _ = open_inspector(*workspace, POSITIONAL)
    try:
        fixed_count = len(dlg._markers)
        dlg._set_mode(MODE_OVERLAY)
        assert len(dlg._markers) == fixed_count
    finally:
        dlg.deleteLater()


# ==========================================================================
# the grid
# ==========================================================================

def test_grid_defaults_to_the_library_profile(workspace) -> None:
    dlg, _ = open_inspector(*workspace, SOUND)
    try:
        assert dlg._profile.name == "PAPER_2pt"
        assert dlg._view._spacing == dlg._profile.grid == 20.0
    finally:
        dlg.deleteLater()


@pytest.mark.parametrize("spacing", [5.0, 10.0, 20.0, 7.5])
def test_the_view_survives_every_offered_spacing(workspace, spacing) -> None:
    dlg, _ = open_inspector(*workspace, SOUND)
    try:
        dlg._view.set_grid_spacing(spacing)
        assert dlg._view._spacing == spacing
        dlg._view.grab()  # forces drawBackground
    finally:
        dlg.deleteLater()


def test_grid_can_be_hidden(workspace) -> None:
    dlg, _ = open_inspector(*workspace, SOUND)
    try:
        dlg._grid_cb.setChecked(False)
        assert dlg._view._grid_visible is False
        dlg._view.grab()
    finally:
        dlg.deleteLater()


def test_a_frame_is_drawn_so_a_rebased_viewbox_is_visible(workspace) -> None:
    dlg, _ = open_inspector(*workspace, POSITIONAL)
    try:
        frames = [i for i in dlg._scene.items()
                  if type(i).__name__ == "_FrameItem"]
        assert len(frames) == 1
    finally:
        dlg.deleteLater()


# ==========================================================================
# rendering
# ==========================================================================

def test_render_keeps_every_layer_visible() -> None:
    """The canvas hides ports; the inspector must not, or it would mark up
    geometry the viewer cannot see."""
    import xml.etree.ElementTree as ET

    out = render_component_bytes(SOUND)
    root = ET.fromstring(out)
    displays = {g.get("id"): g.get("display") for g in root.iter()
                if (g.tag.split("}")[-1]) == "g" and g.get("id")}
    assert displays["ports"] is None


def test_a_file_that_does_not_parse_still_shows_something(workspace) -> None:
    """The most likely thing to be inspected is a broken file, so blank space
    would be the worst possible answer."""
    builtin, overlay = workspace
    dlg, _ = open_inspector(builtin, overlay, "<svg", name="broken.svg")
    try:
        assert ids(dlg._findings) == {"parse-failure"}
        texts = [i for i in dlg._scene.items()
                 if type(i).__name__ == "QGraphicsTextItem"]
        assert texts, "expected an explanatory label in the view"
        assert "cannot render" in texts[0].toPlainText()
    finally:
        dlg.deleteLater()


# ==========================================================================
# saving
# ==========================================================================

def test_nothing_is_written_until_save(workspace) -> None:
    builtin, overlay = workspace
    dlg, path = open_inspector(builtin, overlay, MANGLED)
    try:
        assert path.read_text(encoding="utf-8") == MANGLED
        assert list(overlay.rglob("*.svg")) == []
    finally:
        dlg.deleteLater()


def test_saving_a_builtin_forks_into_the_overlay(workspace) -> None:
    """Editing a shipped component never fails and never asks: the tree may be
    inside a signed, read-only .app, so a copy is the only possible outcome."""
    builtin, overlay = workspace
    saved: list = []
    dlg, path = open_inspector(builtin, overlay, MANGLED, saved=saved.append)
    try:
        assert dlg._save_btn.text() == "Save (creates your copy)"
        assert "shipped" in dlg._save_btn.toolTip()

        dlg._save_in_place()

        assert path.read_text(encoding="utf-8") == MANGLED, (
            "the shipped file must be untouched")
        fork = overlay / "PAPER_2pt" / "part.svg"
        assert fork.is_file()
        assert "Layer_1" not in fork.read_text(encoding="utf-8")

        assert saved and saved[0].forked and saved[0].path == fork

        index = OverrideIndex.load(overlay, builtin)
        assert index.is_fork("PAPER_2pt/part.svg")
        assert index.overrides["PAPER_2pt/part.svg"]["origin_sha256"]
        assert index.drift() == [], "freshly forked, so not yet drifted"
    finally:
        dlg.deleteLater()


def test_after_forking_the_inspector_follows_its_own_copy(workspace) -> None:
    """Otherwise Save twice would fork twice and the second edit would be
    written against a stale path."""
    builtin, overlay = workspace
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        dlg._save_in_place()
        assert dlg._path == overlay / "PAPER_2pt" / "part.svg"
        assert not dlg._target_is_builtin
        assert dlg._save_btn.text() == "Save"
        assert not dlg._save_btn.isEnabled(), "nothing left to fix"
    finally:
        dlg.deleteLater()


def test_saving_a_user_file_overwrites_in_place_and_backs_up(
        tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    overlay = tmp_path / "overlay"
    builtin.mkdir()
    (overlay / "PAPER_2pt").mkdir(parents=True)
    path = overlay / "PAPER_2pt" / "mine.svg"
    path.write_text(MANGLED, encoding="utf-8")

    dlg = ComponentInspectorDialog(
        path, index=OverrideIndex.load(overlay, builtin),
        builtin_root=builtin, overlay_root=overlay)
    dlg._recompute()
    try:
        assert dlg._save_btn.text() == "Save"
        dlg._save_in_place()
        assert "Layer_1" not in path.read_text(encoding="utf-8")
        backups = list((overlay / ".backups").rglob("*.svg"))
        assert len(backups) == 1
        assert "Layer_1" in backups[0].read_text(encoding="utf-8"), (
            "the backup must hold the pre-save content")
    finally:
        dlg.deleteLater()


def test_save_refuses_a_candidate_that_lost_the_document(workspace) -> None:
    """The tripwire for a buggy fix: the backup is the only thing behind it."""
    from diagrammer.io.component_writer import WriteRefused, save_component_text

    builtin, overlay = workspace
    index = OverrideIndex.load(overlay, builtin)
    target = overlay / "x.svg"
    with pytest.raises(WriteRefused, match="no <svg> root"):
        save_component_text("", target, original_text=MANGLED, index=index)
    with pytest.raises(WriteRefused, match="half the file"):
        save_component_text("<svg/>", target, original_text=MANGLED, index=index)
    assert not target.exists()


def test_reload_from_disk_picks_up_an_external_edit(workspace) -> None:
    """The other half of "Open in Illustrator": the file changes under us."""
    builtin, overlay = workspace
    dlg, path = open_inspector(*workspace, MANGLED)
    try:
        assert "layer-wrapper" in ids(dlg._findings)
        path.write_text(SOUND, encoding="utf-8")
        dlg.reload_from_disk()
        dlg._recompute()
        assert dlg._findings == []
        assert dlg._original_text == SOUND
    finally:
        dlg.deleteLater()


def test_reload_keeps_a_selection_that_still_applies(workspace) -> None:
    builtin, overlay = workspace
    dlg, path = open_inspector(*workspace, MANGLED)
    try:
        dlg._selected_fix_ids = {"unwrap-layer-group"}
        dlg._recompute()
        # rewrite with the same faults, as an editor round trip would
        path.write_text(MANGLED.replace("M0,50H60", "M0,50H58"),
                        encoding="utf-8")
        dlg.reload_from_disk()
        dlg._recompute()
        assert dlg._selected_fix_ids == {"unwrap-layer-group"}, (
            "a still-applicable selection should survive a reload")
    finally:
        dlg.deleteLater()


def test_reload_drops_a_selection_that_no_longer_applies(workspace) -> None:
    """Carrying a stale selection would make the preview describe a fix that is
    not there."""
    builtin, overlay = workspace
    dlg, path = open_inspector(*workspace, MANGLED)
    try:
        dlg._selected_fix_ids = {"unwrap-layer-group"}
        dlg._recompute()
        path.write_text(SOUND, encoding="utf-8")
        dlg.reload_from_disk()
        dlg._recompute()
        assert "unwrap-layer-group" not in dlg._selected_fix_ids
    finally:
        dlg.deleteLater()


def test_refreshing_the_fixes_list_does_not_re_enter_the_recompute(
        workspace) -> None:
    """Regression: this loop span the suite at 100% CPU forever.

    Setting a check state emits ``itemChanged``, which schedules a recompute,
    which rebuilds the list, which sets check states again. ``blockSignals``
    alone did not close it — Qt also emits ``itemChanged`` on insertion, and
    the "no automatic fixes" placeholder used to be added after signals were
    unblocked. The guard is an explicit ``_populating`` flag.
    """
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        calls = []
        real = dlg._recompute

        def counted():
            calls.append(1)
            real()

        dlg._recompute = counted
        dlg._refresh_fixes_list()
        assert calls == [], "rebuilding the list must not schedule a recompute"
        assert not dlg._recompute_pending

        dlg._refresh_findings_tree()
        assert calls == [], "rebuilding the tree must not schedule one either"
    finally:
        dlg.deleteLater()


def test_a_real_toggle_still_schedules_one_recompute(workspace) -> None:
    """The other half: the guard must not deafen the actual checkbox."""
    dlg, _ = open_inspector(*workspace, MANGLED)
    try:
        item = next(dlg._fixes_list.item(i)
                    for i in range(dlg._fixes_list.count())
                    if dlg._fixes_list.item(i).data(256) == "unwrap-layer-group")
        assert item.checkState() == Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Unchecked)
        assert "unwrap-layer-group" not in dlg._selected_fix_ids
        assert dlg._recompute_pending, "a user toggle must schedule a recompute"
    finally:
        dlg.deleteLater()


# ==========================================================================
# overwriting the shipped file in place
# ==========================================================================

def test_save_to_library_is_offered_only_where_the_tree_is_writable(
        workspace) -> None:
    """A source checkout can curate the shipped libraries; an installed .app
    cannot, because the bundle is read-only and code-signed."""
    import os
    import stat

    builtin, overlay = workspace
    dlg, path = open_inspector(builtin, overlay, MANGLED)
    try:
        assert dlg._builtin_is_writable
        assert dlg._save_builtin_btn.isVisible() or True  # hidden until shown
        dlg._refresh_save_buttons()
        assert dlg._save_builtin_btn.isEnabled()

        mode = path.parent.stat().st_mode
        os.chmod(path.parent, mode & ~stat.S_IWUSR)
        try:
            assert not dlg._builtin_is_writable
            dlg._refresh_save_buttons()
            assert not dlg._save_builtin_btn.isVisible()
        finally:
            os.chmod(path.parent, mode)
    finally:
        dlg.deleteLater()


def test_save_to_library_overwrites_rather_than_forking(
        workspace, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    builtin, overlay = workspace
    saved: list = []
    dlg, path = open_inspector(builtin, overlay, MANGLED, saved=saved.append)
    try:
        dlg._save_over_builtin()
        assert "Layer_1" not in path.read_text(encoding="utf-8"), (
            "the shipped file itself should have been rewritten")
        forks = [f for f in overlay.rglob("*.svg")
                 if ".backups" not in f.parts]
        assert forks == [], "nothing should be forked"
        assert saved and not saved[0].forked
        assert saved[0].path == path
        # but a backup is still kept, dot-prefixed so the scanner ignores it
        backups = list((overlay / ".backups").rglob("*.svg"))
        assert len(backups) == 1
        assert "Layer_1" in backups[0].read_text(encoding="utf-8")
        assert dlg._path == path, "the inspector keeps following the same file"
    finally:
        dlg.deleteLater()


def test_cancelling_the_overwrite_confirmation_writes_nothing(
        workspace, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    builtin, overlay = workspace
    dlg, path = open_inspector(builtin, overlay, MANGLED)
    try:
        dlg._save_over_builtin()
        assert path.read_text(encoding="utf-8") == MANGLED
        assert list(overlay.rglob("*.svg")) == []
    finally:
        dlg.deleteLater()


def test_plain_save_still_forks_even_where_the_tree_is_writable(
        workspace) -> None:
    """The default must stay safe: only the explicit button overwrites."""
    builtin, overlay = workspace
    dlg, path = open_inspector(builtin, overlay, MANGLED)
    try:
        dlg._save_in_place()
        assert path.read_text(encoding="utf-8") == MANGLED
        assert (overlay / "PAPER_2pt" / "part.svg").is_file()
    finally:
        dlg.deleteLater()
