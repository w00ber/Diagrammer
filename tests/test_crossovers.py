"""Tests for wire crossover styles: hops, overrides, and junction conversion."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF


def _add_junction(scene, x, y, visible=False):
    from diagrammer.items.junction_item import JunctionItem

    j = JunctionItem()
    j.setPos(QPointF(x, y))
    j.setVisible(visible)
    scene.addItem(j)
    return j


def _connect(scene, port_a, port_b):
    from diagrammer.commands.connect_command import CreateConnectionCommand

    cmd = CreateConnectionCommand(scene, port_a, port_b)
    scene.undo_stack.push(cmd)
    return cmd.connection


def _cross_pair(scene):
    """A horizontal and a vertical wire crossing at (100, 50)."""
    h = _connect(scene, _add_junction(scene, 0, 50).port,
                 _add_junction(scene, 200, 50).port)
    v = _connect(scene, _add_junction(scene, 100, -30).port,
                 _add_junction(scene, 100, 130).port)
    scene.update_connections()
    return h, v


@pytest.fixture
def hop_default():
    """Set the global crossover default to 'hop' for the test duration."""
    from diagrammer.panels.settings_dialog import app_settings

    old = getattr(app_settings, "default_crossover_style", "plain")
    app_settings.default_crossover_style = "hop"
    yield
    app_settings.default_crossover_style = old


def _owner_of(scene, a, b):
    _style, owner_id, _flip = scene.resolve_crossover(a, b)
    return a if owner_id == a.instance_id else b


class TestHopRendering:
    def test_plain_default_no_arcs(self, scene):
        h, v = _cross_pair(scene)
        assert not scene.crossover_scan_needed()
        assert h.path().length() == pytest.approx(200.0)
        assert v.path().length() == pytest.approx(160.0)

    def test_hop_default_only_owner_arcs(self, scene, hop_default):
        h, v = _cross_pair(scene)
        scene.update_connections()
        owner = _owner_of(scene, h, v)
        loser = v if owner is h else h
        plain_owner_len = 200.0 if owner is h else 160.0
        plain_loser_len = 160.0 if owner is h else 200.0
        assert owner.path().length() > plain_owner_len + 1.0
        assert loser.path().length() == pytest.approx(plain_loser_len)

    def test_hops_never_leak_into_waypoints(self, scene, hop_default):
        """The load-bearing invariant: hop geometry exists only in the
        painted path, never in the expanded route or waypoints."""
        h, v = _cross_pair(scene)
        scene.update_connections()
        owner = _owner_of(scene, h, v)
        assert len(owner._expanded) == 2  # straight route, no hop points
        before = [QPointF(w) for w in owner.vertices]
        # Simulate what a segment drag does on release
        owner._store_expanded_as_waypoints(list(owner._expanded))
        for w in owner.vertices:
            # every waypoint lies on the straight route (x or y matches)
            assert (abs(w.x() - owner._expanded[0].x()) < 0.5
                    or abs(w.y() - owner._expanded[0].y()) < 0.5)
        assert len(owner.vertices) <= max(len(before), 2)

    def test_t_joined_wires_do_not_hop(self, scene, hop_default):
        """Wires meeting at a shared junction port must not hop there."""
        hub = _add_junction(scene, 100, 50)
        a = _connect(scene, _add_junction(scene, 0, 50).port, hub.port)
        b = _connect(scene, hub.port, _add_junction(scene, 100, 130).port)
        scene.update_connections()
        assert a._compute_hops() == []
        assert b._compute_hops() == []

    def test_moving_wire_recomputes_hop_position(self, scene, hop_default):
        """Two-pass rebuild: after moving the non-owner wire, the owner's
        hop lands on the new intersection."""
        h, v = _cross_pair(scene)
        scene.update_connections()
        owner = _owner_of(scene, h, v)
        loser = v if owner is h else h
        old_hops = owner._compute_hops()
        assert len(old_hops) == 1

        # Shift the loser wire by moving both its endpoint junctions
        shift = QPointF(30, 0) if loser is v else QPointF(0, 30)
        for port in (loser.source_port, loser.target_port):
            comp = port.component
            comp._skip_snap = True
            comp.setPos(comp.pos() + shift)
            comp._skip_snap = False
        scene.update_connections()

        new_hops = owner._compute_hops()
        assert len(new_hops) == 1
        point, _sign = new_hops[0]
        old_point, _old_sign = old_hops[0]
        expected = QPointF(old_point.x() + shift.x(),
                           old_point.y() + shift.y())
        assert point.x() == pytest.approx(expected.x())
        assert point.y() == pytest.approx(expected.y())

    def test_hop_owner_is_higher_z(self, scene, hop_default):
        h, v = _cross_pair(scene)
        h.setZValue(50.0)
        v.setZValue(10.0)
        scene.update_connections()
        _style, owner_id, _flip = scene.resolve_crossover(h, v)
        assert owner_id == h.instance_id
        assert h.path().length() > 201.0
        assert v.path().length() == pytest.approx(160.0)


class TestCrossoverOverrides:
    def test_override_plain_suppresses_hop(self, scene, hop_default):
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand

        h, v = _cross_pair(scene)
        scene.update_connections()
        owner = _owner_of(scene, h, v)
        assert owner.path().length() > (200.0 if owner is h else 160.0) + 1.0

        cmd = SetCrossoverStyleCommand(scene, h, v, None, {"style": "plain"})
        scene.undo_stack.push(cmd)
        assert h.path().length() == pytest.approx(200.0)
        assert v.path().length() == pytest.approx(160.0)

        scene.undo_stack.undo()
        assert owner.path().length() > (200.0 if owner is h else 160.0) + 1.0

    def test_override_hop_with_plain_default(self, scene):
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand

        h, v = _cross_pair(scene)
        assert not scene.crossover_scan_needed()
        cmd = SetCrossoverStyleCommand(scene, h, v, None, {"style": "hop"})
        scene.undo_stack.push(cmd)
        assert scene.crossover_scan_needed()
        owner = _owner_of(scene, h, v)
        assert owner.path().length() > (200.0 if owner is h else 160.0) + 1.0

    def test_swap_owner(self, scene, hop_default):
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand

        h, v = _cross_pair(scene)
        scene.update_connections()
        _style, owner_id, _flip = scene.resolve_crossover(h, v)
        other_id = h.instance_id if owner_id == v.instance_id else v.instance_id
        cmd = SetCrossoverStyleCommand(scene, h, v, None, {"owner": other_id})
        scene.undo_stack.push(cmd)
        _style2, owner_id2, _flip2 = scene.resolve_crossover(h, v)
        assert owner_id2 == other_id
        new_owner = h if other_id == h.instance_id else v
        assert new_owner.path().length() > (200.0 if new_owner is h else 160.0) + 1.0
        scene.undo_stack.undo()
        _style3, owner_id3, _flip3 = scene.resolve_crossover(h, v)
        assert owner_id3 == owner_id

    def test_find_crossing_at(self, scene):
        h, v = _cross_pair(scene)
        hit = scene.find_crossing_at(QPointF(102, 48), 10.0)
        assert hit is not None
        conn_a, conn_b, pt = hit
        assert {conn_a, conn_b} == {h, v}
        assert pt.x() == pytest.approx(100)
        assert pt.y() == pytest.approx(50)
        # owner is returned first
        assert conn_a is _owner_of(scene, h, v)
        # miss
        assert scene.find_crossing_at(QPointF(30, 100), 10.0) is None

    def test_find_crossing_ignores_t_joins(self, scene):
        hub = _add_junction(scene, 100, 50)
        _connect(scene, _add_junction(scene, 0, 50).port, hub.port)
        _connect(scene, hub.port, _add_junction(scene, 100, 130).port)
        scene.update_connections()
        assert scene.find_crossing_at(QPointF(100, 50), 10.0) is None


class TestConvertCrossingToJunction:
    def test_conversion_creates_four_legs(self, scene):
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        h, v = _cross_pair(scene)
        scene.convert_crossing_to_junction(h, v, QPointF(100, 50))

        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert len(conns) == 4
        assert h not in conns and v not in conns
        new_juncs = [
            i for i in scene.items()
            if isinstance(i, JunctionItem) and i.isVisible()
        ]
        assert len(new_juncs) == 1
        assert len(scene.connections_on_port(new_juncs[0].port)) == 4
        assert new_juncs[0].pos() == QPointF(100, 50)

    def test_single_undo_restores_both_wires(self, scene):
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        h, v = _cross_pair(scene)
        n_junc = len([i for i in scene.items() if isinstance(i, JunctionItem)])
        scene.convert_crossing_to_junction(h, v, QPointF(100, 50))
        scene.undo_stack.undo()
        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert set(conns) == {h, v}
        assert len([i for i in scene.items() if isinstance(i, JunctionItem)]) == n_junc

    def test_conversion_clears_pair_override(self, scene):
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand

        h, v = _cross_pair(scene)
        scene.undo_stack.push(
            SetCrossoverStyleCommand(scene, h, v, None, {"style": "hop"}))
        scene.convert_crossing_to_junction(h, v, QPointF(100, 50))
        assert scene.get_crossover_override(h.instance_id, v.instance_id) is None

    def test_conversion_preserves_other_crossing_overrides(
            self, scene, hop_default):
        """Splitting one crossing must not reset overrides or flip hop
        ownership at the host wire's OTHER crossings."""
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand
        from diagrammer.items.connection_item import ConnectionItem

        h = _connect(scene, _add_junction(scene, 0, 50).port,
                     _add_junction(scene, 400, 50).port)
        v1 = _connect(scene, _add_junction(scene, 80, -30).port,
                      _add_junction(scene, 80, 130).port)
        v2 = _connect(scene, _add_junction(scene, 320, -30).port,
                      _add_junction(scene, 320, 130).port)
        scene.update_connections()
        _style, v1_owner_before, _flipb = scene.resolve_crossover(h, v1)
        # Override crossing (h, v1) to plain, then convert (h, v2)
        scene.undo_stack.push(
            SetCrossoverStyleCommand(scene, h, v1, None, {"style": "plain"}))
        scene.convert_crossing_to_junction(h, v2, QPointF(320, 50))

        # v1 must still cross plainly (override followed the halves)
        assert v1.path().length() == pytest.approx(160.0)
        for conn in scene.items():
            if isinstance(conn, ConnectionItem) and conn is not v1:
                assert conn._compute_hops() == [], (
                    "plain override was lost by the split"
                )
        # Halves keep the host's z, so ownership at other crossings is stable
        left_half = next(
            c for c in scene.items()
            if isinstance(c, ConnectionItem)
            and c.source_port is h.source_port
        )
        _s2, owner_after, _flip_after = scene.resolve_crossover(left_half, v1)
        if v1_owner_before == v1.instance_id:
            assert owner_after == v1.instance_id

    def test_conversion_refused_at_endpoint(self, scene):
        from diagrammer.items.connection_item import ConnectionItem

        h, v = _cross_pair(scene)
        scene.convert_crossing_to_junction(h, v, QPointF(0, 50))  # h's endpoint
        conns = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        assert set(conns) == {h, v}


