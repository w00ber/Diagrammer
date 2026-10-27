"""Regression tests for the drag-time junction auto-include policy.

User-reported regression: rubber-band-selecting a sub-circuit and
dragging it stranded a free-terminal junction (visually a tee)
because the rubber-band actively deselects junctions outside the
rect AND the press handler short-circuited junction auto-include
for explicit multi-select. Combined, those two filters left the
junction behind, the wire's other end stayed where the junction
sat, and ``_set_waypoints_from_scene`` re-anchored the now-distant
waypoints to the source port — visibly dangling.

The fix is in :func:`diagrammer.canvas.view._terminal_junctions_to_auto_include`,
which auto-includes junctions whose every wire terminates at a
selected item. Hub junctions with at least one wire to an unselected
item are intentionally skipped — pulling them would corrupt the
unselected branch.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.canvas.view import _terminal_junctions_to_auto_include
from diagrammer.commands.connect_command import CreateConnectionCommand
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.junction_item import JunctionItem


def _two_port_def(library):
    cdef = library.get("TWO_PORTS/blank_box_100pt")
    if cdef is None:
        for d in library.all_defs():
            if len(d.ports) >= 2:
                return d
        pytest.skip("No 2-port component available")
    return cdef


def _place(scene, cdef, pos: QPointF) -> ComponentItem:
    c = ComponentItem(cdef)
    c.setPos(pos)
    scene.addItem(c)
    return c


def _connect(scene, src_comp_or_jct, src_port_name, tgt_comp_or_jct, tgt_port_name):
    if isinstance(src_comp_or_jct, JunctionItem):
        src_port = src_comp_or_jct.port
    else:
        src_port = src_comp_or_jct.port_by_name(src_port_name)
    if isinstance(tgt_comp_or_jct, JunctionItem):
        tgt_port = tgt_comp_or_jct.port
    else:
        tgt_port = tgt_comp_or_jct.port_by_name(tgt_port_name)
    cmd = CreateConnectionCommand(scene, src_port, tgt_port)
    scene.undo_stack.push(cmd)
    return cmd.connection


class TestTerminalJunctionAutoInclude:
    def test_free_terminal_junction_is_included(self, scene, library):
        """Single wire from comp.right to a free junction: dragging
        the comp must drag the junction along, otherwise the wire
        would stretch and re-anchor onto a wrong port."""
        cdef = _two_port_def(library)
        comp = _place(scene, cdef, QPointF(0, 0))
        jct = JunctionItem()
        jct.setPos(QPointF(200, 50))
        scene.addItem(jct)
        _connect(scene, comp, comp.ports[-1].port_name, jct, None)

        pulled = _terminal_junctions_to_auto_include(scene.items(), [comp])
        assert jct in pulled, "free terminal junction must follow its wire's component"

    def test_hub_junction_with_unselected_branch_is_excluded(self, scene, library):
        """Junction with wires to comp_a AND comp_b: dragging comp_a
        alone must NOT pull the junction (comp_b's branch would tear)."""
        cdef = _two_port_def(library)
        comp_a = _place(scene, cdef, QPointF(0, 0))
        comp_b = _place(scene, cdef, QPointF(400, 0))
        jct = JunctionItem()
        jct.setPos(QPointF(200, 50))
        scene.addItem(jct)
        _connect(scene, comp_a, comp_a.ports[-1].port_name, jct, None)
        _connect(scene, comp_b, comp_b.ports[0].port_name, jct, None)

        pulled = _terminal_junctions_to_auto_include(scene.items(), [comp_a])
        assert jct not in pulled, (
            "junction with an unselected branch must not be auto-included"
        )

    def test_hub_junction_with_all_branches_selected_is_included(
        self, scene, library
    ):
        """Junction wired to comp_a AND comp_b, both selected: include."""
        cdef = _two_port_def(library)
        comp_a = _place(scene, cdef, QPointF(0, 0))
        comp_b = _place(scene, cdef, QPointF(400, 0))
        jct = JunctionItem()
        jct.setPos(QPointF(200, 50))
        scene.addItem(jct)
        _connect(scene, comp_a, comp_a.ports[-1].port_name, jct, None)
        _connect(scene, comp_b, comp_b.ports[0].port_name, jct, None)

        pulled = _terminal_junctions_to_auto_include(
            scene.items(), [comp_a, comp_b]
        )
        assert jct in pulled

    def test_unrelated_junction_is_not_included(self, scene, library):
        """A junction with no wires to any selected item must be skipped."""
        cdef = _two_port_def(library)
        comp_a = _place(scene, cdef, QPointF(0, 0))
        comp_b = _place(scene, cdef, QPointF(400, 0))
        jct = JunctionItem()
        jct.setPos(QPointF(200, 50))
        scene.addItem(jct)
        _connect(scene, comp_b, comp_b.ports[0].port_name, jct, None)
        # Drag comp_a only; jct only connects to comp_b.

        pulled = _terminal_junctions_to_auto_include(scene.items(), [comp_a])
        assert jct not in pulled

    def test_already_selected_junction_not_double_added(self, scene, library):
        """If the user already selected the junction, don't return it again."""
        cdef = _two_port_def(library)
        comp = _place(scene, cdef, QPointF(0, 0))
        jct = JunctionItem()
        jct.setPos(QPointF(200, 50))
        scene.addItem(jct)
        _connect(scene, comp, comp.ports[-1].port_name, jct, None)

        pulled = _terminal_junctions_to_auto_include(scene.items(), [comp, jct])
        assert jct not in pulled


class TestClosureAlongRails:
    """The rule: the connected sub-circuit you selected moves, up to the
    first component you did not select.

    A rail is a chain of junctions joined by wires. Judged one at a
    time, every rail junction has a neighbour that is "not selected" —
    another junction, which can never be selected — so a single-pass
    policy left every rail behind even with the whole circuit boxed.
    """

    def _ladder(self, scene, library):
        cdef = _two_port_def(library)
        a = _place(scene, cdef, QPointF(0, 100))
        b = _place(scene, cdef, QPointF(400, 100))
        # Rail: three junctions in a chain, taps from each component.
        j0, j1, j2 = (JunctionItem() for _ in range(3))
        for j, x in ((j0, 0), (j1, 200), (j2, 400)):
            j.setPos(QPointF(x, 0))
            scene.addItem(j)
        _connect(scene, j0, None, j1, None)
        _connect(scene, j1, None, j2, None)
        _connect(scene, a, a.ports[0].port_name, j0, None)
        _connect(scene, b, b.ports[0].port_name, j2, None)
        return a, b, (j0, j1, j2)

    def test_whole_circuit_selected_pulls_the_whole_rail(self, scene, library):
        a, b, rail = self._ladder(scene, library)
        pulled = set(_terminal_junctions_to_auto_include(scene.items(), [a, b]))
        assert pulled == set(rail)

    def test_rail_continuing_to_an_unselected_part_stays(self, scene, library):
        a, b, rail = self._ladder(scene, library)
        pulled = _terminal_junctions_to_auto_include(scene.items(), [a])
        assert pulled == [], "rail runs on to b, which is not moving"

    def test_free_end_of_a_selected_rail_comes_along(self, scene, library):
        """A rail's dangling end has only a junction neighbour; it must
        follow the rail rather than pin it."""
        cdef = _two_port_def(library)
        a = _place(scene, cdef, QPointF(0, 100))
        j0, j_end = JunctionItem(), JunctionItem()
        j0.setPos(QPointF(0, 0)); j_end.setPos(QPointF(300, 0))
        scene.addItem(j0); scene.addItem(j_end)
        _connect(scene, a, a.ports[0].port_name, j0, None)
        _connect(scene, j0, None, j_end, None)
        pulled = set(_terminal_junctions_to_auto_include(scene.items(), [a]))
        assert pulled == {j0, j_end}
