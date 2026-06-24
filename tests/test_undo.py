"""Tests for undo/redo correctness across command types."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.canvas.scene import DiagramScene
from diagrammer.commands.add_command import AddComponentCommand, MoveComponentCommand
from diagrammer.commands.annotation_command import EditAnnotationTextCommand
from diagrammer.commands.delete_command import DeleteCommand
from diagrammer.commands.layer_command import ChangeZOrderCommand
from diagrammer.commands.shape_command import (
    AddShapeCommand,
    MoveLineEndpointCommand,
    ResizeShapeCommand,
)
from diagrammer.items.annotation_item import AnnotationItem
from diagrammer.items.junction_item import JunctionItem
from diagrammer.items.shape_item import LineItem, RectangleItem
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


class TestResizeShapeUndo:
    def test_resize_undo_restores_size_and_pos(self, scene):
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)

        cmd = ResizeShapeCommand(
            rect, 100, 60, QPointF(50, 50), 200, 120, QPointF(30, 20),
        )
        scene.undo_stack.push(cmd)  # push() runs redo()
        assert rect.shape_width == pytest.approx(200)
        assert rect.shape_height == pytest.approx(120)
        assert rect.pos().x() == pytest.approx(30)

        scene.undo_stack.undo()
        assert rect.shape_width == pytest.approx(100)
        assert rect.shape_height == pytest.approx(60)
        assert rect.pos().x() == pytest.approx(50)
        assert rect.pos().y() == pytest.approx(50)

    def test_resize_redo(self, scene):
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)

        cmd = ResizeShapeCommand(
            rect, 100, 60, QPointF(50, 50), 200, 120, QPointF(30, 20),
        )
        scene.undo_stack.push(cmd)
        scene.undo_stack.undo()
        scene.undo_stack.redo()
        assert rect.shape_width == pytest.approx(200)
        assert rect.shape_height == pytest.approx(120)
        assert rect.pos().x() == pytest.approx(30)


class TestMoveLineEndpointUndo:
    def test_endpoint_undo_restores_points(self, scene):
        line = LineItem(QPointF(0, 0), QPointF(100, 0))
        scene.addItem(line)

        cmd = MoveLineEndpointCommand(
            line, QPointF(0, 0), QPointF(100, 0), QPointF(0, 0), QPointF(150, 80),
        )
        scene.undo_stack.push(cmd)
        assert line._end.x() == pytest.approx(150)
        assert line._end.y() == pytest.approx(80)

        scene.undo_stack.undo()
        assert line._end.x() == pytest.approx(100)
        assert line._end.y() == pytest.approx(0)

    def test_endpoint_redo(self, scene):
        line = LineItem(QPointF(0, 0), QPointF(100, 0))
        scene.addItem(line)

        cmd = MoveLineEndpointCommand(
            line, QPointF(0, 0), QPointF(100, 0), QPointF(0, 0), QPointF(150, 80),
        )
        scene.undo_stack.push(cmd)
        scene.undo_stack.undo()
        scene.undo_stack.redo()
        assert line._end.x() == pytest.approx(150)
        assert line._end.y() == pytest.approx(80)


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


class TestAnnotationEditUndo:
    def test_text_edit_undo_restores_old_text(self, scene):
        annot = AnnotationItem("hello")
        scene.addItem(annot)
        cmd = EditAnnotationTextCommand(annot, "hello", "world")
        scene.undo_stack.push(cmd)
        assert annot.source_text == "world"
        scene.undo_stack.undo()
        assert annot.source_text == "hello"

    def test_text_edit_redo_restores_new_text(self, scene):
        annot = AnnotationItem("hello")
        scene.addItem(annot)
        cmd = EditAnnotationTextCommand(annot, "hello", "world")
        scene.undo_stack.push(cmd)
        scene.undo_stack.undo()
        scene.undo_stack.redo()
        assert annot.source_text == "world"

    def test_finish_editing_pushes_undo_command(self, scene):
        """The end-to-end edit path pushes EditAnnotationTextCommand
        when text changes — Ctrl+Z reverts the edit."""
        annot = AnnotationItem("hello")
        scene.addItem(annot)
        # Simulate the user entering edit mode and typing.
        annot.start_editing()
        annot.setPlainText("world")
        annot.finish_editing()
        assert annot.source_text == "world"
        # Undo should restore the prior text.
        scene.undo_stack.undo()
        assert annot.source_text == "hello"

    def test_finish_editing_with_no_change_does_not_push(self, scene):
        """Opening and closing the editor without changing text must
        NOT clutter the undo stack."""
        annot = AnnotationItem("hello")
        scene.addItem(annot)
        before_count = scene.undo_stack.count()
        annot.start_editing()
        # No text change.
        annot.finish_editing()
        assert scene.undo_stack.count() == before_count
        assert annot.source_text == "hello"


class TestZOrderUndo:
    def test_change_z_order_undo_restores_old_values(self, scene):
        rects = []
        for i in range(3):
            r = RectangleItem(width=50, height=30)
            r.setPos(QPointF(i * 100, 0))
            r.setZValue(float(i + 1))
            scene.addItem(r)
            rects.append(r)
        # Move middle to front: z=2 -> z=4
        cmd = ChangeZOrderCommand([(rects[1], 2.0, 4.0)], "Bring to front")
        scene.undo_stack.push(cmd)
        assert rects[1].zValue() == pytest.approx(4.0)
        scene.undo_stack.undo()
        assert rects[1].zValue() == pytest.approx(2.0)

    def test_bulk_z_change_undo_restores_all(self, scene):
        rects = []
        for i in range(3):
            r = RectangleItem(width=50, height=30)
            r.setZValue(float(i + 1))
            scene.addItem(r)
            rects.append(r)
        # Reorder all three at once
        states = [
            (rects[0], 1.0, 5.0),
            (rects[1], 2.0, 6.0),
            (rects[2], 3.0, 7.0),
        ]
        cmd = ChangeZOrderCommand(states, "Bring to front")
        scene.undo_stack.push(cmd)
        assert [r.zValue() for r in rects] == [5.0, 6.0, 7.0]
        scene.undo_stack.undo()
        assert [r.zValue() for r in rects] == [1.0, 2.0, 3.0]


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
