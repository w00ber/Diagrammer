"""Move, remove and revert from the library context menu.

The menu composition tests matter as much as the operations: a shipped
component must never offer a delete (its file lives inside the application, and
on macOS inside a signed bundle), and a user's own must never be limited to a
tombstone, because tombstoning everything just accumulates junk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from diagrammer.panels import library_actions as la
from diagrammer.panels.library_panel import (
    build_category_menu,
    build_component_menu,
)

KEY = "flowchart/decision"
REL = "flowchart/decision.svg"


@pytest.fixture
def win(isolate_component_overlay, _ensure_qapp):
    """A real MainWindow with its overlay redirected to a tmp dir."""
    from diagrammer.main_window import MainWindow

    w = MainWindow()
    yield w
    w.close()
    w.deleteLater()


def entries(menu) -> list[str]:
    return [a.text() for a in menu.actions() if a.text()]


# ==========================================================================
# menu composition
# ==========================================================================

def test_builtin_offers_hide_and_never_a_delete(win) -> None:
    comp = win._library.get(KEY)
    assert comp.is_builtin
    cm = build_component_menu(win, comp, False, panel=win._library_panel,
                              key=KEY)
    labels = entries(cm.menu)
    assert "Hide from Library…" in labels
    assert "Remove…" not in labels
    assert cm.revert_act is None, "a built-in has nothing to revert to"
    assert cm.inspect_act is not None


def test_a_user_component_offers_a_real_remove(win, tmp_path: Path) -> None:
    """Respecting the user's agency over their own files."""
    custom = tmp_path / "MyLib"
    custom.mkdir()
    (custom / "widget.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<g id="ports"><circle id="port:a" cx="0" cy="20" r="3"/></g></svg>',
        encoding="utf-8")
    win._library.scan(custom, source_kind="custom")
    comp = win._library.get("MyLib/widget")
    assert comp.is_user_owned

    cm = build_component_menu(win, comp, False, panel=win._library_panel,
                              key="MyLib/widget")
    labels = entries(cm.menu)
    assert "Remove…" in labels
    assert "Hide from Library…" not in labels


def test_the_menu_degrades_without_a_window(_ensure_qapp, tmp_path) -> None:
    """A panel built in isolation must still produce a usable menu."""
    from diagrammer.models.component_def import ComponentDef
    from diagrammer.models.library import ComponentLibrary
    from diagrammer.panels.library_panel import LibraryPanel

    svg = tmp_path / "w.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
                   '<g id="ports"><circle id="port:a" cx="0" cy="20" r="3"/>'
                   "</g></svg>", encoding="utf-8")
    panel = LibraryPanel(ComponentLibrary())
    assert panel.component_actions is None
    cm = build_component_menu(panel, ComponentDef.from_svg(svg), False,
                              panel=panel)
    labels = entries(cm.menu)
    assert "☆ Add to Favorites" in labels
    assert "Move to Category…" not in labels, "no actions, no management entries"
    assert cm.handle(cm.move_act) is False


def test_category_menu_gains_validate_only_with_actions(win) -> None:
    cm = build_category_menu(win, "flowchart", win._library,
                             panel=win._library_panel)
    labels = entries(cm.menu)
    assert "Validate This Category…" in labels
    assert "View as Table" in labels
    assert "↑ Move Up" in labels


def test_all_three_views_build_the_same_component_menu(win) -> None:
    """One factory feeds the List tree, the Grid thumbnails and the Table view,
    so an entry added once appears in all three."""
    from diagrammer.panels.library_panel import _DragThumbnail

    comp = win._library.get(KEY)
    tree = build_component_menu(win, comp, False, panel=win._library_panel,
                                key=KEY)
    thumb = _DragThumbnail(comp, win._library_panel)
    grid = thumb.build_menu()
    assert entries(tree.menu) == entries(grid.menu)


# ==========================================================================
# hide / restore
# ==========================================================================

def test_hiding_a_builtin_is_reversible_and_spares_the_file(
        win, monkeypatch) -> None:
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    shipped = win._builtin_components_path / REL
    comp = win._library.get(KEY)

    win._component_actions.remove(comp)
    assert win._library.get(KEY) is None
    assert win._overrides.is_hidden("builtin", REL)
    assert shipped.is_file(), "the shipped file must never be touched"

    win._overrides.unhide("builtin", REL)
    win._rebuild_library()
    assert win._library.get(KEY) is not None


