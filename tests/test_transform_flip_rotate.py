"""Regression tests for combined flip + rotate behaviour.

A component (or shape) that is mirrored and then rotated should rotate
the *visible* shape — i.e. its ports/corners should land where you'd
expect after applying flip-then-rotate to the unflipped reference.
Before the fix the internal ``_rotation_angle`` was simply increased,
which (because flip is composed *after* the rotation in the transform
stack) produced the opposite visual rotation.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

from diagrammer.commands.transform_command import (
    FlipItemCommand,
    RotateItemCommand,
)
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.shape_item import RectangleItem
from diagrammer.models.library import ComponentLibrary


def _approx_eq(a: QPointF, b: QPointF, tol: float = 1e-3) -> bool:
    return math.hypot(a.x() - b.x(), a.y() - b.y()) < tol


@pytest.fixture
def asym_component_def(library):
    """Pick any 3-port component (asymmetric port layout)."""
    for comp in library._by_key.values():
        if len(comp.ports) >= 3:
            return comp
    pytest.skip("No 3-port component available in library")


def _expected_flip_then_rotate_ports(comp_def, horizontal: bool, degrees: float):
    """Compute expected scene positions for ports after visually flipping
    and then rotating around the component centre."""
    cx, cy = comp_def.width / 2, comp_def.height / 2
    t = QTransform()
    t.translate(cx, cy)
    t.rotate(degrees)
    if horizontal:
        t.scale(-1, 1)
    else:
        t.scale(1, -1)
    t.translate(-cx, -cy)
    return [t.map(QPointF(p.x, p.y)) for p in comp_def.ports]


class TestComponentFlipRotate:
    def test_h_flip_then_rotate_90_matches_visual_expectation(self, asym_component_def, scene):
        item = ComponentItem(asym_component_def)
        scene.addItem(item)
        item.setPos(0, 0)

        item.set_flip_h(True)
        item.rotate_by(90)  # User intent: visually rotate 90 CW

        expected = _expected_flip_then_rotate_ports(asym_component_def, horizontal=True, degrees=90)
        for port, exp in zip(item.ports, expected):
            assert _approx_eq(port.scene_center(), exp), (
                f"Port {port.port_name} at {port.scene_center()} != expected {exp}"
            )

    def test_v_flip_then_rotate_90_matches_visual_expectation(self, asym_component_def, scene):
        item = ComponentItem(asym_component_def)
        scene.addItem(item)
        item.setPos(0, 0)

        item.set_flip_v(True)
        item.rotate_by(90)

        expected = _expected_flip_then_rotate_ports(asym_component_def, horizontal=False, degrees=90)
        for port, exp in zip(item.ports, expected):
            assert _approx_eq(port.scene_center(), exp)

    def test_both_flips_then_rotate_uses_normal_direction(self, asym_component_def, scene):
        """flip_h + flip_v == 180 deg rotation, which commutes with rotation
        — so degrees should NOT be negated when both flips are set."""
        item = ComponentItem(asym_component_def)
        scene.addItem(item)
        item.setPos(0, 0)

        item.set_flip_h(True)
        item.set_flip_v(True)
        item.rotate_by(90)

        # Equivalent to a fresh item rotated 270 (because both flips ≡ 180,
        # then +90 visual = 270 in raw terms with no flip)
        ref = ComponentItem(asym_component_def)
        scene.addItem(ref)
        ref.setPos(0, 0)
        ref.rotate_by(270)

        for p_item, p_ref in zip(item.ports, ref.ports):
            assert _approx_eq(p_item.scene_center(), p_ref.scene_center())

    def test_unflipped_rotation_unaffected(self, asym_component_def, scene):
        item = ComponentItem(asym_component_def)
        scene.addItem(item)
        item.setPos(0, 0)
        item.rotate_by(90)
        assert item.rotation_angle == 90.0

    def test_rotate_undo_is_inverse_when_flipped(self, asym_component_def, scene):
        item = ComponentItem(asym_component_def)
        scene.addItem(item)
        item.setPos(0, 0)
        item.set_flip_h(True)

        before = [QPointF(p.scene_center()) for p in item.ports]
        item.rotate_by(90)
        item.rotate_by(-90)
        after = [QPointF(p.scene_center()) for p in item.ports]

        for a, b in zip(before, after):
            assert _approx_eq(a, b)


class TestShapeFlipRotate:
    def test_rotate_command_after_flip_command_visual_match(self, scene):
        rect = RectangleItem(width=100, height=40)
        rect.setPos(0, 0)
        scene.addItem(rect)

        # Flip horizontally
        flip_cmd = FlipItemCommand(rect, horizontal=True)
        scene.undo_stack.push(flip_cmd)
        # Rotate 90 CW
        rot_cmd = RotateItemCommand(rect, 90.0)
        scene.undo_stack.push(rot_cmd)

        # Reference shape: visually flipped then rotated using a manual transform
        ref = RectangleItem(width=100, height=40)
        ref.setPos(0, 0)
        scene.addItem(ref)
        cx, cy = ref.boundingRect().center().x(), ref.boundingRect().center().y()
        manual = QTransform()
        manual.translate(cx, cy)
        manual.rotate(90)
        manual.scale(-1, 1)
        manual.translate(-cx, -cy)
        ref.setTransform(manual)

        # Compare a few corner points mapped to scene
        br = rect.boundingRect()
        corners = [br.topLeft(), br.topRight(), br.bottomRight(), br.bottomLeft()]
        for c in corners:
            actual = rect.mapToScene(c)
            expected = ref.mapToScene(c)
            assert _approx_eq(actual, expected), (
                f"corner {c} actual={actual} expected={expected}"
            )

    def test_rotate_command_undo_inverse_when_flipped(self, scene):
        rect = RectangleItem(width=100, height=40)
        rect.setPos(0, 0)
        scene.addItem(rect)
        flip_cmd = FlipItemCommand(rect, horizontal=True)
        scene.undo_stack.push(flip_cmd)

        before = rect.transform()
        before_rot = rect.rotation()

        rot_cmd = RotateItemCommand(rect, 45.0)
        scene.undo_stack.push(rot_cmd)
        scene.undo_stack.undo()

        assert rect.transform() == before
        assert rect.rotation() == pytest.approx(before_rot)
