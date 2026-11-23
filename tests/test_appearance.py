"""Canvas colours, interface theme and polarity: three independent settings,
one source of truth, and every control showing the same state.

The bug this pins: canvas colours used to be stored per interface theme and
the Settings theme radios switched between those sets, so the way back to
your own background after experimenting was "System, then Light". Polarity
is drawn on top of the canvas colours and must never rewrite them.
"""

from __future__ import annotations

import json

import pytest

MINE = {"background": "#fff8e7", "grid": "#e0d0b0", "grid_major": "#c0a080"}


# ==========================================================================
# the settings model
# ==========================================================================

def _load(tmp_path, monkeypatch, data: dict):
    from diagrammer.panels import settings_dialog as sd
    f = tmp_path / "s.json"
    f.write_text(json.dumps(data))
    monkeypatch.setattr(sd, "_SETTINGS_FILE", f)
    sd.app_settings.reset_all()
    sd.app_settings.load()
    return sd.app_settings


def test_old_per_theme_colours_become_the_custom_set(tmp_path, monkeypatch):
    s = _load(tmp_path, monkeypatch, {
        "theme": "light",
        "theme_colors": {"light": MINE,
                         "system": {"background": "#123456"}}})
    assert s.canvas_colors == "custom"
    assert s.custom_canvas_colors == MINE
    assert s.current_background_color().name() == "#fff8e7"


def test_untouched_old_colours_migrate_to_default(tmp_path, monkeypatch):
    s = _load(tmp_path, monkeypatch, {
        "theme": "dark",
        "theme_colors": {"dark": {"background": "#ffffff", "grid": "#dcdcdc",
                                  "grid_major": "#bebebe"}}})
    assert s.canvas_colors == "default"


def test_canvas_colours_survive_save_and_load(tmp_path, monkeypatch):
    from diagrammer.panels import settings_dialog as sd
    s = sd.app_settings
    s.canvas_colors = "custom"
    s.custom_canvas_colors = dict(MINE)
    s.invert_polarity = True
    s.save()
    again = _load(tmp_path, monkeypatch,
                  json.loads(sd._SETTINGS_FILE.read_text()))
    assert (again.canvas_colors, again.custom_canvas_colors,
            again.invert_polarity) == ("custom", MINE, True)


def test_default_ignores_custom_colours_but_keeps_them(isolate_app_settings):
    s = isolate_app_settings
    s.custom_canvas_colors = dict(MINE)
    s.canvas_colors = "default"
    assert s.current_background_color().name() == "#ffffff"
    s.canvas_colors = "custom"
    assert s.current_background_color().name() == "#fff8e7"


def test_theme_no_longer_changes_canvas_colours(isolate_app_settings):
    s = isolate_app_settings
    s.canvas_colors = "custom"
    s.custom_canvas_colors = dict(MINE)
    for theme in ("system", "light", "dark"):
        s.theme = theme
        assert s.current_background_color().name() == "#fff8e7"


# ==========================================================================
# the window: every control, one state
# ==========================================================================

@pytest.fixture
def win(isolate_component_overlay, isolate_app_settings, _ensure_qapp):
    from diagrammer.main_window import MainWindow
    isolate_app_settings.canvas_colors = "custom"
    isolate_app_settings.custom_canvas_colors = dict(MINE)
    w = MainWindow()
    w._apply_appearance()
    yield w
    w.hide()
    w.deleteLater()


def _state(w):
    from diagrammer.panels.settings_dialog import app_settings
    return {
        "bg": w._view.backgroundBrush().color().name(),
        "inverted": app_settings.invert_polarity,
        "canvas": app_settings.canvas_colors,
        "menu_polarity": w._polarity_act.isChecked(),
        "menu_canvas": [k for k, a in w._canvas_actions.items() if a.isChecked()],
    }


def test_polarity_off_restores_the_custom_background_exactly(win):
    start = _state(win)
    assert start["bg"] == "#fff8e7"
    win._polarity_act.setChecked(True)
    assert _state(win)["bg"] != "#fff8e7"
    win._polarity_act.setChecked(False)
    assert _state(win) == start


def test_appearance_menu_switches_canvas_colours_and_tracks(win):
    win._canvas_actions["default"].trigger()
    assert _state(win)["bg"] == "#ffffff"
    assert _state(win)["menu_canvas"] == ["default"]
    win._canvas_actions["custom"].trigger()
    assert _state(win)["bg"] == "#fff8e7"


def test_polarity_action_is_shared_by_view_and_appearance_menus(win):
    """One QAction in both menus, so the two check marks cannot disagree."""
    from PySide6.QtWidgets import QMenu
    titles = {m.title().replace("&", "")
              for m in win._polarity_act.associatedObjects()
              if isinstance(m, QMenu)}
    assert {"View", "Appearance"} <= titles


def test_settings_previews_live_and_cancel_restores(win):
    from diagrammer.panels.settings_dialog import SettingsDialog, app_settings
    start = _state(win)
    dlg = SettingsDialog(app_settings, win, library=win._library)
    dlg._polarity_cb.setChecked(True)
    dlg._canvas_radios["default"].setChecked(True)
    preview = _state(win)
    assert preview["bg"] == "#000000"                  # default, inverted
    assert preview["menu_polarity"] and preview["menu_canvas"] == ["default"]
    dlg.reject()
    assert _state(win) == start


def test_settings_ok_sticks_and_reopens_showing_it(win):
    from diagrammer.panels.settings_dialog import SettingsDialog, app_settings
    dlg = SettingsDialog(app_settings, win, library=win._library)
    dlg._polarity_cb.setChecked(True)
    dlg.accept()
    dlg.apply()
    win._apply_appearance()
    assert _state(win)["inverted"] and _state(win)["menu_polarity"]

    again = SettingsDialog(app_settings, win, library=win._library)
    assert again._polarity_cb.isChecked()
    assert again._canvas_radios["custom"].isChecked()
    again.reject()
    assert _state(win)["inverted"], "cancelling an unchanged dialog keeps it"


def test_menu_toggle_is_reflected_in_a_newly_opened_settings(win):
    from diagrammer.panels.settings_dialog import SettingsDialog, app_settings
    win._polarity_act.setChecked(True)
    dlg = SettingsDialog(app_settings, win, library=win._library)
    assert dlg._polarity_cb.isChecked()
    dlg.reject()


def test_library_thumbnails_follow_polarity(win):
    """The panel previews what will land on the canvas."""
    from PySide6.QtGui import QImage

    from diagrammer.panels.library_panel import _make_icon
    from diagrammer.utils import polarity

    cdef = win._library.get("PAPER_2pt/circuits/LCR/CAP1")

    def ink(icon):
        img = icon.pixmap(64, 64).toImage().convertToFormat(
            QImage.Format.Format_ARGB32)
        dark = light = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if c.alpha() > 128:
                    dark += c.lightness() < 60
                    light += c.lightness() > 200
        return dark, light

    n_dark, n_light = ink(_make_icon(cdef))
    polarity.set_inverted(True)
    f_dark, f_light = ink(_make_icon(cdef))
    assert n_dark > n_light and f_light > f_dark
