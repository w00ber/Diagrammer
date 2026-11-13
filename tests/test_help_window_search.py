"""The Help window's search bar.

Regression for two bugs that together made search look broken: typing a term
searched from wherever the cursor had been left — after clearing highlights,
the end of the document — so a present term reported "No matches"; and Return
in the search field did nothing, so only the Next button found anything.
"""

from __future__ import annotations

import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from diagrammer.panels.help_window import HelpWindow


@pytest.fixture
def win():
    w = HelpWindow(filename="help.md", show_shortcuts=False)
    w.show()
    yield w
    w.close()
    w.deleteLater()


def _pos(w) -> int:
    return w._browser.textCursor().selectionStart()


def test_typing_finds_a_term_even_with_the_cursor_at_the_end(win):
    cursor = win._browser.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    win._browser.setTextCursor(cursor)

    win._search.setText("Diagrammer")

    assert win._browser.textCursor().selectedText().lower() == "diagrammer"
    assert "match" in win._match_label.text()
    assert win._match_label.text() != "No matches"


def test_typing_jumps_to_the_first_match(win):
    win._search.setText("Diagrammer")
    first = _pos(win)
    doc_text = win._browser.document().toPlainText().lower()
    assert first == doc_text.index("diagrammer")


def test_return_goes_to_the_next_match_and_shift_return_back(win):
    win._search.setText("Diagrammer")
    first = _pos(win)

    QTest.keyClick(win._search, Qt.Key.Key_Return)
    second = _pos(win)
    assert second > first

    QTest.keyClick(win._search, Qt.Key.Key_Return,
                   Qt.KeyboardModifier.ShiftModifier)
    assert _pos(win) == first


def test_next_wraps_from_the_last_match_to_the_first(win):
    win._search.setText("Diagrammer")
    first = _pos(win)
    n = win._matches
    assert n >= 2
    for _ in range(n):
        win._search_next()
    assert _pos(win) == first


def test_all_matches_are_highlighted_without_editing_the_document(win):
    doc = win._browser.document()
    before = doc.toHtml()

    win._search.setText("Diagrammer")
    assert len(win._browser.extraSelections()) == win._matches >= 2

    win._search.setText("")
    assert win._browser.extraSelections() == []
    assert win._match_label.text() == ""
    # Highlighting is an overlay: the document's own formatting is untouched.
    assert doc.toHtml() == before


def test_absent_term_says_no_matches(win):
    win._search.setText("zzqx-not-in-help")
    assert win._match_label.text() == "No matches"
    assert win._browser.extraSelections() == []