def test_a_hidden_builtin_survives_a_restart(win, monkeypatch) -> None:
    """The tombstone is on disk, so it has to be applied at load."""
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win._component_actions.remove(win._library.get(KEY))

    from diagrammer.main_window import MainWindow
    second = MainWindow()
    try:
        assert second._overrides.is_hidden("builtin", REL)
        assert second._library.get(KEY) is None
    finally:
        second.close()
        second.deleteLater()


# ==========================================================================
# move
# ==========================================================================

def _accept_move(monkeypatch, category: str) -> None:
    monkeypatch.setattr(la._MoveDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(la._MoveDialog, "category", lambda self: category)


def test_moving_a_builtin_forks_and_tombstones_in_one_index_write(
        win, monkeypatch) -> None:
    """The composition that forces forks and tombstones into the same file: an
    index that recorded the fork but lost the tombstone would show the
    component in both categories at once."""
    _accept_move(monkeypatch, "flowchart/mine")
    saves: list[int] = []
    real_save = win._overrides.save
    monkeypatch.setattr(win._overrides, "save",
                        lambda *a, **k: (saves.append(1), real_save(*a, **k))[1])

    win._component_actions.move_to_category(win._library.get(KEY))

    assert len(saves) == 1, "both halves must land in one atomic save"
    assert win._overrides.is_fork("flowchart/mine/decision.svg")
    assert win._overrides.is_hidden("builtin", REL)
    assert (win._overlay_root / "flowchart/mine/decision.svg").is_file()
    assert (win._builtin_components_path / REL).is_file()

    assert win._library.get(KEY) is None, "the shipped one is hidden"
    moved = win._library.get("flowchart/mine/decision")
    assert moved is not None and moved.source_kind == "overlay"


def test_moving_a_user_file_actually_moves_it(win, monkeypatch) -> None:
    _accept_move(monkeypatch, "flowchart/mine")
    win._component_actions.move_to_category(win._library.get(KEY))
    first = win._overlay_root / "flowchart/mine/decision.svg"
    assert first.is_file()

    _accept_move(monkeypatch, "flowchart/elsewhere")
    win._component_actions.move_to_category(
        win._library.get("flowchart/mine/decision"))

    second = win._overlay_root / "flowchart/elsewhere/decision.svg"
    assert second.is_file()
    assert not first.exists(), "a user file is moved, not copied"
    assert win._overrides.is_fork("flowchart/elsewhere/decision.svg"), (
        "the index key must follow the file")
    assert "flowchart/mine/decision.svg" not in win._overrides.overrides


def test_moving_onto_an_existing_name_is_refused(win, monkeypatch) -> None:
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(a[2])))
    _accept_move(monkeypatch, "flowchart/mine")
    win._component_actions.move_to_category(win._library.get(KEY))
    target = win._overlay_root / "flowchart/mine/decision.svg"
    body = target.read_bytes()

    # a second component of the same name already in the destination
    other = win._overlay_root / "flowchart/other"
    other.mkdir(parents=True)
    (other / "decision.svg").write_bytes(body)
    win._rebuild_library()

    _accept_move(monkeypatch, "flowchart/mine")
    win._component_actions.move_to_category(
        win._library.get("flowchart/other/decision"))
    assert warned, "the user must be told rather than silently losing a file"
    assert (other / "decision.svg").is_file()
    assert target.read_bytes() == body, "the destination must not be clobbered"


def test_cancelling_the_move_dialog_changes_nothing(win, monkeypatch) -> None:
    monkeypatch.setattr(la._MoveDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)
    win._component_actions.move_to_category(win._library.get(KEY))
    assert win._overrides.overrides == {}
    assert win._overrides.hidden == {}
    assert win._library.get(KEY) is not None


def test_moving_to_the_same_category_is_a_noop(win, monkeypatch) -> None:
    _accept_move(monkeypatch, "flowchart")
    win._component_actions.move_to_category(win._library.get(KEY))
    assert win._overrides.overrides == {}


# ==========================================================================
# delete / revert
# ==========================================================================