class TestCrossoverSerialization:
    def test_round_trip(self, scene, tmp_path):
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand
        from diagrammer.io.serializer import DiagramSerializer
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.models.library import ComponentLibrary

        h, v = _cross_pair(scene)
        scene.undo_stack.push(SetCrossoverStyleCommand(
            scene, h, v, None, {"style": "hop", "owner": h.instance_id}))
        path = tmp_path / "x.dgm"
        DiagramSerializer.save(scene, path)

        scene2 = DiagramScene(library=ComponentLibrary())
        DiagramSerializer.load(scene2, path)
        key = scene2.crossover_key(h.instance_id, v.instance_id)
        entry = scene2._crossover_overrides.get(key)
        assert entry == {"style": "hop", "owner": h.instance_id}

    def test_dangling_override_pruned_on_save(self, scene, tmp_path):
        import json
        from diagrammer.io.serializer import DiagramSerializer

        h, v = _cross_pair(scene)
        scene._crossover_overrides[frozenset((h.instance_id, "nonexistent"))] = {
            "style": "hop"}
        path = tmp_path / "x.dgm"
        DiagramSerializer.save(scene, path)
        data = json.loads(path.read_text())
        assert "crossovers" not in data

    def test_malformed_entries_skipped_on_load(self, scene, tmp_path):
        import json
        from diagrammer.io.serializer import DiagramSerializer
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.models.library import ComponentLibrary

        h, v = _cross_pair(scene)
        path = tmp_path / "x.dgm"
        DiagramSerializer.save(scene, path)
        data = json.loads(path.read_text())
        data["crossovers"] = [
            {"a": h.instance_id, "b": h.instance_id, "style": "hop"},  # a == b
            {"a": h.instance_id, "b": "missing", "style": "hop"},      # dangling
            {"a": h.instance_id, "b": v.instance_id, "style": "bogus"},  # bad style
            "not-a-dict",
        ]
        path.write_text(json.dumps(data))

        scene2 = DiagramScene(library=ComponentLibrary())
        DiagramSerializer.load(scene2, path)  # must not raise
        assert scene2._crossover_overrides == {}

    def test_version_is_current_format(self, scene, tmp_path):
        import json
        from diagrammer.io.serializer import DiagramSerializer, FORMAT_VERSION

        path = tmp_path / "x.dgm"
        DiagramSerializer.save(scene, path)
        assert json.loads(path.read_text())["version"] == FORMAT_VERSION


