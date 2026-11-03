"""Tests for the library panel's "Open With" context-menu actions."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMenu

from diagrammer.models.component_def import ComponentDef
from diagrammer.models.library import ComponentLibrary
from diagrammer.panels import open_with
from diagrammer.panels.library_panel import (
    LibraryPanel,
    _DragThumbnail,
    build_component_menu,
)


@pytest.fixture
def svg_file(tmp_path) -> Path:
    path = tmp_path / "part.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<g id="artwork"><rect x="5" y="5" width="30" height="30"/></g>'
        '</svg>'
    )
    return path


def _action_texts(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _submenu(menu: QMenu, title: str) -> QMenu:
    for a in menu.actions():
        if a.menu() is not None and a.text() == title:
            return a.menu()
    raise AssertionError(f"no {title!r} submenu in {_action_texts(menu)}")


def test_display_name_strips_platform_suffixes():
    assert open_with.display_name("/Applications/Inkscape.app") == "Inkscape"
    assert open_with.display_name(r"C:\Program Files\x\inkscape.exe") == "inkscape"
    assert open_with.display_name("inkscape") == "inkscape"


def test_menu_lists_saved_editors_then_detected(svg_file, monkeypatch, isolate_app_settings):
    isolate_app_settings.external_editors = ["/Applications/Saved.app"]
    monkeypatch.setattr(open_with, "detect_apps",
                        lambda: ["/Applications/Saved.app", "/Applications/Found.app"])

    menu = QMenu()
    open_with.add_open_with_actions(menu, svg_file)

    assert _action_texts(menu)[0] == "Open in Default App"
    sub = _submenu(menu, "Open With")
    # A detected app already saved must not appear twice.
    assert _action_texts(sub) == ["Saved", "Found", "Choose Application\u2026"]


def test_menu_with_nothing_detected_still_offers_chooser(svg_file, monkeypatch):
    monkeypatch.setattr(open_with, "detect_apps", list)
    menu = QMenu()
    open_with.add_open_with_actions(menu, svg_file)
    assert _action_texts(_submenu(menu, "Open With")) == ["Choose Application\u2026"]


def test_triggering_an_entry_launches_and_remembers_it(svg_file, monkeypatch,
                                                       isolate_app_settings):
    monkeypatch.setattr(open_with, "detect_apps", lambda: ["/Applications/Found.app"])
    launched: list[tuple[Path, str]] = []
    monkeypatch.setattr(open_with, "open_with_app",
                        lambda path, app: launched.append((path, app)) or True)

    menu = QMenu()
    open_with.add_open_with_actions(menu, svg_file)
    _submenu(menu, "Open With").actions()[0].trigger()

    assert launched == [(svg_file, "/Applications/Found.app")]
    # Picking a detected app promotes it into the persisted list.
    assert isolate_app_settings.external_editors == ["/Applications/Found.app"]


def test_failed_launch_is_not_remembered(svg_file, monkeypatch, isolate_app_settings):
    monkeypatch.setattr(open_with, "detect_apps", lambda: ["/Applications/Gone.app"])
    monkeypatch.setattr(open_with, "open_with_app", lambda path, app: False)

    menu = QMenu()
    open_with.add_open_with_actions(menu, svg_file)
    _submenu(menu, "Open With").actions()[0].trigger()

    assert isolate_app_settings.external_editors == []


def test_default_app_entry_uses_default_opener(svg_file, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(open_with, "open_in_default_app",
                        lambda path: calls.append(path) or True)
    menu = QMenu()
    open_with.add_open_with_actions(menu, svg_file)
    menu.actions()[0].trigger()
    assert calls == [svg_file]


def test_remember_app_dedupes_and_caps(isolate_app_settings):
    for i in range(12):
        open_with.remember_app(f"/Applications/App{i}.app")
    open_with.remember_app("/Applications/App11.app")
    editors = isolate_app_settings.external_editors
    assert editors[0] == "/Applications/App11.app"
    assert len(editors) == len(set(editors)) == 10


def test_external_editors_round_trip_through_settings(isolate_app_settings):
    isolate_app_settings.external_editors = ["/Applications/Inkscape.app"]
    isolate_app_settings.save()
    isolate_app_settings.external_editors = []
    isolate_app_settings.load()
    assert isolate_app_settings.external_editors == ["/Applications/Inkscape.app"]


def test_grid_thumbnail_menu_offers_open_actions(svg_file, monkeypatch):
    """The grid view's tiles get the same component menu as the tree."""
    monkeypatch.setattr(open_with, "detect_apps", list)
    comp_def = ComponentDef.from_svg(svg_file)
    thumb = _DragThumbnail(comp_def, LibraryPanel(ComponentLibrary()))

    menu, fav_act, show_act = thumb.build_menu()
    texts = _action_texts(menu)
    assert "Open in Default App" in texts
    assert "Open With" in texts
    assert show_act is not None and show_act.text() == "Show in File Browser"
    assert "Favorites" in fav_act.text()


