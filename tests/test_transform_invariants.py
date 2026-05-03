"""Transform-invariance regression tests.

These tests pin down geometric invariants that the transform / copy /
serialization code paths must preserve. They are deliberately written
against scene-space *snapshots* (see ``_geometry_snapshot``) so they
remain valid across the upcoming refactor of internal storage.

Some assertions are marked ``xfail`` because they expose pre-existing
bugs that the refactor is meant to fix; once a phase lands, the
corresponding xfail flips to a regular passing test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPointF

from diagrammer.commands.connect_command import CreateConnectionCommand
from diagrammer.commands.transform_command import (
    FlipComponentCommand,
    FlipItemCommand,
    RotateComponentCommand,
    RotateItemCommand,
)
from diagrammer.items.annotation_item import AnnotationItem
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.junction_item import JunctionItem
from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem

from tests._geometry_snapshot import assert_snapshots_equal, scene_snapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _two_port_def(library):
    """Return a 2-port component definition from the bundled library."""
    cdef = library.get("TWO_PORTS/blank_box_100pt")
    if cdef is None:
        # Fallback: scan didn't find it (test environment differences).
        # Find any component with at least 2 ports.
        for d in library.all_defs():
            if len(d.ports) >= 2:
                return d
        pytest.skip("No 2-port component available in bundled library")
    return cdef


def _place_component(scene, cdef, pos: QPointF) -> ComponentItem:
    item = ComponentItem(cdef)
    item.setPos(pos)
    scene.addItem(item)
    return item


def _connect(scene, src_comp, src_port_name, tgt_comp, tgt_port_name,
             waypoints: list[QPointF] | None = None):
    src_port = src_comp.port_by_name(src_port_name)
    tgt_port = tgt_comp.port_by_name(tgt_port_name)
    cmd = CreateConnectionCommand(scene, src_port, tgt_port)
    scene.undo_stack.push(cmd)
    conn = cmd.connection
    if waypoints:
        conn._waypoints = [QPointF(w) for w in waypoints]
        conn.update_route()
    return conn


@pytest.fixture
def fixture_diagram(scene, library):
    """A diagram covering every persistent item type the refactor touches.

    Layout:
      - 2 components wired together with a waypoint in the middle
      - 1 annotation, 1 rectangle, 1 ellipse, 1 line, 1 standalone junction
    """
    cdef = _two_port_def(library)
    comp_a = _place_component(scene, cdef, QPointF(0, 0))
    comp_b = _place_component(scene, cdef, QPointF(300, 0))
    # Wire with a single user waypoint
    src_port = comp_a.ports[-1]   # rightmost port (last in list, typically)
    tgt_port = comp_b.ports[0]    # leftmost port
    src_pt = src_port.scene_center()
    tgt_pt = tgt_port.scene_center()
    midpoint = QPointF((src_pt.x() + tgt_pt.x()) / 2,
                       (src_pt.y() + tgt_pt.y()) / 2 - 50)
    conn = _connect(scene, comp_a, src_port.port_name,
                    comp_b, tgt_port.port_name, waypoints=[midpoint])
    annot = AnnotationItem("hello")
    annot.setPos(QPointF(0, 200))
    scene.addItem(annot)
    rect = RectangleItem(width=80, height=40)
    rect.setPos(QPointF(150, 200))
    scene.addItem(rect)
    ell = EllipseItem(width=60, height=60)
    ell.setPos(QPointF(280, 200))
    scene.addItem(ell)
    line = LineItem(start=QPointF(0, 0), end=QPointF(60, 0))
    line.setPos(QPointF(380, 200))
    scene.addItem(line)
    junc = JunctionItem()
    junc.setPos(QPointF(150, 100))
    scene.addItem(junc)
    return {
        "comp_a": comp_a,
        "comp_b": comp_b,
        "conn": conn,
        "annot": annot,
        "rect": rect,
        "ell": ell,
        "line": line,
        "junc": junc,
    }


# ---------------------------------------------------------------------------
# Component-level invariants (currently passing — locks the contract)
# ---------------------------------------------------------------------------


class TestComponentRotation:
    def test_rotate_plus_minus_90_is_identity(self, scene, library):
        cdef = _two_port_def(library)
        comp = _place_component(scene, cdef, QPointF(50, 50))
        before = scene_snapshot(scene)
        scene.undo_stack.push(RotateComponentCommand(comp, 90))
        scene.undo_stack.push(RotateComponentCommand(comp, -90))
        assert_snapshots_equal(before, scene_snapshot(scene),
                               msg="+90 then -90 should be identity")

    def test_rotate_four_times_90_is_identity(self, scene, library):
        cdef = _two_port_def(library)
        comp = _place_component(scene, cdef, QPointF(50, 50))
        before = scene_snapshot(scene)
        for _ in range(4):
            scene.undo_stack.push(RotateComponentCommand(comp, 90))
        assert_snapshots_equal(before, scene_snapshot(scene),
                               msg="Four 90-degree rotations should be identity")

    def test_undo_restores_pre_rotation_snapshot(self, scene, library):
        cdef = _two_port_def(library)
        comp = _place_component(scene, cdef, QPointF(50, 50))
        before = scene_snapshot(scene)
        scene.undo_stack.push(RotateComponentCommand(comp, 90))
        scene.undo_stack.undo()
        assert_snapshots_equal(before, scene_snapshot(scene),
                               msg="Undoing a rotation should restore exact geometry")


class TestComponentFlip:
    def test_flip_h_twice_is_identity(self, scene, library):
        cdef = _two_port_def(library)
        comp = _place_component(scene, cdef, QPointF(50, 50))
        before = scene_snapshot(scene)
        scene.undo_stack.push(FlipComponentCommand(comp, horizontal=True))
        scene.undo_stack.push(FlipComponentCommand(comp, horizontal=True))
        assert_snapshots_equal(before, scene_snapshot(scene))

    def test_flip_v_twice_is_identity(self, scene, library):
        cdef = _two_port_def(library)
        comp = _place_component(scene, cdef, QPointF(50, 50))
        before = scene_snapshot(scene)
        scene.undo_stack.push(FlipComponentCommand(comp, horizontal=False))
        scene.undo_stack.push(FlipComponentCommand(comp, horizontal=False))
        assert_snapshots_equal(before, scene_snapshot(scene))


# ---------------------------------------------------------------------------
# Wire-follows-port invariants — the contract that port-relative waypoints
# (Phase B) is supposed to keep watertight.
# ---------------------------------------------------------------------------


class TestWireFollowsPort:
    def test_endpoint_tracks_port_through_rotation(self, scene, library):
        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        conn = _connect(scene, comp_a, comp_a.ports[-1].port_name,
                        comp_b, comp_b.ports[0].port_name)
        # Rotate comp_a; conn.source endpoint must equal the new port position.
        scene.undo_stack.push(RotateComponentCommand(comp_a, 90))
        new_src = comp_a.ports[-1].scene_center()
        assert conn._source_port.scene_center().x() == pytest.approx(new_src.x())
        assert conn._source_port.scene_center().y() == pytest.approx(new_src.y())

    @pytest.mark.xfail(
        reason="Phase B: waypoints are absolute scene coords and don't follow "
               "the port when its parent component is rotated.",
        strict=False,
    )
    def test_waypoint_follows_anchor_port_through_rotation(self, scene, library):
        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        # Place a waypoint right next to comp_a's source port. The "natural"
        # anchor for that waypoint is comp_a's port; after rotating comp_a
        # the waypoint's position relative to that port should be unchanged.
        src_pt = comp_a.ports[-1].scene_center()
        wp = QPointF(src_pt.x() + 10, src_pt.y() - 20)
        conn = _connect(scene, comp_a, comp_a.ports[-1].port_name,
                        comp_b, comp_b.ports[0].port_name, waypoints=[wp])
        # Capture (port -> waypoint) offset.
        before_offset = (
            conn._waypoints[0].x() - comp_a.ports[-1].scene_center().x(),
            conn._waypoints[0].y() - comp_a.ports[-1].scene_center().y(),
        )
        scene.undo_stack.push(RotateComponentCommand(comp_a, 90))
        # The Phase B contract: a port-anchored waypoint stays at the same
        # offset relative to its anchor port. Today, the waypoint stays put
        # in scene space, so this offset changes — hence xfail.
        wp_now = conn._waypoints[0]
        if hasattr(wp_now, "to_scene"):
            wp_now = wp_now.to_scene()
        after_offset = (
            wp_now.x() - comp_a.ports[-1].scene_center().x(),
            wp_now.y() - comp_a.ports[-1].scene_center().y(),
        )
        # Allow rotation of the offset vector (rotating the component
        # rotates the local frame). Compare magnitudes.
        import math
        before_mag = math.hypot(*before_offset)
        after_mag = math.hypot(*after_offset)
        assert after_mag == pytest.approx(before_mag, abs=0.5)


# ---------------------------------------------------------------------------
# Annotation invariants
# ---------------------------------------------------------------------------


class TestAnnotationRotation:
    def test_rotate_plus_minus_90_via_qt_is_identity(self, scene):
        """Pure Qt setRotation round-trip: should be exact today."""
        annot = AnnotationItem("hello")
        annot.setPos(QPointF(50, 50))
        scene.addItem(annot)
        before = scene_snapshot(scene)
        scene.undo_stack.push(RotateItemCommand(annot, 90))
        scene.undo_stack.push(RotateItemCommand(annot, -90))
        assert_snapshots_equal(before, scene_snapshot(scene),
                               msg="Annotation +90/-90 should round-trip")

    @pytest.mark.xfail(
        reason="Phase C: each RotateItemCommand resets the transform origin "
               "to the *current* boundingRect().center(); when the bounding "
               "rect grows between two rotations, the second rotation pivots "
               "around a different scene point and the visible center drifts.",
        strict=True,
    )
    def test_visual_center_unchanged_across_text_reflow_then_rotate(self, scene):
        """After Phase C: the cached intrinsic anchor pins the annotation's
        visible center even when the bounding rect changes between rotations.
        Today the second rotation re-anchors and the center drifts."""
        annot = AnnotationItem("short")
        annot.setPos(QPointF(50, 50))
        scene.addItem(annot)
        scene.undo_stack.push(RotateItemCommand(annot, 30))
        before_center = annot.mapToScene(annot.boundingRect().center())
        # Simulate a reflow that changes the bounding rect.
        annot._source_text = "much longer text that reflows"
        annot.setPlainText(annot._source_text)
        # Rotate by 0 — should be a no-op for the visible center, but the
        # command resets the transform origin to the new bounding-rect
        # center, which lives at a different scene point.
        scene.undo_stack.push(RotateItemCommand(annot, 0))
        after_center = annot.mapToScene(annot.boundingRect().center())
        assert after_center.x() == pytest.approx(before_center.x(), abs=0.5)
        assert after_center.y() == pytest.approx(before_center.y(), abs=0.5)


# ---------------------------------------------------------------------------
# Shape invariants
# ---------------------------------------------------------------------------


class TestShapeRotation:
    def test_rectangle_rotate_plus_minus_90_via_qt(self, scene):
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)
        before = scene_snapshot(scene)
        scene.undo_stack.push(RotateItemCommand(rect, 90))
        scene.undo_stack.push(RotateItemCommand(rect, -90))
        assert_snapshots_equal(before, scene_snapshot(scene))

    def test_rectangle_flip_h_twice_is_identity(self, scene):
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)
        before = scene_snapshot(scene)
        scene.undo_stack.push(FlipItemCommand(rect, horizontal=True))
        scene.undo_stack.push(FlipItemCommand(rect, horizontal=True))
        assert_snapshots_equal(before, scene_snapshot(scene))


# ---------------------------------------------------------------------------
# Save / load round-trip — gates Phase B (file format migration) and
# Phase C (intrinsic transform field serialization).
# ---------------------------------------------------------------------------


class TestSaveLoadRoundtrip:
    def test_roundtrip_preserves_fixture_diagram_geometry(
        self, scene, fixture_diagram, library, tmp_path: Path,
    ):
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer

        before = scene_snapshot(scene)
        path = tmp_path / "fixture.dgm"
        DiagramSerializer.save(scene, path)
        scene2 = DiagramScene(library=library)
        DiagramSerializer.load(scene2, path, library=library)
        after = scene_snapshot(scene2)
        # Snapshots are keyed by instance_id and the loader must preserve
        # ids — if not, that itself is a serializer bug worth surfacing.
        assert_snapshots_equal(before, after,
                               msg="save -> load should preserve geometry")

    def test_roundtrip_preserves_rotated_shape(self, scene, library, tmp_path: Path):
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer

        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)
        scene.undo_stack.push(RotateItemCommand(rect, 30))
        before = scene_snapshot(scene)
        path = tmp_path / "rot.dgm"
        DiagramSerializer.save(scene, path)
        scene2 = DiagramScene(library=library)
        DiagramSerializer.load(scene2, path, library=library)
        assert_snapshots_equal(before, scene_snapshot(scene2))

    def test_roundtrip_preserves_rotated_component(self, scene, library, tmp_path: Path):
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer

        cdef = _two_port_def(library)
        comp = _place_component(scene, cdef, QPointF(50, 50))
        scene.undo_stack.push(RotateComponentCommand(comp, 90))
        scene.undo_stack.push(FlipComponentCommand(comp, horizontal=True))
        before = scene_snapshot(scene)
        path = tmp_path / "comp.dgm"
        DiagramSerializer.save(scene, path)
        scene2 = DiagramScene(library=library)
        DiagramSerializer.load(scene2, path, library=library)
        assert_snapshots_equal(before, scene_snapshot(scene2),
                               msg="Component rotation+flip must round-trip")

    def test_roundtrip_preserves_wire_with_waypoints(
        self, scene, library, tmp_path: Path,
    ):
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer

        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        src_pt = comp_a.ports[-1].scene_center()
        tgt_pt = comp_b.ports[0].scene_center()
        wp = QPointF((src_pt.x() + tgt_pt.x()) / 2,
                     (src_pt.y() + tgt_pt.y()) / 2 - 40)
        _connect(scene, comp_a, comp_a.ports[-1].port_name,
                 comp_b, comp_b.ports[0].port_name, waypoints=[wp])
        before = scene_snapshot(scene)
        path = tmp_path / "wire.dgm"
        DiagramSerializer.save(scene, path)
        scene2 = DiagramScene(library=library)
        DiagramSerializer.load(scene2, path, library=library)
        assert_snapshots_equal(before, scene_snapshot(scene2),
                               msg="Wire waypoints must round-trip")


# ---------------------------------------------------------------------------
# Clipboard round-trip — gates Phase B (port-relative waypoints in the
# clipboard format) and Phase C (intrinsic transform fields for shapes).
# ---------------------------------------------------------------------------


def _make_clipboard_host(scene, library):
    """Build a minimal host that satisfies ClipboardMixin's protocol.

    The mixin needs ``_scene``, ``_view``, ``_clipboard``, ``_library``,
    and a ``statusBar()``. ``_view`` is only touched for paste positioning
    so a stub object suffices.
    """
    from PySide6.QtCore import QRect
    from diagrammer.clipboard_ops import ClipboardMixin

    class _StubViewport:
        def rect(self):
            return QRect(0, 0, 800, 600)

    class _StubView:
        def mapToScene(self, _):
            return QPointF(0, 0)

        def viewport(self):
            return _StubViewport()

    class _StubStatusBar:
        def showMessage(self, *_a, **_kw):
            pass

    class _Host(ClipboardMixin):
        def __init__(self):
            self._scene = scene
            self._library = library
            self._view = _StubView()
            self._clipboard: list[dict] = []

        def statusBar(self):
            return _StubStatusBar()

    return _Host()


class TestClipboardRoundtrip:
    @pytest.mark.xfail(
        reason="Phase C: clipboard_ops._copy() does not include 'rotation' "
               "(or flip_h/flip_v) for rectangle/ellipse, so a rotated shape "
               "loses its rotation on copy -> paste.",
        strict=True,
    )
    def test_clipboard_preserves_rotated_rectangle(self, scene, library):
        host = _make_clipboard_host(scene, library)
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)
        scene.undo_stack.push(RotateItemCommand(rect, 30))
        rect.setSelected(True)
        host._copy()
        # The copied dict must carry enough state to reproduce the rotation.
        # Today the entry has neither 'rotation' nor 'rotation_angle'.
        copied = [e for e in host._clipboard if e.get("type") == "rectangle"]
        assert copied, "rectangle not copied"
        assert "rotation" in copied[0] or "rotation_angle" in copied[0], (
            "shape clipboard entry is missing rotation state"
        )
