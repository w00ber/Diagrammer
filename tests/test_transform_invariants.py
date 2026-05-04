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

    def test_waypoint_follows_anchor_port_through_rotation(self, scene, library):
        """A waypoint anchored to a port must move with that port through
        any component transform. Port-relative offsets are stored in the
        port's local coords (Phase D semantics), so the waypoint rotates
        with the parent component automatically."""
        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        # Waypoint near comp_a's source port — closest-port heuristic
        # binds it to that port.
        src_port = comp_a.ports[-1]
        src_pt = src_port.scene_center()
        wp = QPointF(src_pt.x() + 10, src_pt.y() - 20)
        conn = _connect(scene, comp_a, src_port.port_name,
                        comp_b, comp_b.ports[0].port_name, waypoints=[wp])
        # The anchor must have been bound to comp_a's port.
        assert conn._anchors[0].anchor is src_port
        anchor_dx = conn._anchors[0].dx
        anchor_dy = conn._anchors[0].dy
        # Rotate the component; the anchor's port-LOCAL offset is
        # invariant under any parent transform.
        scene.undo_stack.push(RotateComponentCommand(comp_a, 90))
        assert conn._anchors[0].dx == pytest.approx(anchor_dx)
        assert conn._anchors[0].dy == pytest.approx(anchor_dy)
        # And the resolved scene point matches the rotated frame —
        # i.e., the waypoint rotated 90 around the port along with
        # the rest of the component, not just translated.
        rotated_scene = src_port.mapToScene(QPointF(anchor_dx, anchor_dy))
        assert conn._waypoints[0].x() == pytest.approx(rotated_scene.x(), abs=0.5)
        assert conn._waypoints[0].y() == pytest.approx(rotated_scene.y(), abs=0.5)


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

    def test_visual_center_unchanged_across_text_reflow(self, scene):
        """After Phase C: when the bounding rect changes (text edit, math
        re-render), the annotation's visible center stays put. The
        intrinsic anchor is re-pinned with a one-time scene-position
        correction, so subsequent rotations also pivot around the
        right point."""
        annot = AnnotationItem("short")
        annot.setPos(QPointF(50, 50))
        scene.addItem(annot)
        scene.undo_stack.push(RotateItemCommand(annot, 30))
        before_center = annot.mapToScene(annot.boundingRect().center())
        # Simulate a reflow via the public API — text_content's setter
        # calls _try_render_math which recomputes the intrinsic anchor.
        annot.text_content = "much longer text that reflows"
        after_center = annot.mapToScene(annot.boundingRect().center())
        assert after_center.x() == pytest.approx(before_center.x(), abs=0.5)
        assert after_center.y() == pytest.approx(before_center.y(), abs=0.5)
        # Subsequent rotation back to identity must also leave the
        # visible center at the same scene point — a drifting anchor
        # would tumble the annotation.
        scene.undo_stack.push(RotateItemCommand(annot, -30))
        final_center = annot.mapToScene(annot.boundingRect().center())
        assert final_center.x() == pytest.approx(before_center.x(), abs=0.5)
        assert final_center.y() == pytest.approx(before_center.y(), abs=0.5)


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

    def test_line_rotate_plus_minus_90(self, scene):
        line = LineItem(start=QPointF(0, 0), end=QPointF(80, 0))
        line.setPos(QPointF(50, 50))
        scene.addItem(line)
        before = scene_snapshot(scene)
        scene.undo_stack.push(RotateItemCommand(line, 90))
        scene.undo_stack.push(RotateItemCommand(line, -90))
        assert_snapshots_equal(before, scene_snapshot(scene))

    def test_rotated_shape_pivots_around_geometric_center(self, scene):
        """Phase C contract: a shape rotates around its own (width/2,
        height/2), not around the bounding rect's selection-padded
        center. Two opposite rotations cancel exactly."""
        rect = RectangleItem(width=200, height=60)
        rect.setPos(QPointF(100, 100))
        scene.addItem(rect)
        before_center = rect.mapToScene(QPointF(100, 30))  # geometric center
        scene.undo_stack.push(RotateItemCommand(rect, 45))
        after_center = rect.mapToScene(QPointF(100, 30))
        assert before_center.x() == pytest.approx(after_center.x(), abs=0.5)
        assert before_center.y() == pytest.approx(after_center.y(), abs=0.5)


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

    def test_v1_format_file_loads_via_migration(
        self, scene, library, tmp_path: Path,
    ):
        """A pre-Phase-B v1 file (absolute waypoints) must load and rebind
        each waypoint to its closest port — geometry preserved."""
        import json
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer

        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        src_port = comp_a.ports[-1]
        tgt_port = comp_b.ports[0]
        src_pt = src_port.scene_center()
        tgt_pt = tgt_port.scene_center()
        wp = QPointF((src_pt.x() + tgt_pt.x()) / 2,
                     (src_pt.y() + tgt_pt.y()) / 2 - 40)
        _connect(scene, comp_a, src_port.port_name,
                 comp_b, tgt_port.port_name, waypoints=[wp])

        # Save in v2, then rewrite the file as v1 (absolute waypoints)
        # to simulate a file written by the old code.
        path = tmp_path / "v1.dgm"
        DiagramSerializer.save(scene, path)
        data = json.loads(path.read_text())
        data["version"] = "1.1"
        for cd in data["connections"]:
            cd["waypoints"] = [[wp.x(), wp.y()]]
        path.write_text(json.dumps(data))

        scene2 = DiagramScene(library=library)
        DiagramSerializer.load(scene2, path, library=library)
        # The migrated wire's waypoint should resolve to the same scene point.
        from diagrammer.items.connection_item import ConnectionItem
        conns = [i for i in scene2.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 1
        loaded = conns[0]
        assert len(loaded._waypoints) == 1
        assert loaded._waypoints[0].x() == pytest.approx(wp.x(), abs=0.5)
        assert loaded._waypoints[0].y() == pytest.approx(wp.y(), abs=0.5)
        # And it should be port-anchored (closest port wins; here source
        # is closer because PASTE_OFFSET-style offset isn't in play).
        assert loaded._anchors[0].anchor in (loaded.source_port, loaded.target_port)


# ---------------------------------------------------------------------------
# Clipboard round-trip — gates Phase B (port-relative waypoints in the
# clipboard format) and Phase C (intrinsic transform fields for shapes).
# ---------------------------------------------------------------------------


def _make_transform_host(scene, library):
    """Build a minimal host satisfying TransformMixin's protocol for tests.

    Combines TransformMixin and ClipboardMixin (the former depends on
    ``_gather_connected_junctions`` from the latter).
    """
    from diagrammer.clipboard_ops import ClipboardMixin
    from diagrammer.transform_ops import TransformMixin

    class _Host(TransformMixin, ClipboardMixin):
        def __init__(self):
            self._scene = scene
            self._library = library
            self._clipboard: list[dict] = []

    return _Host()


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


def _decorative_def(library):
    """Return a port-less (decorative) component definition.

    The bundled FLAIR/edges/* SVGs are pure-decoration: they have no
    ``<g id="ports">`` so ``ComponentDef.ports`` is empty.
    """
    for d in library.all_defs():
        if not d.ports:
            return d
    pytest.skip("No decorative (port-less) component in library")


class TestWaypointDrag:
    """Regression: Phase B's _refresh_from_anchors hook in update_route
    silently overwrote any direct ``_waypoints[idx] = ...`` write — so
    the in-class waypoint drag handlers (which I missed during the
    write-site audit because I only looked at view.py) saw the user's
    drag clobbered back to the anchor-derived position. Symptom:
    "I added a waypoint but I cannot drag it."
    """

    def test_single_waypoint_drag_persists_through_update_route(self, scene, library):
        """Simulate the connection_item drag flow: set _dragging_waypoint
        and feed a mouseMoveEvent. The new position must survive the
        update_route() refresh that fires inside the move handler."""
        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        src_pt = comp_a.ports[-1].scene_center()
        tgt_pt = comp_b.ports[0].scene_center()
        wp = QPointF((src_pt.x() + tgt_pt.x()) / 2,
                     (src_pt.y() + tgt_pt.y()) / 2)
        conn = _connect(scene, comp_a, comp_a.ports[-1].port_name,
                        comp_b, comp_b.ports[0].port_name, waypoints=[wp])
        # Bypass the press handler (which needs view-level event setup)
        # and put the connection straight into "dragging waypoint 0".
        conn._dragging_waypoint = 0

        # Drag the waypoint 50 units up. Build a synthetic QMouseEvent
        # that the connection's mouseMoveEvent can read scenePos from.
        target = QPointF(wp.x(), wp.y() - 50)

        class _FakeMoveEvent:
            def __init__(self, sp):
                self._sp = sp
            def scenePos(self):
                return self._sp
            def accept(self):
                pass

        conn.mouseMoveEvent(_FakeMoveEvent(target))
        # The waypoint must be at (or near, after grid snap) the target,
        # not back at the original position.
        moved = conn._waypoints[0]
        assert abs(moved.y() - target.y()) <= 10  # within one grid cell
        assert moved.y() < wp.y()  # genuinely moved upward


class TestPortlessComponentRotation:
    """Regression tests for the ``i.ports`` filter that silently dropped
    decorative components from fine-rotate (and from group fine-rotate).
    Selected by users via the FLAIR/edges/* bug report."""

    def test_fine_rotate_single_decorative_component(self, scene, library):
        cdef = _decorative_def(library)
        comp = ComponentItem(cdef)
        comp.setPos(QPointF(100, 100))
        scene.addItem(comp)
        comp.setSelected(True)
        host = _make_transform_host(scene, library)
        host._fine_rotate_selected(15)
        assert comp.rotation_angle == pytest.approx(15)

    def test_fine_rotate_group_with_decorative_component(self, scene, library):
        """Even when grouped with port-bearing components, a decorative
        component must rotate AND orbit — not get silently dropped."""
        deco_def = _decorative_def(library)
        ported_def = _two_port_def(library)
        deco = ComponentItem(deco_def)
        deco.setPos(QPointF(0, 0))
        scene.addItem(deco)
        ported = ComponentItem(ported_def)
        ported.setPos(QPointF(300, 0))
        scene.addItem(ported)
        deco.setSelected(True)
        ported.setSelected(True)
        host = _make_transform_host(scene, library)
        host._fine_rotate_selected(30)
        # Both components rotated internally.
        assert deco.rotation_angle == pytest.approx(30)
        assert ported.rotation_angle == pytest.approx(30)
        # And the decorative component also orbited around the group
        # center: its position is no longer (0, 0).
        assert deco.pos() != QPointF(0, 0)


class TestGroupTransform:
    """End-to-end tests through TransformMixin — these were the
    bug-prone paths the refactor was aimed at."""

    def test_group_rotate_90_then_minus_90_is_identity(self, scene, library):
        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        annot = AnnotationItem("hi")
        annot.setPos(QPointF(150, 100))
        scene.addItem(annot)
        rect = RectangleItem(width=80, height=40)
        rect.setPos(QPointF(150, -80))
        scene.addItem(rect)
        before = scene_snapshot(scene)
        for item in (comp_a, comp_b, annot, rect):
            item.setSelected(True)
        host = _make_transform_host(scene, library)
        host._rotate_selected(90)
        host._rotate_selected(-90)
        assert_snapshots_equal(before, scene_snapshot(scene),
                               msg="Group rotate +90 then -90 must restore exact geometry")

    def test_group_rotate_preserves_internal_wire_shape(self, scene, library):
        """Two components connected by an L-shaped wire, rotated as a
        group, must keep the relative wire shape — this used to require
        the pre-capture / ROUTE_DIRECT hack and now falls out from
        Phase B's port-local waypoints."""
        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 100))  # offset so we get a real L
        src_port = comp_a.ports[-1]
        tgt_port = comp_b.ports[0]
        wp = QPointF(src_port.scene_center().x() + 50,
                     src_port.scene_center().y())
        conn = _connect(scene, comp_a, src_port.port_name,
                        comp_b, tgt_port.port_name, waypoints=[wp])
        # Capture the offset of the waypoint from its anchor port in
        # port-local coords — invariant under any rigid-body transform.
        anchor_local = (conn._anchors[0].dx, conn._anchors[0].dy)

        for item in (comp_a, comp_b, conn):
            item.setSelected(True)
        host = _make_transform_host(scene, library)
        host._rotate_selected(90)

        # Waypoint anchor unchanged, anchor offset (port-local)
        # unchanged → wire shape rotated with the components.
        assert conn._anchors[0].anchor in (conn.source_port, conn.target_port)
        assert conn._anchors[0].dx == pytest.approx(anchor_local[0])
        assert conn._anchors[0].dy == pytest.approx(anchor_local[1])
        # Phase D contract: with port-local waypoints, the routing mode
        # does NOT need to switch to ROUTE_DIRECT for 90° rotations.
        from diagrammer.items.connection_item import ROUTE_DIRECT
        assert conn.routing_mode != ROUTE_DIRECT

    def test_group_rotate_identity_round_trip_with_undo(self, scene, library):
        """Undo of a group rotation must restore exact geometry — every
        mutation in the macro lives in a QUndoCommand."""
        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        _connect(scene, comp_a, comp_a.ports[-1].port_name,
                 comp_b, comp_b.ports[0].port_name)
        before = scene_snapshot(scene)
        for item in (comp_a, comp_b):
            item.setSelected(True)
        host = _make_transform_host(scene, library)
        host._rotate_selected(90)
        scene.undo_stack.undo()
        assert_snapshots_equal(before, scene_snapshot(scene))


