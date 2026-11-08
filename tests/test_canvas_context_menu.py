"""Right-clicking the canvas keeps the selection the menu is about to act on.

No canvas item is ever ``ItemIsMovable`` (the view drags them itself), and
Qt's ``QGraphicsItem::mousePressEvent`` ignores a non-left press on a
non-movable item. The scene therefore saw every right-press as unhandled and
cleared the selection, so the context menu built a moment later had nothing to
offer — Group, Ungroup, Create Component from Selection and Delete were all
unreachable, and with nothing else under the cursor the menu did not open.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from diagrammer.commands.group_command import GroupCommand


@pytest.fixture
def win(isolate_component_overlay, _ensure_qapp):
    from diagrammer.main_window import MainWindow

    w = MainWindow()
    w.resize(1000, 700)
    w.show()
    QApplication.processEvents()
    yield w
    # Not close(): a dirty diagram would prompt and block under offscreen Qt.
    w._scene.undo_stack.clear()
    w.hide()
    w.deleteLater()


def _place(win, x, y):
    from diagrammer.commands.add_command import AddComponentCommand

    cdef = next(c for c in win._library.all_defs() if len(c.ports) >= 2)
    cmd = AddComponentCommand(win._scene, cdef, QPointF(x, y))
    win._scene.undo_stack.push(cmd)
    return cmd.item


def _viewport_point(win, item):
    return win._view.mapFromScene(item.sceneBoundingRect().center())


def _right_click(win, pos) -> list[str]:
    """Press, then deliver the context-menu event, as the platform does.

    Returns the menu's entries, or ``[]`` if no menu opened.
    """
    viewport = win._view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.RightButton,
                     Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(viewport, Qt.MouseButton.RightButton,
                       Qt.KeyboardModifier.NoModifier, pos)
    seen: list[str] = []

    def grab():
        menu = QApplication.activePopupWidget()
        if menu is not None:
            seen.extend(a.text() for a in menu.actions() if a.text())
            menu.close()

    QTimer.singleShot(50, grab)
    QApplication.sendEvent(viewport, QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, pos, viewport.mapToGlobal(pos)))
    return seen


def test_right_press_on_a_selected_item_keeps_the_whole_selection(win):
    a, b = _place(win, 0, 0), _place(win, 200, 0)
    a.setSelected(True)
    b.setSelected(True)
    win._view.centerOn(100, 0)

    entries = _right_click(win, _viewport_point(win, a))

    assert a.isSelected() and b.isSelected()
    assert "Group" in entries
    assert "Create Component from Selection..." in entries
    assert "Delete" in entries


def test_right_click_on_a_group_member_offers_ungroup_and_create_component(win):
    a, b = _place(win, 0, 0), _place(win, 200, 0)
    win._scene.undo_stack.push(GroupCommand([a, b]))
    win._scene.clearSelection()
    win._view.centerOn(100, 0)

    entries = _right_click(win, _viewport_point(win, a))

    # The click selected the whole group, as a left-click would.
    assert a.isSelected() and b.isSelected()
    assert "Ungroup" in entries
    assert "Create Component from Selection..." in entries


def test_right_click_on_an_unselected_item_selects_just_that_item(win):
    a, b, c = _place(win, 0, 0), _place(win, 200, 0), _place(win, 400, 0)
    a.setSelected(True)
    b.setSelected(True)
    win._view.centerOn(200, 0)

    _right_click(win, _viewport_point(win, c))

    assert c.isSelected()
    assert not a.isSelected() and not b.isSelected()


def test_right_click_on_empty_canvas_leaves_the_selection_alone(win):
    a, b = _place(win, 0, 0), _place(win, 200, 0)
    a.setSelected(True)
    b.setSelected(True)
    win._view.centerOn(100, 0)

    empty = win._view.mapFromScene(QPointF(100, 200))
    assert win._view._item_at_pos(QPointF(empty)) is None
    entries = _right_click(win, empty)

    assert a.isSelected() and b.isSelected()
    assert "Create Component from Selection..." in entries
