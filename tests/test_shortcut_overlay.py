"""Tests for the floating, context-sensitive keyboard-shortcut hint overlay.

Requires a working Qt platform; skips where the GUI can't start (run under
QT_QPA_PLATFORM=offscreen to include). The app settings file is redirected to a
tmp path so a real user's ~/.diagrammer/settings.json is never touched.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def main_window(tmp_path):
    """A MainWindow with settings redirected to tmp; cleaned up after use."""
    from diagrammer.panels import settings_dialog as sd

    settings_file = tmp_path / "settings.json"
    with mock.patch.object(sd, "_SETTINGS_FILE", settings_file):
        from diagrammer.main_window import MainWindow

        win = MainWindow()
        win.resize(1000, 700)
        # Reset the shared settings singleton so state doesn't leak between tests
        sd.app_settings.show_shortcut_overlay = False
        win._shortcut_overlay_on = False
        win._update_shortcut_overlay()
        try:
            yield win
        finally:
            # Don't call win.close(): MainWindow.closeEvent prompts to save a
            # dirty diagram, which would block forever under offscreen Qt. Just
            # reset shared state and hide; the window is GC'd after the session.
            sd.app_settings.show_shortcut_overlay = False
            win.hide()


def test_overlay_created_over_viewport_and_hidden_by_default(main_window):
    ov = main_window._shortcut_overlay
    assert ov is not None
    # Parented to the view's viewport so it never lands in scene exports.
    assert ov.parentWidget() is main_window._view.viewport()
    assert ov not in main_window._scene.items()
    assert main_window._shortcut_overlay_on is False
    assert ov.isHidden()
    assert main_window._shortcut_hints_act.isCheckable()
    assert not main_window._shortcut_hints_act.isChecked()


def test_toggle_shows_and_hides_session_only(main_window):
    """'?' toggles for the session without changing the persistent default."""
    from diagrammer.panels import settings_dialog as sd

    assert sd.app_settings.show_shortcut_overlay is False

    main_window._toggle_shortcut_overlay()
    assert main_window._shortcut_overlay_on is True
    # Own visibility flag (window isn't shown in the fixture, so isVisible()
    # would be False regardless); the overlay called show() on itself.
    assert not main_window._shortcut_overlay.isHidden()
    assert main_window._shortcut_hints_act.isChecked()
    # The session toggle must NOT mutate the persisted default.
    assert sd.app_settings.show_shortcut_overlay is False

    main_window._toggle_shortcut_overlay()
    assert main_window._shortcut_overlay_on is False
    assert main_window._shortcut_overlay.isHidden()


def test_apply_settings_resyncs_to_default(main_window):
    """Accepting Settings re-applies the persisted default visibility."""
    from diagrammer.panels import settings_dialog as sd

    # Simulate turning the default on in Settings, then applying.
    sd.app_settings.show_shortcut_overlay = True
    main_window._apply_shortcut_overlay_settings()
    assert main_window._shortcut_overlay_on is True
    assert main_window._shortcut_hints_act.isChecked()
    assert not main_window._shortcut_overlay.isHidden()


def test_discoverability_label_tracks_toggle_key(main_window):
    """The persistent under-canvas hint shows the live overlay toggle key."""
    from diagrammer import shortcuts

    main_window._refresh_overlay_hint_label()
    assert main_window._overlay_hint_label.text() == (
        "? to toggle keyboard shortcut overlay")

    shortcuts.get_shortcut("overlay.toggle").set_override("Ctrl+Shift+K")
    try:
        main_window._refresh_overlay_hint_label()
        assert "Ctrl+Shift+K" in main_window._overlay_hint_label.text()
    finally:
        shortcuts.get_shortcut("overlay.toggle").reset()


def test_toggle_shortcut_is_registered_as_question_mark():
    from diagrammer import shortcuts

    sc = shortcuts.get_shortcut("overlay.toggle")
    assert sc.display_text == "?"
    assert sc.category == "Help"


def test_context_empty_selection(main_window):
    main_window._scene.clearSelection()
    assert main_window._shortcut_context() == "none"


def test_context_tracks_selected_item_types(main_window):
    from diagrammer.items.annotation_item import AnnotationItem
    from diagrammer.items.shape_item import ShapeItem

    main_window._add_shape("rectangle")
    main_window._add_annotation()

    shapes = [i for i in main_window._scene.items() if isinstance(i, ShapeItem)]
    annots = [i for i in main_window._scene.items() if isinstance(i, AnnotationItem)]
    assert shapes and annots

    main_window._scene.clearSelection()
    shapes[0].setSelected(True)
    assert main_window._shortcut_context() == "component"

    main_window._scene.clearSelection()
    annots[0].setSelected(True)
    assert main_window._shortcut_context() == "annotation"


@pytest.mark.parametrize("context", ["none", "component", "wire", "annotation"])
def test_every_context_yields_rows_with_nonempty_keys(main_window, context):
    rows = main_window._shortcut_hint_rows(context)
    assert rows, f"expected hint rows for context {context!r}"
    assert all(keys for keys, _label in rows)


def test_hint_keys_resolve_live_after_rebind(main_window):
    from diagrammer import shortcuts

    shortcuts.get_shortcut("edit.flip_h").set_override("Ctrl+Alt+M")
    try:
        rows = dict((label, keys) for keys, label in
                    main_window._shortcut_hint_rows("component"))
        assert rows["Flip horizontal"] == "Ctrl+Alt+M"
    finally:
        shortcuts.get_shortcut("edit.flip_h").reset()


def test_none_context_has_connect_mode_and_escape(main_window):
    labels = {label for _keys, label in main_window._shortcut_hint_rows("none")}
    assert "Connect / wire mode" in labels
    assert "Exit to select mode" in labels
    rows = dict((label, keys) for keys, label in
                main_window._shortcut_hint_rows("none"))
    assert rows["Connect / wire mode"] == "W"
    assert rows["Exit to select mode"] == "Esc"


def test_component_context_distinguishes_90_and_fine_rotation(main_window):
    rows = dict((label, keys) for keys, label in
                main_window._shortcut_hint_rows("component"))
    # 90° rotation on Space / Shift+Space
    assert rows["Rotate CCW 90°"] == "Space"
    assert rows["Rotate CW 90°"] == "Shift+Space"
    # Fine 15° rotation on R / Shift+R
    assert rows["Fine rotate CCW 15°"] == "R"
    assert rows["Fine rotate CW 15°"] == "Shift+R"
