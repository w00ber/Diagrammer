"""Tests for wire direction arrows (signal-flow indicators)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.items.connection_item import ConnectionItem, WireArrow


@pytest.fixture(autouse=True)
def _reset_sticky_arrow_direction():
    """Reset the shared session-sticky arrow direction between tests.

    ``ConnectionItem._new_arrow_forward`` is a class attribute persisting
    across tests; without this a test that flips an arrow would leak the
    direction into unrelated placement tests.
    """
    ConnectionItem._new_arrow_forward = True
    yield
    ConnectionItem._new_arrow_forward = True


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


def _h_wire(scene, y=0.0, x0=0.0, x1=200.0):
    """A straight horizontal wire from (x0, y) to (x1, y)."""
    conn = _connect(scene, _add_junction(scene, x0, y).port,
                    _add_junction(scene, x1, y).port)
    scene.update_connections()
    return conn


class TestArrowPlacement:
    def test_add_arrow_at_projects_onto_route(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(50, 12))  # off-wire click projects onto it
        assert len(conn.arrows) == 1
        assert conn.arrows[0].t == pytest.approx(0.25)
        assert conn.arrows[0].forward is True

    def test_multiple_arrows_per_wire(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(50, 0))
        conn.add_arrow_at(QPointF(150, 0))
        assert len(conn.arrows) == 2

    def test_new_arrow_uses_defaults(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(100, 0))
        a = conn.arrows[0]
        assert a.style is None and a.size is None and a.line_width is None

    def test_scene_find_wire_at(self, scene):
        conn = _h_wire(scene)
        hit = scene.find_wire_at(QPointF(80, 8), tolerance=15.0)
        assert hit is not None
        found, proj = hit
        assert found is conn
        assert (proj.x(), proj.y()) == (pytest.approx(80), pytest.approx(0))
        assert scene.find_wire_at(QPointF(80, 100), tolerance=15.0) is None

    def test_scene_find_wire_at_exclude_port(self, scene):
        conn = _h_wire(scene)
        hit = scene.find_wire_at(QPointF(80, 8), tolerance=15.0,
                                 exclude_port=conn.source_port)
        assert hit is None

    def test_scene_find_wire_arrow_at(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(50, 0))
        hit = scene.find_wire_arrow_at(QPointF(53, 4), tolerance=12.0)
        assert hit is not None and hit[0] is conn and hit[1] == 0
        assert scene.find_wire_arrow_at(QPointF(150, 0), tolerance=12.0) is None


class TestArrowGeometry:
    def test_arrow_extends_clickable_shape(self, scene):
        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.5, size=40.0)]
        # Arrow disc radius 24 > stroke half-width; a point 20 above the
        # wire at the arrow is inside shape(), away from the arrow it isn't.
        assert conn.shape().contains(QPointF(100, 20))
        assert not conn.shape().contains(QPointF(30, 20))

    def test_bounding_rect_covers_large_arrow(self, scene):
        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.5, size=40.0)]
        r = conn.boundingRect()
        assert r.top() <= -20.0
        assert r.bottom() >= 20.0

    def test_find_arrow_at_tolerance(self, scene):
        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.5)]
        assert conn._find_arrow_at(QPointF(103, 4)) == 0
        assert conn._find_arrow_at(QPointF(140, 0)) is None

    def test_large_arrow_pick_radius_grows(self, scene):
        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.5, size=50.0)]
        # Pick radius follows size (50 * 0.6 = 30), same as the shape() disc
        assert conn._find_arrow_at(QPointF(125, 0)) == 0
        assert conn._find_arrow_at(QPointF(140, 0)) is None

    def test_pick_radius_consistent_between_item_and_scene(self, scene):
        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.5, size=50.0)]
        # Scene-level lookup shares the item's tolerance model
        probe = QPointF(125, 0)
        assert conn._find_arrow_at(probe) == 0
        assert scene.find_wire_arrow_at(probe, tolerance=10.0) == (conn, 0)

    def test_arrow_tracks_reroute(self, scene):
        from diagrammer.utils.geometry import point_at_fraction

        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.5)]
        # Move an endpoint junction: the route changes but t is relative,
        # so the arrow stays midway along the new route.
        end = conn.target_port.component
        end.setPos(QPointF(400, 0))
        conn.update_route()
        pt, _ = point_at_fraction(conn.all_points(), conn.arrows[0].t)
        assert pt.x() == pytest.approx(200)


class TestArrowOperations:
    def test_flip_toggles_forward(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(100, 0))
        conn._flip_arrow(0)
        assert conn.arrows[0].forward is False
        conn._flip_arrow(0)
        assert conn.arrows[0].forward is True

    def test_delete_arrow(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(50, 0))
        conn.add_arrow_at(QPointF(150, 0))
        conn._delete_arrow(0)
        assert len(conn.arrows) == 1
        assert conn.arrows[0].t == pytest.approx(0.75)

    def test_set_arrow_fields(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(100, 0))
        conn._set_arrow_fields(0, style="open", size=20.0)
        a = conn.arrows[0]
        assert a.style == "open"
        assert a.size == 20.0
        assert a.line_width is None
        # Reset a field back to "use default"
        conn._set_arrow_fields(0, style=None)
        assert conn.arrows[0].style is None

    def test_arrows_property_returns_copies(self, scene):
        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.5)]
        snapshot = conn.arrows
        snapshot[0].t = 0.9
        assert conn.arrows[0].t == pytest.approx(0.5)

    def test_default_style_resolution_follows_settings(self, scene):
        from diagrammer.panels.settings_dialog import app_settings

        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.5), WireArrow(t=0.8, style="open", size=30.0)]
        old = (app_settings.default_wire_arrow_style,
               app_settings.default_wire_arrow_size)
        try:
            app_settings.default_wire_arrow_style = "open"
            app_settings.default_wire_arrow_size = 22.0
            style, size, _lw = conn._resolved_arrow(conn.arrows[0])
            assert (style, size) == ("open", 22.0)
            # Explicit overrides stick
            style, size, _lw = conn._resolved_arrow(conn.arrows[1])
            assert (style, size) == ("open", 30.0)
        finally:
            (app_settings.default_wire_arrow_style,
             app_settings.default_wire_arrow_size) = old


class TestStickyArrowDirection:
    def test_new_arrow_defaults_forward(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(100, 0))
        assert conn.arrows[0].forward is True

    def test_flip_makes_next_placement_backward_same_wire(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(50, 0))
        conn._flip_arrow(0)
        conn.add_arrow_at(QPointF(150, 0))
        assert conn.arrows[1].forward is False

    def test_sticky_direction_is_global_across_wires(self, scene):
        wire_a = _h_wire(scene, y=0.0)
        wire_b = _h_wire(scene, y=100.0)
        wire_a.add_arrow_at(QPointF(100, 0))
        wire_a._flip_arrow(0)
        # A brand-new arrow on a different wire inherits the flipped default
        wire_b.add_arrow_at(QPointF(100, 100))
        assert wire_b.arrows[0].forward is False

    def test_dialog_direction_change_updates_sticky(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(50, 0))
        conn._set_arrow_fields(0, forward=False)
        conn.add_arrow_at(QPointF(150, 0))
        assert conn.arrows[1].forward is False

    def test_style_only_edit_leaves_sticky_unchanged(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(50, 0))
        conn._set_arrow_fields(0, style="open")
        conn.add_arrow_at(QPointF(150, 0))
        # Direction default untouched by a style-only edit
        assert conn.arrows[1].forward is True

    def test_undo_of_flip_does_not_rewind_sticky(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(50, 0))
        conn._flip_arrow(0)
        scene.undo_stack.undo()  # restores arrow 0 to forward
        assert conn.arrows[0].forward is True
        # Sticky default (UI state) stays flipped — new placements honor it
        conn.add_arrow_at(QPointF(150, 0))
        assert conn.arrows[-1].forward is False


class TestArrowUndo:
    def test_add_arrow_undo_redo(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(100, 0))
        assert len(conn.arrows) == 1
        scene.undo_stack.undo()
        assert conn.arrows == []
        scene.undo_stack.redo()
        assert len(conn.arrows) == 1
        assert conn.arrows[0].t == pytest.approx(0.5)

    def test_flip_undo(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(100, 0))
        conn._flip_arrow(0)
        assert conn.arrows[0].forward is False
        scene.undo_stack.undo()
        assert conn.arrows[0].forward is True

    def test_delete_undo_restores_exact_arrow(self, scene):
        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.3, forward=False, style="open", size=18.0)]
        conn._delete_arrow(0)
        assert conn.arrows == []
        # _delete_arrow pushed the command; undo restores the full record
        scene.undo_stack.undo()
        assert conn.arrows == [WireArrow(t=0.3, forward=False, style="open", size=18.0)]

    def test_style_edit_undo(self, scene):
        conn = _h_wire(scene)
        conn.add_arrow_at(QPointF(100, 0))
        conn._set_arrow_fields(0, style="open")
        scene.undo_stack.undo()
        assert conn.arrows[0].style is None

    def test_detached_wire_mutates_without_undo(self, scene):
        # Operations on a wire without an undo stack fall back to direct
        # mutation instead of raising.
        conn = _h_wire(scene)
        scene.removeItem(conn)
        conn.add_arrow_at(QPointF(100, 0))
        assert len(conn.arrows) == 1


class TestArrowClipboard:
    def test_copy_paste_preserves_arrows(self, scene):
        from diagrammer.clipboard_ops import ClipboardMixin
        from diagrammer.items.connection_item import ConnectionItem

        conn = _h_wire(scene)
        conn.arrows = [WireArrow(t=0.25, forward=False, style="open")]
        for item in (conn, conn.source_port.component,
                     conn.target_port.component):
            item.setSelected(True)

        class _Host(ClipboardMixin):
            """Minimal MainWindow stand-in for copy/paste."""

            def __init__(self, sc):
                self._scene = sc
                self._clipboard = []

            def _try_paste_external_svg(self):
                return False

        host = _Host(scene)
        host._copy()
        host._paste()

        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 2
        pasted = next(c for c in conns if c is not conn)
        assert pasted.arrows == [WireArrow(t=0.25, forward=False, style="open")]
