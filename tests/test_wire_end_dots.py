"""Tests for wire end dots: the D / Shift+D cycle key, the Ctrl+Alt+click
gesture (and its hit radius versus direction arrows), and their wiring
into the menu, shortcut registry, and cheat-sheet overlay."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_shortcut_overlay import main_window  # noqa: E402,F401  (fixture)


def _add_junction(scene, x, y):
    from diagrammer.items.junction_item import JunctionItem

    j = JunctionItem()
    j.setPos(QPointF(x, y))
    j.setVisible(False)
    scene.addItem(j)
    return j


def _connect(scene, port_a, port_b):
    from diagrammer.commands.connect_command import CreateConnectionCommand

    cmd = CreateConnectionCommand(scene, port_a, port_b)
    scene.undo_stack.push(cmd)
    return cmd.connection


@pytest.fixture
def wired(scene):
    """A view plus a straight wire between two free ends, (0,0)–(200,0)."""
    from diagrammer.canvas.view import DiagramView

    view = DiagramView(scene)
    view.resize(400, 400)
    view.setSceneRect(-100, -200, 400, 400)
    a = _add_junction(scene, 0, 0)
    b = _add_junction(scene, 200, 0)
    conn = _connect(scene, a.port, b.port)
    scene.update_connections()
    yield view, conn, a, b
    # Destroy the view now rather than whenever GC gets to it: a failing
    # test's traceback keeps it alive, and a late collection (e.g. while
    # a later test builds a MainWindow) segfaults.
    import shiboken6
    view.setScene(None)
    shiboken6.delete(view)


def _ctrl_alt_press(view, scene_pos: QPointF):
    from PySide6.QtGui import QMouseEvent

    vp = QPointF(view.mapFromScene(scene_pos))
    view.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, vp, view.viewport().mapToGlobal(vp),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
    ))


def _put_cursor(monkeypatch, view, scene_pos: QPointF | None):
    """Place the cursor over *scene_pos* (None: off the canvas).

    Patches the view instance, not ``QCursor.pos``: monkeypatching a
    Shiboken type corrupts teardown and segfaults during GC.
    """
    monkeypatch.setattr(view, "_cursor_scene_pos", lambda: scene_pos)


class TestCycle:
    def test_forward_cycle_order(self, wired):
        view, _conn, a, _b = wired
        seen = []
        for _ in range(3):
            view._cycle_end_dots([a], 1)
            seen.append(a.end_marker)
        assert seen == ["filled", "open", "none"]

    def test_reverse_cycle_order(self, wired):
        view, _conn, a, _b = wired
        view._cycle_end_dots([a], -1)
        assert a.end_marker == "open"

    def test_mixed_ends_sync_to_first(self, wired):
        view, _conn, a, b = wired
        a.end_marker = "filled"
        view._cycle_end_dots([a, b], 1)
        assert (a.end_marker, b.end_marker) == ("open", "open")

    def test_one_undo_step_restores_all(self, wired, scene):
        view, _conn, a, b = wired
        view._cycle_end_dots([a, b], 1)
        assert (a.end_marker, b.end_marker) == ("filled", "filled")
        scene.undo_stack.undo()
        assert (a.end_marker, b.end_marker) == ("none", "none")


class TestKeyTargeting:
    def test_hovered_free_end_wins_over_selection(self, wired, monkeypatch):
        view, conn, a, b = wired
        conn.setSelected(True)
        _put_cursor(monkeypatch, view, QPointF(3, 2))
        view.cycle_end_dots(1)
        assert (a.end_marker, b.end_marker) == ("filled", "none")

    def test_falls_back_to_selected_wires_free_ends(self, wired, monkeypatch):
        view, conn, a, b = wired
        conn.setSelected(True)
        _put_cursor(monkeypatch, view, QPointF(100, 0))  # mid-wire, no end
        view.cycle_end_dots(1)
        assert (a.end_marker, b.end_marker) == ("filled", "filled")

    def test_cursor_off_view_uses_selection(self, wired, monkeypatch):
        view, conn, a, b = wired
        conn.setSelected(True)
        _put_cursor(monkeypatch, view, None)
        view.cycle_end_dots(-1)
        assert (a.end_marker, b.end_marker) == ("open", "open")

    def test_nothing_targeted_is_a_no_op(self, wired, scene, monkeypatch):
        view, _conn, a, b = wired
        _put_cursor(monkeypatch, view, None)
        count = scene.undo_stack.count()
        view.cycle_end_dots(1)
        assert (a.end_marker, b.end_marker) == ("none", "none")
        assert scene.undo_stack.count() == count

    def test_tee_junction_is_not_a_free_end(self, wired, scene):
        view, _conn, _a, b = wired
        c = _add_junction(scene, 200, 100)
        _connect(scene, b.port, c.port)  # b now carries two wires
        scene.update_connections()
        assert view._free_end_at(QPointF(200, 0)) is None
        assert view._free_end_at(QPointF(200, 100)) is c


class TestGesture:
    def test_ctrl_alt_click_at_free_end_cycles_dot(self, wired):
        view, conn, a, _b = wired
        _ctrl_alt_press(view, QPointF(2, 1))
        assert a.end_marker == "filled"
        assert conn.arrows == []

    def test_ctrl_alt_click_mid_wire_adds_arrow(self, wired):
        view, conn, a, b = wired
        _ctrl_alt_press(view, QPointF(100, 0))
        assert len(conn.arrows) == 1
        assert (a.end_marker, b.end_marker) == ("none", "none")

    def test_hit_radius_separates_dot_from_arrow(self, wired):
        """Just inside FREE_END_PICK_PX (on screen) → dot; just outside
        but still on the wire → arrow."""
        from diagrammer.canvas.view import FREE_END_PICK_PX

        view, conn, _a, b = wired
        px = view.pick_tolerance(1.0)  # scene units per screen pixel
        _ctrl_alt_press(view, QPointF(200 - (FREE_END_PICK_PX - 2) * px, 0))
        assert b.end_marker == "filled" and conn.arrows == []
        _ctrl_alt_press(view, QPointF(200 - (FREE_END_PICK_PX + 2) * px, 0))
        assert b.end_marker == "filled" and len(conn.arrows) == 1


class TestRegistryAndOverlay:
    def test_default_bindings_without_conflicts(self):
        from diagrammer import shortcuts

        assert shortcuts.get_shortcut("edit.cycle_end_dot").display_text == "D"
        assert shortcuts.get_shortcut(
            "edit.cycle_end_dot_back").display_text == "Shift+D"
        assert shortcuts.find_conflicts() == {}

    def test_wire_hints_cover_arrows_and_end_dots(self, main_window):
        rows = dict((label, keys) for keys, label in
                    main_window._shortcut_hint_rows("wire"))
        assert rows["Cycle end dot / reverse"] == "D / Shift+D"
        assert rows["Add arrow (at a free end: dot)"] == "Ctrl+Alt+click"
        assert rows["Flip arrow"] == "Double-click"
        assert rows["Delete arrow"] == "Ctrl+Shift+click"

    def test_menu_action_cycles_selected_wire(self, main_window, monkeypatch):
        from PySide6.QtGui import QAction, QKeySequence

        scene = main_window._scene
        a = _add_junction(scene, 0, 0)
        b = _add_junction(scene, 200, 0)
        conn = _connect(scene, a.port, b.port)
        scene.clearSelection()
        conn.setSelected(True)
        _put_cursor(monkeypatch, main_window._view, None)
        act = next(x for x in main_window.findChildren(QAction)
                   if x.shortcut() == QKeySequence("D"))
        act.trigger()
        assert (a.end_marker, b.end_marker) == ("filled", "filled")
