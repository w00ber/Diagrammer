"""Quarter turns and flips keep on-grid ports on the grid, and keep a
wire's orthogonal routing.

With grid snapping on, rotations by 90/180/270 degrees and flips pivot
on the nearest point that maps grid points onto grid points (a grid
point or cell centre for a quarter turn, the half grid for a half turn
or a flip's mirror line). Previously a lone part turned about its own
centre and a group about its unsnapped centroid, which left the ports
of most library parts half a grid cell off after a 90-degree turn.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.commands.add_command import MoveComponentCommand
from diagrammer.commands.connect_command import CreateConnectionCommand
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.connection_item import (
    ROUTE_DIRECT,
    ROUTE_ORTHO,
    ROUTE_ORTHO_45,
    ConnectionItem,
)
from diagrammer.items.junction_item import JunctionItem

from tests._geometry_snapshot import assert_snapshots_equal, scene_snapshot
from tests.test_transform_invariants import _make_transform_host

GRID = 20.0


@pytest.fixture
def view(scene):
    """A view on the scene with the default grid and snapping on."""
    from diagrammer.canvas.view import DiagramView

    v = DiagramView(scene)
    assert v.grid_spacing == GRID and v._snap_enabled
    yield v
    import shiboken6
    v.setScene(None)
    shiboken6.delete(v)


def _off_grid(scene) -> float:
    """Largest distance of any component/junction port from the grid."""
    worst = 0.0
    for item in scene.items():
        if isinstance(item, (ComponentItem, JunctionItem)):
            for port in item.ports:
                p = port.scene_center()
                worst = max(worst,
                            abs(p.x() - round(p.x() / GRID) * GRID),
                            abs(p.y() - round(p.y() / GRID) * GRID))
    return worst


def _place(scene, cdef, port_at: QPointF) -> ComponentItem:
    """Place *cdef* with its first port exactly on *port_at*."""
    comp = ComponentItem(cdef)
    scene.addItem(comp)
    comp._skip_snap = True
    comp.setPos(port_at - (comp.ports[0].scene_center() - comp.pos()))
    comp._skip_snap = False
    return comp


def _geometry(scene) -> dict:
    """Snapshot of everything visible: positions, ports, rendered wire
    routes (``expanded_hash``) and routing modes. Waypoint lists are
    dropped: a transform stores auto-routed bends as waypoints, which
    changes the representation but not the drawing."""
    snap = scene_snapshot(scene, tol=1e-3)
    for entry in snap.values():
        entry.pop("waypoints", None)
    return snap


def _connect(scene, port_a, port_b):
    cmd = CreateConnectionCommand(scene, port_a, port_b)
    scene.undo_stack.push(cmd)
    return cmd.connection


def _select_all(scene):
    scene.clearSelection()
    for item in scene.items():
        if isinstance(item, (ComponentItem, ConnectionItem)):
            item.setSelected(True)


def _apply(host, op):
    kind, arg = op
    if kind == "rot":
        host._rotate_selected(arg)
    else:
        host._flip_selected(horizontal=arg)


OPS = [("rot", 90), ("rot", -90), ("rot", 180), ("flip", True), ("flip", False)]


def _shunted_junction(scene, library):
    """Issue #34's circuit: JJ1 shunted by RES1, leads to free ends."""
    jj_def = library.get("PAPER_2pt/circuits/quantum/JJ1")
    res_def = library.get("PAPER_2pt/circuits/LCR/RES1")
    if jj_def is None or res_def is None:
        pytest.skip("JJ1 / RES1 not in bundled library")
    jj = _place(scene, jj_def, QPointF(200, 100))
    res = _place(scene, res_def, QPointF(120, 100))
    _connect(scene, jj.port_by_name("bot"), res.port_by_name("left"))
    _connect(scene, jj.port_by_name("top"), res.port_by_name("right"))
    for name, dy in (("bot", -60), ("top", 60)):
        end = JunctionItem()
        end.setPos(jj.port_by_name(name).scene_center() + QPointF(0, dy))
        scene.addItem(end)
        _connect(scene, end.port, jj.port_by_name(name))
    scene.update_connections()


class TestPortsStayOnGrid:
    def test_every_library_part(self, scene, view, library):
        """Each part with ports, alone, through ±90, 180 and both flips.
        Some SVGs put ports 0.1 off their own grid; that offset may move
        between axes but must not grow."""
        host = _make_transform_host(scene, library)
        failures = []
        for cdef in library.all_defs():
            if not cdef.ports:
                continue
            comp = _place(scene, cdef, QPointF(200, 200))
            for op in OPS:
                before = _off_grid(scene)
                _select_all(scene)
                _apply(host, op)
                after = _off_grid(scene)
                if after > before + 1e-6:
                    failures.append(f"{cdef.name} {op}: {before:.2f} -> {after:.2f}")
            scene.removeItem(comp)
        assert not failures, failures[:10]

    @pytest.mark.parametrize("op", OPS)
    def test_wired_group(self, scene, view, library, op):
        _shunted_junction(scene, library)
        before = _off_grid(scene)
        _select_all(scene)
        _apply(_make_transform_host(scene, library), op)
        assert _off_grid(scene) <= before + 1e-6