class TestClipboardRoundtrip:
    def test_clipboard_preserves_rotated_rectangle(self, scene, library):
        """A rotated shape copied and pasted must keep its rotation.
        Pre-Phase-C the clipboard format omitted rotation/flip for
        shapes — silent data loss."""
        host = _make_clipboard_host(scene, library)
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(50, 50))
        scene.addItem(rect)
        scene.undo_stack.push(RotateItemCommand(rect, 30))
        rect.setSelected(True)
        host._copy()
        host._paste()
        from diagrammer.items.shape_item import RectangleItem as _Rect
        rects = [i for i in scene.items() if isinstance(i, _Rect)]
        assert len(rects) == 2
        pasted = next(r for r in rects if r is not rect)
        assert pasted.rotation_angle == pytest.approx(30, abs=0.5)

    def test_paste_preserves_wire_shape_relative_to_components(
        self, scene, library,
    ):
        """Copying a (component, component, wire-with-waypoint) selection and
        pasting should reproduce the wire shape relative to the pasted
        components — i.e. the waypoint sits at the same offset from its
        anchor port. Pre-Phase-B this drifted because waypoints were shifted
        by PASTE_OFFSET while the ports were also shifted, double-offsetting."""
        host = _make_clipboard_host(scene, library)
        cdef = _two_port_def(library)
        comp_a = _place_component(scene, cdef, QPointF(0, 0))
        comp_b = _place_component(scene, cdef, QPointF(300, 0))
        src_port = comp_a.ports[-1]
        tgt_port = comp_b.ports[0]
        src_pt = src_port.scene_center()
        tgt_pt = tgt_port.scene_center()
        wp = QPointF((src_pt.x() + tgt_pt.x()) / 2,
                     (src_pt.y() + tgt_pt.y()) / 2 - 40)
        conn = _connect(scene, comp_a, src_port.port_name,
                        comp_b, tgt_port.port_name, waypoints=[wp])
        # Capture the wire's anchor offsets before copy.
        original_offsets = [(a.dx, a.dy) for a in conn._anchors]

        for item in (comp_a, comp_b, conn):
            item.setSelected(True)
        host._copy()
        host._paste()

        # Two new components and one new wire should now exist.
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        all_comps = [i for i in scene.items() if isinstance(i, ComponentItem)]
        all_conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(all_comps) == 4
        assert len(all_conns) == 2
        pasted_conn = next(c for c in all_conns if c is not conn)
        pasted_offsets = [(a.dx, a.dy) for a in pasted_conn._anchors]
        assert pasted_offsets == pytest.approx(original_offsets, abs=0.5)
