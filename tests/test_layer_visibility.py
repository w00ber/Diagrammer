"""Hiding a layer must be undoable, visible as a warning, and complete.

Regression cover for a session where a stray bare "H" hid the active layer:
the canvas went blank except for one wire end dot, Ctrl+Z did nothing, and
the file had not been saved. Three separate defects made that read as data
loss — a non-undoable mutation, a bare-key binding, and junctions ignoring
layer visibility — so each is pinned here.
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
        win.resize(900, 600)
        try:
            yield win
        finally:
            # Don't close(): closeEvent prompts to save a dirty diagram,
            # which blocks forever under offscreen Qt.
            win.hide()


def _add_shape(scene):
    """Put one real item on the active layer and return it."""
    from PySide6.QtCore import QRectF

    from diagrammer.items.shape_item import RectangleItem

    item = RectangleItem(QRectF(0, 0, 40, 20))
    scene.addItem(item)
    scene.assign_active_layer(item)
    scene.apply_layer_state()
    return item


# --------------------------------------------------------------- undoability

def test_hiding_the_active_layer_is_undoable(main_window):
    scene = main_window._scene
    item = _add_shape(scene)
    assert item.isVisible()

    main_window._toggle_active_layer_visible()
    assert scene.layer_manager.active_layer.visible is False
    assert not item.isVisible()
    assert scene.undo_stack.canUndo()

    scene.undo_stack.undo()
    assert scene.layer_manager.active_layer.visible is True
    assert item.isVisible()


def test_locking_the_active_layer_is_undoable(main_window):
    scene = main_window._scene
    item = _add_shape(scene)

    main_window._toggle_active_layer_lock()
    assert scene.layer_manager.active_layer.locked is True
    assert not item.flags() & item.GraphicsItemFlag.ItemIsSelectable

    scene.undo_stack.undo()
    assert scene.layer_manager.active_layer.locked is False
    assert item.flags() & item.GraphicsItemFlag.ItemIsSelectable


def test_panel_toggle_is_undoable(main_window):
    """The Layers panel's own eye toggle goes through the undo stack too."""
    scene = main_window._scene
    item = _add_shape(scene)
    panel = main_window._layers_panel

    panel._set_layer_state(0, visible=False)
    assert not item.isVisible()

    scene.undo_stack.undo()
    assert item.isVisible()


def test_command_text_names_the_layer(main_window):
    from diagrammer.commands.layer_command import SetLayerStateCommand

    lm = main_window._scene.layer_manager
    cmd = SetLayerStateCommand(lm, 0, visible=False)
    assert cmd.text() == 'Hide layer "Default"'


def test_command_requires_a_flag(main_window):
    from diagrammer.commands.layer_command import SetLayerStateCommand

    with pytest.raises(ValueError):
        SetLayerStateCommand(main_window._scene.layer_manager, 0)


def test_undo_survives_the_layer_being_removed(main_window):
    """Layer removal isn't undoable; undoing past it must not raise."""
    from diagrammer.commands.layer_command import SetLayerStateCommand

    scene = main_window._scene
    lm = scene.layer_manager
    lm.add_layer("Second")
    scene.undo_stack.push(SetLayerStateCommand(lm, 1, visible=False))
    lm.remove_layer(1)
    scene.undo_stack.undo()  # no IndexError


# ------------------------------------------------------- junction visibility

def test_junctions_follow_layer_visibility(scene):
    """A hidden layer must take its wire end dots with it."""
    from diagrammer.items.junction_item import JunctionItem

    junction = JunctionItem()
    scene.addItem(junction)
    scene.assign_active_layer(junction)
    junction.end_marker = "filled"
    scene.apply_layer_state()
    assert junction.isVisible()

    scene.layer_manager.active_layer.visible = False
    scene.apply_layer_state()
    assert not junction.isVisible()

    scene.layer_manager.active_layer.visible = True
    scene.apply_layer_state()
    assert junction.isVisible()


def test_locking_a_layer_locks_its_junctions_too(scene):
    """A junction is an item like any other: a locked layer makes it
    unselectable, and unlocking gives that back. It is never
    ItemIsMovable — the view moves it explicitly, as it does components."""
    from PySide6.QtWidgets import QGraphicsItem
    from diagrammer.items.junction_item import JunctionItem

    selectable = QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    movable = QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    junction = JunctionItem()
    scene.addItem(junction)
    scene.assign_active_layer(junction)
    assert junction.flags() & selectable

    scene.layer_manager.active_layer.locked = True
    scene.apply_layer_state()
    assert not (junction.flags() & selectable)
    assert not (junction.flags() & movable)

    scene.layer_manager.active_layer.locked = False
    scene.apply_layer_state()
    assert junction.flags() & selectable


# ------------------------------------------------------------- the shortcuts

def test_layer_toggles_need_a_modifier():
    """No bare letter may hide or lock a layer."""
    from diagrammer.shortcuts import SHORTCUTS, get_shortcut

    assert "edit.hide_layer" not in SHORTCUTS
    assert "edit.lock_layer" not in SHORTCUTS
    for action_id in ("edit.toggle_layer_visible", "edit.toggle_layer_lock"):
        seq = get_shortcut(action_id).key_sequence.toString()
        assert seq.startswith("Shift+"), (action_id, seq)


def test_no_shortcut_conflicts():
    """Freeing H/L must not have collided the toggles with anything."""
    from diagrammer.shortcuts import find_conflicts

    assert find_conflicts() == {}


# ----------------------------------------------------------- standing warning

def test_status_bar_warns_while_a_layer_is_hidden(main_window):
    label = main_window._layer_warning_label
    assert label.text() == ""

    main_window._toggle_active_layer_visible()
    assert "1 layer hidden" in label.text()

    main_window._scene.undo_stack.undo()
    assert label.text() == ""


def test_status_bar_warns_while_a_layer_is_locked(main_window):
    label = main_window._layer_warning_label

    main_window._toggle_active_layer_lock()
    assert "locked" in label.text()

    main_window._scene.undo_stack.undo()
    assert label.text() == ""


def test_warning_names_the_live_show_key(main_window):
    from diagrammer.shortcuts import get_shortcut

    main_window._toggle_active_layer_visible()
    keys = get_shortcut("edit.toggle_layer_visible").display_text
    assert keys and keys in main_window._layer_warning_label.text()