def test_removing_a_user_component_can_trash_it(win, monkeypatch,
                                                tmp_path: Path) -> None:
    _accept_move(monkeypatch, "flowchart/mine")
    win._component_actions.move_to_category(win._library.get(KEY))
    mine = win._overlay_root / "flowchart/mine/decision.svg"
    assert mine.is_file()

    landed = tmp_path / "trash" / "decision.svg"
    landed.parent.mkdir()
    monkeypatch.setattr(la, "QMessageBox", QMessageBox)
    monkeypatch.setattr("diagrammer.io.trash.move_to_trash",
                        lambda p: (landed.write_bytes(Path(p).read_bytes()),
                                   Path(p).unlink(), landed)[2])

    win._component_actions._trash(win._library.get("flowchart/mine/decision"))

    assert not mine.exists()
    assert landed.is_file(), "the content went to the Trash, not nowhere"
    assert win._overrides.deleted[-1]["rel"] == "flowchart/mine/decision.svg"
    assert win._overrides.deleted[-1]["trash"] == str(landed)
    assert "flowchart/mine/decision.svg" not in win._overrides.overrides


def test_trashing_also_clears_any_tombstone_for_the_same_file(
        win, monkeypatch, tmp_path: Path) -> None:
    """A tombstone for a file that no longer exists is exactly the junk the
    user asked not to accumulate."""
    _accept_move(monkeypatch, "flowchart/mine")
    win._component_actions.move_to_category(win._library.get(KEY))
    comp = win._library.get("flowchart/mine/decision")
    win._overrides.hide("custom", str(comp.svg_path))

    landed = tmp_path / "t.svg"
    monkeypatch.setattr("diagrammer.io.trash.move_to_trash",
                        lambda p: (Path(p).unlink(), landed)[1])
    win._component_actions._trash(comp)
    assert not win._overrides.is_hidden("custom", str(comp.svg_path))


def test_a_failed_trash_leaves_the_file_alone(win, monkeypatch) -> None:
    _accept_move(monkeypatch, "flowchart/mine")
    win._component_actions.move_to_category(win._library.get(KEY))
    comp = win._library.get("flowchart/mine/decision")
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr("diagrammer.io.trash.move_to_trash",
                        lambda p: (_ for _ in ()).throw(OSError("no trash")))

    win._component_actions._trash(comp)
    assert Path(comp.svg_path).is_file()
    assert not win._overrides.deleted


def test_can_revert_only_for_a_fork(win, monkeypatch) -> None:
    assert not win._component_actions.can_revert(win._library.get(KEY))
    _accept_move(monkeypatch, "flowchart/mine")
    win._component_actions.move_to_category(win._library.get(KEY))
    assert win._component_actions.can_revert(
        win._library.get("flowchart/mine/decision"))


# ==========================================================================
# the canvas route into the inspector
# ==========================================================================

def _place(win, key: str = KEY):
    """Put one component on the canvas and select it."""
    from diagrammer.items.component_item import ComponentItem

    item = ComponentItem(win._library.get(key))
    win._scene.addItem(item)
    item.setSelected(True)
    return item


def test_a_selected_canvas_component_is_what_inspect_acts_on(win) -> None:
    """Regression: this read ``comp_def``, which is the *library table* item's
    attribute — the canvas item exposes ``component_def``. Opening the Library
    menu with exactly one component selected raised every time."""
    item = _place(win)
    comp = win._selected_component_def()
    assert comp is not None
    assert comp is item.component_def
    assert comp.name == "decision"


def test_opening_the_library_menu_with_a_selection_does_not_raise(win) -> None:
    """``aboutToShow`` is the path the user actually hit."""
    _place(win)
    win._sync_library_menu()
    assert win._inspect_component_act.isEnabled()
    assert "decision" in win._inspect_component_act.text()


def test_the_menu_falls_back_to_the_panel_and_then_to_nothing(win) -> None:
    assert win._selected_component_def() is None
    win._sync_library_menu()
    assert not win._inspect_component_act.isEnabled()
    assert win._inspect_component_act.text().endswith("Component…")


def test_two_selected_components_are_ambiguous(win) -> None:
    """Inspect acts on one file, so a multi-selection is not a target."""
    _place(win)
    _place(win)
    assert win._selected_component_def() is None
    win._sync_library_menu()
    assert not win._inspect_component_act.isEnabled()


def test_inspect_from_the_canvas_opens_the_library_file(win) -> None:
    item = _place(win)
    dlg = win.open_component_inspector(item.component_def.svg_path)
    try:
        assert dlg is not None
        assert dlg._path == item.component_def.svg_path
    finally:
        dlg.close()
