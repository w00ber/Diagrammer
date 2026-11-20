"""macOS app-menu slots must go to the actions meant for them.

Qt's default ``TextHeuristicRole`` moves any action whose text looks like
"settings", "preferences", "about" or "quit" into the macOS application menu,
and the shortcut goes with it. The Help menu lists one action per help.md
chapter, and a chapter titled "Settings" took the Preferences slot: Cmd+, and
Diagrammer > Settings both opened Help.
"""

from __future__ import annotations

import re
from unittest import mock

import pytest
from PySide6.QtGui import QAction

# What Qt's heuristic matches (qcocoamenuloader / QPlatformMenuItem docs),
# with some margin.
_HEURISTIC = re.compile(r"setting|preference|option|setup|config|about|quit|exit",
                        re.IGNORECASE)


@pytest.fixture()
def main_window(tmp_path):
    from diagrammer.panels import settings_dialog as sd

    with mock.patch.object(sd, "_SETTINGS_FILE", tmp_path / "settings.json"):
        from diagrammer.main_window import MainWindow

        win = MainWindow()
        try:
            yield win
        finally:
            win.hide()


def _menu_actions(win) -> list[QAction]:
    out = []
    for top in win.menuBar().actions():
        menu = top.menu()
        if menu is not None:
            out += [a for a in menu.actions() if not a.isSeparator()]
    return out


def test_settings_owns_the_preferences_slot(main_window):
    prefs = [a for a in _menu_actions(main_window)
             if a.menuRole() == QAction.MenuRole.PreferencesRole]
    assert [a.text().replace("&", "") for a in prefs] == ["Settings…"]


def test_no_action_is_left_to_the_role_heuristic(main_window):
    """Anything whose text the heuristic would match must say its role."""
    loose = [a.text() for a in _menu_actions(main_window)
             if a.menuRole() == QAction.MenuRole.TextHeuristicRole
             and _HEURISTIC.search(a.text().replace("&", ""))]
    assert loose == []
