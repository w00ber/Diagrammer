"""Help chapters: the ## headings of a doc, in a sidebar and the Help menu.

The doc stays one scrolling page. Picking a chapter scrolls to it, the sidebar
follows the chapter being read, and a search dims chapters with no match.
"""

from __future__ import annotations

import pytest

from PySide6.QtCore import Qt

from diagrammer.panels.help_window import (
    SHORTCUTS_CHAPTER,
    HelpWindow,
    doc_chapters,
    find_doc,
    help_chapters,
)


@pytest.fixture
def win():
    w = HelpWindow(filename="help.md")
    w.resize(900, 700)
    w.show()
    yield w
    w.close()
    w.deleteLater()


def _row_name(w) -> str:
    return w.chapters()[w._chapter_list.currentRow()]


def test_doc_chapters_reads_level_two_headings_outside_code():
    md = "# T\n\n## One\ntext\n### sub\n```\n## not a heading\n```\n## Two\n"
    assert doc_chapters(md) == ["One", "Two"]


def test_help_is_a_handful_of_chapters_ending_in_shortcuts():
    chapters = help_chapters()
    # Regrouped from 23 sections; a long list defeats the purpose.
    assert 6 <= len(chapters) <= 14
    assert chapters[0] == "Getting Started"
    assert chapters[-1] == SHORTCUTS_CHAPTER
    assert len(set(chapters)) == len(chapters)


def test_menu_and_window_agree_on_the_chapters(win):
    # The menu reads help.md; the window reads the rendered page, which also
    # holds the generated shortcuts table. They must name the same chapters.
    assert win.chapters() == help_chapters()
    assert win._chapter_list.count() == len(help_chapters())


def test_go_to_chapter_scrolls_there_and_selects_it(win):
    bar = win._browser.verticalScrollBar()
    assert win.go_to_chapter("wiring")          # case-insensitive
    assert _row_name(win) == "Wiring"
    assert bar.value() > 0
    assert not win.go_to_chapter("No Such Chapter")


def test_sidebar_follows_scrolling(win):
    bar = win._browser.verticalScrollBar()
    bar.setValue(0)
    assert win._chapter_list.currentRow() == 0
    bar.setValue(bar.maximum())
    assert _row_name(win) == SHORTCUTS_CHAPTER
    win.go_to_chapter("Settings")
    assert _row_name(win) == "Settings"


def test_clicking_a_chapter_scrolls_to_it(win):
    bar = win._browser.verticalScrollBar()
    bar.setValue(0)
    row = win.chapters().index("Saving and Exporting")
    win._chapter_list.itemClicked.emit(win._chapter_list.item(row))
    assert bar.value() > 0
    assert _row_name(win) == "Saving and Exporting"


def test_search_dims_chapters_without_a_match(win):
    def dimmed():
        out = []
        for i in range(win._chapter_list.count()):
            item = win._chapter_list.item(i)
            if item.data(Qt.ItemDataRole.ForegroundRole) is not None:
                out.append(item.text())
        return out

    win._search.setText("junction")
    assert win._matches > 0
    assert "Wiring" not in dimmed()
    assert "Getting Started" in dimmed()

    win._search.setText("")
    assert dimmed() == []


def test_help_menu_lists_every_chapter_and_opens_it(tmp_path):
    from unittest import mock

    from diagrammer.panels import settings_dialog as sd

    with mock.patch.object(sd, "_SETTINGS_FILE", tmp_path / "settings.json"):
        from diagrammer.main_window import MainWindow
        mw = MainWindow()
    try:
        from PySide6.QtWidgets import QMenu
        help_menu = next(m for m in mw.menuBar().findChildren(QMenu)
                         if m.title().replace("&", "") == "Help")
        titles = [a.text() for a in help_menu.actions()]
        for chapter in help_chapters():
            assert chapter in titles

        act = next(a for a in help_menu.actions() if a.text() == "Wiring")
        act.trigger()
        w = HelpWindow._instances["help.md"]
        assert w.isVisible()
        assert _row_name(w) == "Wiring"
        w.close()
    finally:
        mw.hide()


def test_tutorial_gets_its_own_chapters():
    t = HelpWindow(filename="tutorial.md", show_shortcuts=False)
    try:
        md = find_doc("tutorial.md").read_text(encoding="utf-8")
        assert t.chapters() == doc_chapters(md)
        assert len(t.chapters()) > 1
    finally:
        t.deleteLater()
