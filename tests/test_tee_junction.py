"""Tests for converting an unconnected tee — a wire end or component lead
touching the middle of another wire — into a real junction with a dot."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.items.connection_item import ConnectionItem
from diagrammer.items.junction_item import JunctionItem


def _junction(scene, x, y):
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


def _wires(scene):
    return [i for i in scene.items() if isinstance(i, ConnectionItem)]


def _host_halves(scene, junction):
    """Wires on *junction* whose far end lies on the host line y == 0."""
    halves = []
    for conn in scene.connections_on_port(junction.port):
        far = conn.target_port if conn.source_port is junction.port else conn.source_port
        if far.scene_center().y() == pytest.approx(0):
            halves.append(conn)
    return halves


@pytest.fixture
def host(scene):
    """A straight horizontal wire between free ends at (-60,0) and (260,0)."""
    conn = _connect(scene, _junction(scene, -60, 0).port,
                    _junction(scene, 260, 0).port)
    scene.update_connections()
    return conn


def _capacitor(scene, library, port_pos: QPointF):
    """CAP1 placed so its top ('left') port sits at *port_pos*."""
    from diagrammer.items.component_item import ComponentItem

    cdef = library.get("PAPER_2pt/circuits/LCR/CAP1")
    if cdef is None:
        pytest.skip("CAP1 not in bundled library")
    cap = ComponentItem(cdef)
    scene.addItem(cap)
    offset = cap.port_by_name("left").scene_center() - cap.pos()
    cap.setPos(port_pos - offset)
    scene.update_connections()
    return cap


class TestWireEndTee:
    @pytest.fixture
    def tee(self, scene, host):
        """A vertical wire whose free end touches the host at (100, 0)."""
        end = _junction(scene, 100, 0)
        _connect(scene, _junction(scene, 100, 100).port, end.port)
        scene.update_connections()
        return end

    def test_detected(self, scene, host, tee):
        hit = scene.find_tee_at(QPointF(103, 2), tolerance=12.0)
        assert hit is not None
        found_host, port, point = hit
        assert found_host is host and port is tee.port
        assert (point.x(), point.y()) == (pytest.approx(100), pytest.approx(0))

    def test_convert_makes_dotted_junction_and_undoes(self, scene, host, tee):
        scene.convert_tee_to_junction(*scene.find_tee_at(QPointF(100, 0), 12.0))
        assert host.scene() is None                      # split into halves
        assert len(scene.connections_on_port(tee.port)) == 3
        assert tee._should_draw_dot()
        assert scene.find_tee_at(QPointF(100, 0), 12.0) is None

        scene.undo_stack.undo()
        assert host.scene() is scene
        assert len(scene.connections_on_port(tee.port)) == 1
        assert len(_wires(scene)) == 2

    def test_slightly_off_route_lands_on_it(self, scene, host):
        end = _junction(scene, 100, 0.5)
        _connect(scene, _junction(scene, 100, 100).port, end.port)
        scene.update_connections()
        scene.convert_tee_to_junction(*scene.find_tee_at(QPointF(100, 0), 12.0))
        assert (end.pos().x(), end.pos().y()) == (pytest.approx(100), pytest.approx(0))
        halves = _host_halves(scene, end)
        assert len(halves) == 2
        for half in halves:
            assert all(p.y() == pytest.approx(0) for p in half.routed_points())


class TestComponentTee:
    def test_detected(self, scene, library, host):
        cap = _capacitor(scene, library, QPointF(100, 0))
        hit = scene.find_tee_at(QPointF(98, -3), tolerance=12.0)
        assert hit is not None
        assert hit[0] is host and hit[1] is cap.port_by_name("left")

    def test_convert_adds_junction_and_zero_length_connector(self, scene, library, host):
        cap = _capacitor(scene, library, QPointF(100, 0))
        port = cap.port_by_name("left")
        scene.convert_tee_to_junction(*scene.find_tee_at(QPointF(100, 0), 12.0))

        connectors = scene.connections_on_port(port)
        assert len(connectors) == 1
        connector = connectors[0]
        junction = connector.target_port.component
        assert isinstance(junction, JunctionItem)
        assert len(scene.connections_on_port(junction.port)) == 3
        assert junction._should_draw_dot()
        # Zero length, and it neither shortens the lead nor adds a stub.
        assert {(p.x(), p.y()) for p in connector.all_points()} == {(100.0, 0.0)}
        assert cap._compute_lead_shortening() == {}
        assert scene.find_tee_at(QPointF(100, 0), 12.0) is None

        scene.undo_stack.undo()
        assert host.scene() is scene
        assert junction.scene() is None
        assert scene.connections_on_port(port) == []

    def test_moving_part_keeps_it_connected(self, scene, library, host):
        from diagrammer.commands.add_command import MoveComponentCommand

        cap = _capacitor(scene, library, QPointF(100, 0))
        port = cap.port_by_name("left")
        scene.convert_tee_to_junction(*scene.find_tee_at(QPointF(100, 0), 12.0))
        connector = scene.connections_on_port(port)[0]
        scene.undo_stack.push(MoveComponentCommand(
            cap, cap.pos(), cap.pos() + QPointF(0, 40)))
        scene.update_connections()
        route = connector.routed_points()
        assert (route[0].x(), route[0].y()) == (pytest.approx(100), pytest.approx(40))
        assert (route[-1].x(), route[-1].y()) == (pytest.approx(100), pytest.approx(0))

    def test_slightly_off_route_keeps_host_straight(self, scene, library, host):
        cap = _capacitor(scene, library, QPointF(100, 0.4))
        scene.convert_tee_to_junction(*scene.find_tee_at(QPointF(100, 0), 12.0))
        junction = scene.connections_on_port(
            cap.port_by_name("left"))[0].target_port.component
        assert (junction.pos().x(), junction.pos().y()) == (
            pytest.approx(100), pytest.approx(0))
        halves = _host_halves(scene, junction)
        assert len(halves) == 2
        for half in halves:
            assert all(p.y() == pytest.approx(0) for p in half.routed_points())


class TestNotATee:
    def test_click_far_away(self, scene, host):
        end = _junction(scene, 100, 0)
        _connect(scene, _junction(scene, 100, 100).port, end.port)
        scene.update_connections()
        assert scene.find_tee_at(QPointF(100, 60), tolerance=12.0) is None

    def test_touching_the_hosts_end_is_a_join(self, scene, host):
        end = _junction(scene, 260, 0)  # same spot as the host's end
        _connect(scene, _junction(scene, 260, 100).port, end.port)
        scene.update_connections()
        assert scene.find_tee_at(QPointF(260, 0), tolerance=12.0) is None

    def test_wire_lying_along_the_host_is_an_overlap(self, scene, host):
        _connect(scene, _junction(scene, 100, 0).port, _junction(scene, 150, 0).port)
        scene.update_connections()
        assert scene.find_tee_at(QPointF(100, 0), tolerance=12.0) is None

    def test_properly_drawn_tee_is_not_offered(self, scene, host):
        """A tee already joined through a junction has nothing to convert."""
        tap = _junction(scene, 100, 0)
        scene.split_connection_at_junction(host, tap)
        _connect(scene, _junction(scene, 100, 100).port, tap.port)
        scene.update_connections()
        assert scene.find_tee_at(QPointF(100, 0), tolerance=12.0) is None