class TestTurnsStillUndoEachOther:
    """Symmetric parts sit exactly between allowed pivots; ties must not
    let a turn and its inverse pick different pivots."""

    @pytest.mark.parametrize("sequence", [
        [90, -90], [-90, 90], [90, 90, 90, 90], [-90, -90, -90, -90],
        [90, 90, -90, -90],
    ])
    def test_lone_symmetric_part(self, scene, view, library, sequence):
        cdef = library.get("PAPER_2pt/circuits/quantum/JJ1")
        if cdef is None:
            pytest.skip("JJ1 not in bundled library")
        _place(scene, cdef, QPointF(200, 100))
        before = _geometry(scene)
        host = _make_transform_host(scene, library)
        for degrees in sequence:
            _select_all(scene)
            host._rotate_selected(degrees)
        assert_snapshots_equal(before, _geometry(scene))

    @pytest.mark.parametrize("sequence", [[90, -90], [90, 90, 90, 90]])
    def test_wired_group(self, scene, view, library, sequence):
        _shunted_junction(scene, library)
        before = _geometry(scene)
        host = _make_transform_host(scene, library)
        for degrees in sequence:
            _select_all(scene)
            host._rotate_selected(degrees)
        assert_snapshots_equal(before, _geometry(scene))

    @pytest.mark.parametrize("horizontal", [True, False])
    def test_flip_twice(self, scene, view, library, horizontal):
        _shunted_junction(scene, library)
        before = _geometry(scene)
        host = _make_transform_host(scene, library)
        for _ in range(2):
            _select_all(scene)
            host._flip_selected(horizontal=horizontal)
        assert_snapshots_equal(before, _geometry(scene))

    def test_undo_restores_exactly(self, scene, view, library):
        _shunted_junction(scene, library)
        before = _geometry(scene)
        _select_all(scene)
        _make_transform_host(scene, library)._rotate_selected(90)
        scene.undo_stack.undo()
        assert_snapshots_equal(before, _geometry(scene))


class TestPivotLeftAlone:
    def test_snapping_off_turns_part_about_its_centre(self, scene, view, library):
        from diagrammer.transform_ops import _scene_center

        view._snap_enabled = False
        cdef = library.get("PAPER_2pt/circuits/quantum/JJ1")
        if cdef is None:
            pytest.skip("JJ1 not in bundled library")
        comp = _place(scene, cdef, QPointF(200, 100))
        centre = _scene_center(comp)
        _select_all(scene)
        _make_transform_host(scene, library)._rotate_selected(90)
        after = _scene_center(comp)
        assert (after.x(), after.y()) == (pytest.approx(centre.x()),
                                          pytest.approx(centre.y()))

    def test_text_and_shapes_keep_exact_centroid(self, scene, view, library):
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.shape_item import RectangleItem
        from diagrammer.transform_ops import _scene_center

        rect = RectangleItem(width=70, height=30)
        rect.setPos(QPointF(13, 7))
        scene.addItem(rect)
        note = AnnotationItem("label")
        note.setPos(QPointF(151, 43))
        scene.addItem(note)

        def centroid():
            cs = [_scene_center(i) for i in (rect, note)]
            return (sum(c.x() for c in cs) / 2, sum(c.y() for c in cs) / 2)

        before = centroid()
        rect.setSelected(True)
        note.setSelected(True)
        _make_transform_host(scene, library)._rotate_selected(90)
        assert centroid() == (pytest.approx(before[0]), pytest.approx(before[1]))


def _two_part_l_wire(scene, library, mode=ROUTE_ORTHO, offset=QPointF(300, 100)):
    from tests.test_transform_invariants import _two_port_def

    cdef = _two_port_def(library)
    a = _place(scene, cdef, QPointF(0, 0))
    b = _place(scene, cdef, offset)
    conn = _connect(scene, a.ports[-1], b.ports[0])
    conn.routing_mode = mode
    scene.update_connections()
    return a, b, conn


def _segments_axis_aligned(conn) -> bool:
    pts = conn.routed_points()
    return all(abs(p.x() - q.x()) < 1e-3 or abs(p.y() - q.y()) < 1e-3
               for p, q in zip(pts, pts[1:]))


class TestRoutingModeKept:
    @pytest.mark.parametrize("op", OPS)
    def test_quarter_turns_and_flips_keep_ortho(self, scene, library, op):
        _a, _b, conn = _two_part_l_wire(scene, library)
        _select_all(scene)
        _apply(_make_transform_host(scene, library), op)
        assert conn.routing_mode == ROUTE_ORTHO

    def test_fine_rotation_still_pins_direct(self, scene, library):
        _a, _b, conn = _two_part_l_wire(scene, library)
        _select_all(scene)
        _make_transform_host(scene, library)._fine_rotate_selected(15)
        assert conn.routing_mode == ROUTE_DIRECT

    def test_moving_a_part_after_rotation_routes_orthogonally(self, scene, library):
        """The reason to keep the mode: with ROUTE_DIRECT, dragging a part
        after a rotation drew the segment to its port diagonally."""
        _a, b, conn = _two_part_l_wire(scene, library)
        _select_all(scene)
        _make_transform_host(scene, library)._rotate_selected(90)
        scene.undo_stack.push(MoveComponentCommand(b, b.pos(), b.pos() + QPointF(40, 20)))
        scene.update_connections()
        assert _segments_axis_aligned(conn)

    def test_ortho_45_route_with_diagonal_is_kept(self, scene, library):
        _a, _b, conn = _two_part_l_wire(scene, library, mode=ROUTE_ORTHO_45,
                                        offset=QPointF(300, 160))
        pts = conn.routed_points()
        assert any(abs(p.x() - q.x()) > 1 and abs(p.y() - q.y()) > 1
                   for p, q in zip(pts, pts[1:])), "fixture needs a diagonal"
        _select_all(scene)
        _make_transform_host(scene, library)._rotate_selected(90)
        assert conn.routing_mode == ROUTE_ORTHO_45