class TestJunctionDotSetting:
    @pytest.fixture
    def dots_off(self):
        from diagrammer.panels.settings_dialog import app_settings

        old = getattr(app_settings, "show_junction_dots", True)
        app_settings.show_junction_dots = False
        yield
        app_settings.show_junction_dots = old

    def _t_junction(self, scene):
        hub = _add_junction(scene, 100, 50)
        _connect(scene, _add_junction(scene, 0, 50).port, hub.port)
        _connect(scene, hub.port, _add_junction(scene, 100, 130).port)
        return hub

    def test_dot_drawn_by_default(self, scene):
        hub = self._t_junction(scene)
        assert hub._should_draw_dot()

    def test_dot_suppressed_by_setting(self, scene, dots_off):
        hub = self._t_junction(scene)
        assert not hub._should_draw_dot()

    def test_single_wire_junction_never_dots(self, scene):
        hub = _add_junction(scene, 100, 50)
        _connect(scene, _add_junction(scene, 0, 50).port, hub.port)
        assert not hub._should_draw_dot()


class TestWireEndMarkers:
    def _free_end_wire(self, scene):
        """Wire between two hidden free-end junctions; returns the target end."""
        end = _add_junction(scene, 200, 0)
        _connect(scene, _add_junction(scene, 0, 0).port, end.port)
        return end

    def test_default_is_none(self, scene):
        end = self._free_end_wire(scene)
        assert end.end_marker == "none"
        assert not end.isVisible()

    def test_marker_makes_hidden_junction_visible(self, scene):
        end = self._free_end_wire(scene)
        end.end_marker = "filled"
        assert end.isVisible()
        # And it paints above the attached wire
        conn = scene.connections_on_port(end.port)[0]
        assert end.zValue() > conn.zValue()

    def test_marker_undoable_via_change_style_command(self, scene):
        from diagrammer.commands.style_command import ChangeStyleCommand

        end = self._free_end_wire(scene)
        scene.undo_stack.push(
            ChangeStyleCommand(end, 'end_marker', "none", "open"))
        assert end.end_marker == "open"
        scene.undo_stack.undo()
        assert end.end_marker == "none"
        scene.undo_stack.redo()
        assert end.end_marker == "open"

    def test_bogus_value_coerced_to_none(self, scene):
        end = self._free_end_wire(scene)
        end.end_marker = "sparkles"
        assert end.end_marker == "none"

    def test_marker_independent_of_junction_dots_setting(self, scene):
        from diagrammer.panels.settings_dialog import app_settings

        end = self._free_end_wire(scene)
        end.end_marker = "filled"
        old = getattr(app_settings, "show_junction_dots", True)
        app_settings.show_junction_dots = False
        try:
            # The automatic-dot gate is off, but the explicit marker stands
            assert not end._should_draw_dot()
            assert end.end_marker == "filled"
            assert end.isVisible()
        finally:
            app_settings.show_junction_dots = old

    def test_serialization_round_trip(self, scene, tmp_path):
        import json
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.models.library import ComponentLibrary

        from diagrammer.io.serializer import FORMAT_VERSION

        end_a = self._free_end_wire(scene)
        end_a.end_marker = "filled"
        end_b = self._free_end_wire(scene)
        end_b.end_marker = "open"
        path = tmp_path / "markers.dgm"
        DiagramSerializer.save(scene, path)
        assert json.loads(path.read_text())["version"] == FORMAT_VERSION

        scene2 = DiagramScene(library=ComponentLibrary())
        DiagramSerializer.load(scene2, path)
        markers = {
            j.instance_id: j.end_marker
            for j in scene2.items() if isinstance(j, JunctionItem)
        }
        assert markers[end_a.instance_id] == "filled"
        assert markers[end_b.instance_id] == "open"

    def test_bogus_marker_in_file_ignored(self, scene, tmp_path):
        import json
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.models.library import ComponentLibrary

        end = self._free_end_wire(scene)
        path = tmp_path / "markers.dgm"
        DiagramSerializer.save(scene, path)
        data = json.loads(path.read_text())
        for jd in data["junctions"]:
            jd["end_marker"] = "bogus"
        path.write_text(json.dumps(data))

        scene2 = DiagramScene(library=ComponentLibrary())
        DiagramSerializer.load(scene2, path)  # must not raise
        for j in scene2.items():
            if isinstance(j, JunctionItem):
                assert j.end_marker == "none"


