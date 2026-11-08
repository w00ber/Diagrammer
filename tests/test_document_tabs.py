"""Several diagrams open at once, one per tab (document_tabs.py).

``_scene``/``_view``/``_current_file`` are properties resolving to the active
diagram, so these tests pin down what "active" means: it follows the current
tab, survives a library table being shown, and never becomes ``None``.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from diagrammer.io.serializer import DiagramSerializer


@pytest.fixture
def win(isolate_component_overlay, _ensure_qapp):
    from diagrammer.main_window import MainWindow

    w = MainWindow()
    w.resize(1000, 700)
    yield w
    # Not close(): a dirty diagram would prompt and block under offscreen Qt.
    for doc in w._documents():
        doc.scene.undo_stack.clear()
    w.hide()
    w.deleteLater()


def _place(win):
    from diagrammer.commands.add_command import AddComponentCommand

    cdef = next(c for c in win._library.all_defs() if len(c.ports) >= 2)
    cmd = AddComponentCommand(win._scene, cdef, QPointF(0, 0))
    win._scene.undo_stack.push(cmd)
    return cmd.item


def _saved_diagram(win, tmp_path: Path, name: str) -> str:
    """Write a one-component .dgm via a throwaway scene."""
    from diagrammer.canvas.scene import DiagramScene
    from diagrammer.commands.add_command import AddComponentCommand

    scene = DiagramScene(library=win._library)
    cdef = next(c for c in win._library.all_defs() if len(c.ports) >= 2)
    scene.undo_stack.push(AddComponentCommand(scene, cdef, QPointF(0, 0)))
    path = tmp_path / name
    DiagramSerializer.save(scene, str(path))
    return str(path)


def _tab_texts(win) -> list[str]:
    tabs = win._tab_widget
    return [tabs.tabText(i) for i in range(tabs.count())]


# ---------------------------------------------------------------- basics

def test_starts_with_one_diagram_and_no_tab_bar(win):
    assert len(win._documents()) == 1
    assert win._tab_widget.tabBar().isHidden()
    assert win._current_file is None


def test_new_opens_a_second_tab_and_the_canvas_follows_it(win):
    first = win._scene
    _place(win)

    win._file_new()

    assert len(win._documents()) == 2
    assert win._scene is not first
    assert not win._scene.items()
    assert first.items(), "the first diagram is untouched"
    assert not win._tab_widget.tabBar().isHidden()
    assert _tab_texts(win)[1] == "Untitled 2"


def test_undo_follows_the_active_tab(win):
    a = win._scene
    _place(win)
    win._file_new()
    b = win._scene

    assert win._undo_group.activeStack() is b.undo_stack
    assert not win._undo_group.canUndo()

    win._show_previous_tab()
    assert win._scene is a
    assert win._undo_group.canUndo()
    win._undo_group.undo()
    assert not a.items()


def test_layers_panel_and_overlay_follow_the_active_tab(win):
    win._file_new()
    doc = win._active_doc
    assert win._layers_panel._manager is doc.scene.layer_manager
    assert win._shortcut_overlay.parentWidget() is doc.view.viewport()


def test_dirty_diagram_is_marked_in_its_tab(win):
    win._file_new()
    _place(win)
    assert _tab_texts(win)[1].endswith("•")
    assert win.isWindowModified()


# ---------------------------------------------------------------- cycling

def test_cycling_wraps_in_both_directions(win):
    win._file_new()
    win._file_new()
    tabs = win._tab_widget
    assert tabs.currentIndex() == 2

    win._show_next_tab()
    assert tabs.currentIndex() == 0
    win._show_previous_tab()
    assert tabs.currentIndex() == 2


def test_cycling_includes_library_tables_without_moving_the_canvas(win):
    diagram = win._scene
    win._open_library_table("__all__")
    tabs = win._tab_widget
    assert tabs.count() == 2 and tabs.currentIndex() == 1

    # On the table, Edit/Draw still target the diagram; export/zoom the table.
    assert win._scene is diagram
    assert win._active_scene() is not diagram

    win._show_next_tab()
    assert tabs.currentIndex() == 0
    assert win._active_scene() is diagram


def test_cycle_shortcuts_are_the_platform_tab_chords(win):
    from diagrammer.shortcuts import find_conflicts, get_shortcut

    portable = QKeySequence.SequenceFormat.PortableText
    assert get_shortcut("view.next_tab").key_sequence.toString(portable) == "Ctrl+Shift+]"
    assert get_shortcut("view.prev_tab").key_sequence.toString(portable) == "Ctrl+Shift+["
    # They used to be Bring to Front / Send to Back, which moved.
    assert get_shortcut("edit.bring_front").key_sequence.toString(portable) == "Ctrl+Alt+]"
    assert get_shortcut("edit.send_back").key_sequence.toString(portable) == "Ctrl+Alt+["
    assert find_conflicts() == {}


# ---------------------------------------------------------------- opening

def test_open_reuses_a_blank_untitled_tab(win, tmp_path):
    path = _saved_diagram(win, tmp_path, "a.dgm")
    win._open_file(path)
    assert len(win._documents()) == 1
    assert win._current_file == path
    assert _tab_texts(win) == ["a.dgm"]


def test_open_adds_a_tab_when_the_current_one_has_work(win, tmp_path):
    _place(win)
    path = _saved_diagram(win, tmp_path, "a.dgm")
    win._open_file(path)
    assert len(win._documents()) == 2
    assert win._current_file == path


def test_opening_an_open_file_switches_to_it(win, tmp_path):
    a = _saved_diagram(win, tmp_path, "a.dgm")
    b = _saved_diagram(win, tmp_path, "b.dgm")
    win._open_file(a)
    win._open_file(b)
    assert len(win._documents()) == 2

    win._open_file(a)

    assert len(win._documents()) == 2
    assert win._current_file == a


def test_a_failed_open_leaves_no_stray_tab(win, tmp_path):
    _place(win)
    bad = tmp_path / "bad.dgm"
    bad.write_text("not json")
    with mock.patch.object(QMessageBox, "critical"):
        win._open_file(str(bad))
    assert len(win._documents()) == 1


# ---------------------------------------------------------------- closing

def test_closing_the_last_diagram_leaves_a_fresh_untitled(win):
    win.close_active_tab()
    assert len(win._documents()) == 1
    assert win._scene is not None and not win._scene.items()


def test_closing_a_dirty_diagram_asks_and_cancel_keeps_it(win):
    _place(win)
    win._file_new()
    win._show_previous_tab()
    dirty = win._active_doc

    with mock.patch.object(QMessageBox, "warning",
                           return_value=QMessageBox.StandardButton.Cancel) as ask:
        win.close_active_tab()
    assert ask.called
    assert dirty in win._documents()

    with mock.patch.object(QMessageBox, "warning",
                           return_value=QMessageBox.StandardButton.Discard):
        win.close_active_tab()
    assert dirty not in win._documents()
    assert len(win._documents()) == 1


def test_closing_a_diagram_from_a_table_keeps_the_table_showing(win):
    win._file_new()
    second = win._active_doc
    win._open_library_table("__all__")
    table = win._tab_widget.currentWidget()

    win._close_document(second)

    assert win._tab_widget.currentWidget() is table
    assert win._active_doc in win._documents()


def test_quit_prompts_for_every_unsaved_diagram(win):
    _place(win)
    win._file_new()
    _place(win)
    with mock.patch.object(QMessageBox, "warning",
                           return_value=QMessageBox.StandardButton.Discard) as ask:
        assert win._confirm_close_all()
    assert ask.call_count == 2


# ---------------------------------------------------------------- window-wide state

def test_view_toggles_reach_every_tab_and_new_ones(win):
    win._file_new()
    win._toggle_grid_act.setChecked(False)
    win._grid_spin.setValue(25.0)

    assert all(not d.view.grid_visible for d in win._documents())
    assert all(d.view.grid_spacing == 25.0 for d in win._documents())

    win._file_new()
    assert not win._view.grid_visible
    assert win._view.grid_spacing == 25.0


def test_hint_label_is_set_on_every_tab(win):
    win._file_new()
    win._refresh_overlay_hint_label()
    texts = {d.hint_label.text() for d in win._documents()}
    assert len(texts) == 1 and texts.pop()
    QApplication.processEvents()
