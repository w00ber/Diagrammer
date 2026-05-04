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