def test_grid_thumbnail_menu_hides_open_for_embedded(svg_file, monkeypatch):
    """An embedded compound has no file on disk, so there is nothing to open."""
    monkeypatch.setattr(open_with, "detect_apps", list)
    comp_def = ComponentDef.from_svg(svg_file)
    comp_def.is_embedded = True
    thumb = _DragThumbnail(comp_def, LibraryPanel(ComponentLibrary()))

    menu, fav_act, show_act = thumb.build_menu()
    texts = _action_texts(menu)
    assert "Open in Default App" not in texts
    assert "Open With" not in texts
    assert show_act is None
    assert fav_act is not None


def test_tree_and_grid_menus_match(svg_file, monkeypatch):
    """Both views build their component menu from the same helper."""
    monkeypatch.setattr(open_with, "detect_apps", lambda: ["/Applications/Found.app"])
    comp_def = ComponentDef.from_svg(svg_file)
    panel = LibraryPanel(ComponentLibrary())
    tree_menu, _, _ = build_component_menu(panel, comp_def, is_favorite=False)
    grid_menu, _, _ = _DragThumbnail(comp_def, panel).build_menu()
    assert _action_texts(tree_menu) == _action_texts(grid_menu)


def test_table_view_cell_menu_matches_panel_menu(svg_file, monkeypatch):
    """Right-clicking anywhere in a table cell offers the component menu."""
    monkeypatch.setattr(open_with, "detect_apps", lambda: ["/Applications/Found.app"])
    from diagrammer.panels.library_table_view import build_library_table

    comp_def = ComponentDef.from_svg(svg_file)
    scene, view = build_library_table("cat", {"cat": [comp_def]})
    view.resize(600, 400)
    view.fit_all()

    # Three items make up a cell (frame, label, artwork); each must resolve
    # to the same component so cell padding is right-clickable too.
    resolved = [view._comp_def_at(view.mapFromScene(item.sceneBoundingRect().center()))
                for item in scene.items() if item.sceneBoundingRect().isValid()]
    assert comp_def in resolved

    panel = LibraryPanel(ComponentLibrary())
    view.set_library_panel(panel)
    menu, fav_act, show_act = build_component_menu(view, comp_def, is_favorite=False)
    texts = _action_texts(menu)
    assert "Open in Default App" in texts and "Open With" in texts
    assert show_act is not None and fav_act is not None


def test_table_view_ignores_right_click_on_empty_space(svg_file, monkeypatch):
    monkeypatch.setattr(open_with, "detect_apps", list)
    from diagrammer.panels.library_table_view import build_library_table
    from PySide6.QtCore import QPoint

    comp_def = ComponentDef.from_svg(svg_file)
    scene, view = build_library_table("cat", {"cat": [comp_def]})
    view.resize(600, 400)
    # Far outside any cell: the scene rect is padded generously, so this
    # maps to blank canvas.
    far = view.mapFromScene(scene.sceneRect().topLeft() + QPointF(5, 5))
    assert view._comp_def_at(far) is None
    assert view._comp_def_at(QPoint(-50, -50)) is None