class TestHopFlipSide:
    def _hopped(self, scene):
        """Crossing wires with global hop default; returns (owner, other)."""
        h, v = _cross_pair(scene)
        scene.update_connections()
        owner = _owner_of(scene, h, v)
        other = v if owner is h else h
        return owner, other

    def test_resolve_flip_defaults_false(self, scene, hop_default):
        h, v = _cross_pair(scene)
        _s, _o, flip = scene.resolve_crossover(h, v)
        assert flip is False

    def test_resolve_flip_true_with_override(self, scene, hop_default):
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand
        h, v = _cross_pair(scene)
        scene.undo_stack.push(
            SetCrossoverStyleCommand(scene, h, v, None, {"flip": True}))
        _s, _o, flip = scene.resolve_crossover(h, v)
        assert flip is True

    def test_compute_hops_sign_reflects_flip(self, scene, hop_default):
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand
        owner, other = self._hopped(scene)
        (_pt, sign_before), = owner._compute_hops()
        assert sign_before == 1
        scene.undo_stack.push(
            SetCrossoverStyleCommand(scene, owner, other, None, {"flip": True}))
        (_pt2, sign_after), = owner._compute_hops()
        assert sign_after == -1

    def test_flip_geometry_mirrors_bulge(self):
        from diagrammer.utils.geometry import build_rounded_path
        pts = [QPointF(0, 0), QPointF(100, 0)]
        up = build_rounded_path(pts, 0.0, hops=[(QPointF(50, 0), 1)], hop_radius=6.0)
        down = build_rounded_path(pts, 0.0, hops=[(QPointF(50, 0), -1)], hop_radius=6.0)
        # +1 bulges screen-up (negative y), -1 bulges screen-down
        assert up.boundingRect().top() == pytest.approx(-6.0, abs=0.2)
        assert up.boundingRect().bottom() == pytest.approx(0.0, abs=0.2)
        assert down.boundingRect().bottom() == pytest.approx(6.0, abs=0.2)
        assert down.boundingRect().top() == pytest.approx(0.0, abs=0.2)

    def test_bare_point_hop_equals_plus_one(self):
        from diagrammer.utils.geometry import build_rounded_path
        pts = [QPointF(0, 0), QPointF(100, 0)]
        bare = build_rounded_path(pts, 0.0, hops=[QPointF(50, 0)], hop_radius=6.0)
        signed = build_rounded_path(pts, 0.0, hops=[(QPointF(50, 0), 1)], hop_radius=6.0)
        assert bare == signed

    def test_serialization_preserves_flip(self, scene, tmp_path):
        import json
        from diagrammer.commands.connect_command import SetCrossoverStyleCommand
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer, FORMAT_VERSION
        from diagrammer.models.library import ComponentLibrary

        h, v = _cross_pair(scene)
        scene.undo_stack.push(SetCrossoverStyleCommand(
            scene, h, v, None, {"style": "hop", "flip": True}))
        path = tmp_path / "flip.dgm"
        DiagramSerializer.save(scene, path)
        assert json.loads(path.read_text())["version"] == FORMAT_VERSION

        scene2 = DiagramScene(library=ComponentLibrary())
        DiagramSerializer.load(scene2, path)
        entry = scene2._crossover_overrides.get(
            scene2.crossover_key(h.instance_id, v.instance_id))
        assert entry.get("flip") is True

    def test_cycle_visits_all_four_and_returns(self, scene, hop_default):
        h, v = _cross_pair(scene)
        scene.update_connections()
        view = scene.views()
        if not view:
            from diagrammer.canvas.view import DiagramView
            v_widget = DiagramView(scene)
        else:
            v_widget = view[0]

        seen = []
        start = scene.resolve_crossover(h, v)[1:]  # (owner, flip)
        for _ in range(4):
            v_widget._cycle_crossing_hop(h, v)
            seen.append(scene.resolve_crossover(h, v)[1:])
        # 4 distinct orientations, and the 4th returns to the start
        assert len(set(seen)) == 4
        assert seen[-1] == start
