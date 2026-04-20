"""Tests for undo/redo correctness across command types."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.canvas.scene import DiagramScene
from diagrammer.commands.add_command import AddComponentCommand, MoveComponentCommand
from diagrammer.commands.delete_command import DeleteCommand
from diagrammer.commands.shape_command import AddShapeCommand
from diagrammer.items.junction_item import JunctionItem
from diagrammer.items.shape_item import RectangleItem
from diagrammer.models.library import ComponentLibrary


class TestAddShapeUndo:
    def test_add_undo_removes_item(self, scene):
        rect = RectangleItem(width=100, height=60)
        cmd = AddShapeCommand(scene, rect, QPointF(50, 50))
        scene.undo_stack.push(cmd)

        rects = [i for i in scene.items() if isinstance(i, RectangleItem)]
        assert len(rects) == 1

        scene.undo_stack.undo()
        rects = [i for i in scene.items() if isinstance(i, RectangleItem)]
        assert len(rects) == 0

    def test_add_redo_restores_item(self, scene):
        rect = RectangleItem(width=100, height=60)
        cmd = AddShapeCommand(scene, rect, QPointF(50, 50))
        scene.undo_stack.push(cmd)
        scene.undo_stack.undo()
        scene.undo_stack.redo()

        rects = [i for i in scene.items() if isinstance(i, RectangleItem)]
        assert len(rects) == 1


class TestDeleteUndo:
    def test_delete_undo_restores_items(self, scene):
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)

        cmd = DeleteCommand(scene, [rect])
        scene.undo_stack.push(cmd)

        rects = [i for i in scene.items() if isinstance(i, RectangleItem)]
        assert len(rects) == 0

        scene.undo_stack.undo()
        rects = [i for i in scene.items() if isinstance(i, RectangleItem)]
        assert len(rects) == 1
        assert rects[0].pos().x() == pytest.approx(50)

    def test_delete_multiple_undo(self, scene):
        items = []
        for i in range(3):
            rect = RectangleItem(width=50, height=30)
            rect.setPos(QPointF(i * 100, 0))
            scene.addItem(rect)
            items.append(rect)

        cmd = DeleteCommand(scene, items)
        scene.undo_stack.push(cmd)

        rects = [i for i in scene.items() if isinstance(i, RectangleItem)]
        assert len(rects) == 0

        scene.undo_stack.undo()
        rects = [i for i in scene.items() if isinstance(i, RectangleItem)]
        assert len(rects) == 3


class TestMoveUndo:
    def test_move_undo_restores_position(self, scene):
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)

        # MoveComponentCommand takes (item, old_pos, new_pos)
        old_pos = QPointF(50, 50)
        new_pos = QPointF(200, 300)
        cmd = MoveComponentCommand(rect, old_pos, new_pos)
        scene.undo_stack.push(cmd)  # push calls redo(), which sets pos to new_pos

        assert rect.pos().x() == pytest.approx(200)

        scene.undo_stack.undo()
        assert rect.pos().x() == pytest.approx(50)
        assert rect.pos().y() == pytest.approx(50)

    def test_move_redo(self, scene):
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)

        old_pos = QPointF(50, 50)
        new_pos = QPointF(200, 300)
        cmd = MoveComponentCommand(rect, old_pos, new_pos)
        scene.undo_stack.push(cmd)

        scene.undo_stack.undo()
        scene.undo_stack.redo()
        assert rect.pos().x() == pytest.approx(200)
        assert rect.pos().y() == pytest.approx(300)


class TestUndoStackIntegrity:
    def test_multiple_operations_undo_in_order(self, scene):
        # Add three shapes, then undo them in reverse order
        rects = []
        for i in range(3):
            rect = RectangleItem(width=50, height=30)
            cmd = AddShapeCommand(scene, rect, QPointF(i * 100, 0))
            scene.undo_stack.push(cmd)
            rects.append(rect)

        assert len([i for i in scene.items() if isinstance(i, RectangleItem)]) == 3

        scene.undo_stack.undo()
        assert len([i for i in scene.items() if isinstance(i, RectangleItem)]) == 2

        scene.undo_stack.undo()
        assert len([i for i in scene.items() if isinstance(i, RectangleItem)]) == 1

        scene.undo_stack.undo()
        assert len([i for i in scene.items() if isinstance(i, RectangleItem)]) == 0

    def test_undo_past_beginning_is_noop(self, scene):
        rect = RectangleItem(width=100, height=60)
        cmd = AddShapeCommand(scene, rect, QPointF(50, 50))
        scene.undo_stack.push(cmd)
        scene.undo_stack.undo()
        scene.undo_stack.undo()  # should not crash
        assert len([i for i in scene.items() if isinstance(i, RectangleItem)]) == 0
