"""Cut/copy/paste must carry per-instance styling, not just geometry.

Editing a component's fill or line width writes a ``ComponentStyleOverrides``
entry on that instance.  The clipboard used to serialize only the def key and
transforms, so a paste rebuilt the part from the pristine SVG and silently
threw the user's edits away.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.clipboard_ops import ClipboardMixin
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.junction_item import JunctionItem
from diagrammer.models.svg_style_override import (
    ComponentStyleOverrides,
    SvgElementStyleOverride,
)


class _Host(ClipboardMixin):
    """Minimal stand-in for MainWindow: just what the mixin touches."""

    def __init__(self, scene, library):
        self._scene = scene
        self._view = None
        self._library = library
        self._clipboard: list = []

    def _try_paste_external_svg(self) -> bool:
        return False


@pytest.fixture
def host(library):
    from diagrammer.canvas.scene import DiagramScene
    scene = DiagramScene(library=library)
    return _Host(scene, library)


def _any_def(library):
    defs = library.all_defs()
    if not defs:
        pytest.skip("component library is empty")
    return defs[0]


def test_paste_preserves_component_style_overrides(host, library):
    comp_def = _any_def(library)
    comp = ComponentItem(comp_def)
    comp.setPos(QPointF(0, 0))
    host._scene.addItem(comp)
    comp.set_element_style("artwork/0", "stroke_width", 7.5)
    comp.set_element_style("artwork/0", "fill_color", "#ff0000")

    comp.setSelected(True)
    host._copy()
    host._scene.clearSelection()
    host._paste()

    pasted = [
        i for i in host._scene.items()
        if isinstance(i, ComponentItem) and i is not comp
    ]
    assert len(pasted) == 1
    ovr = pasted[0].style_overrides.get("artwork/0")
    assert ovr is not None, "pasted component lost its style overrides"
    assert ovr.stroke_width == 7.5
    assert ovr.fill_color == "#ff0000"


def test_cut_paste_preserves_component_style_overrides(host, library):
    comp_def = _any_def(library)
    comp = ComponentItem(comp_def)
    comp.setPos(QPointF(0, 0))
    host._scene.addItem(comp)
    comp.set_element_style("artwork/0", "stroke_color", "#00ff00")

    comp.setSelected(True)
    host._cut()
    host._paste()

    pasted = [i for i in host._scene.items() if isinstance(i, ComponentItem)]
    assert len(pasted) == 1
    ovr = pasted[0].style_overrides.get("artwork/0")
    assert ovr is not None and ovr.stroke_color == "#00ff00"


def test_clipboard_snapshot_is_independent_of_later_edits(host, library):
    """Styling the original after a copy must not change what gets pasted."""
    comp_def = _any_def(library)
    comp = ComponentItem(comp_def)
    host._scene.addItem(comp)
    comp.set_element_style("artwork/0", "stroke_width", 2.0)

    comp.setSelected(True)
    host._copy()
    comp.set_element_style("artwork/0", "stroke_width", 99.0)
    host._scene.clearSelection()
    host._paste()

    pasted = [
        i for i in host._scene.items()
        if isinstance(i, ComponentItem) and i is not comp
    ]
    assert pasted[0].style_overrides.get("artwork/0").stroke_width == 2.0


def test_unstyled_component_round_trips_without_overrides(host, library):
    comp_def = _any_def(library)
    comp = ComponentItem(comp_def)
    host._scene.addItem(comp)
    comp.setSelected(True)
    host._copy()

    entry = next(e for e in host._clipboard if e["type"] == "component")
    assert "style_overrides" not in entry

    host._scene.clearSelection()
    host._paste()
    pasted = [
        i for i in host._scene.items()
        if isinstance(i, ComponentItem) and i is not comp
    ]
    assert pasted[0].style_overrides.is_empty()


def test_paste_preserves_junction_end_marker(host):
    """End markers are a junction's only user-visible property.

    Junctions are never selectable on their own; they ride along when the
    wire that terminates on them is copied.
    """
    from diagrammer.commands.connect_command import CreateConnectionCommand

    a, b = JunctionItem(), JunctionItem()
    a.setPos(QPointF(10, 10))
    b.setPos(QPointF(90, 10))
    host._scene.addItem(a)
    host._scene.addItem(b)
    host._scene.undo_stack.push(CreateConnectionCommand(host._scene, a.port, b.port))
    a.end_marker = "open"
    b.end_marker = "filled"

    for conn in host._scene.items():
        if type(conn).__name__ == "ConnectionItem":
            conn.setSelected(True)
    host._copy()
    host._scene.clearSelection()
    host._paste()

    pasted = [
        i for i in host._scene.items()
        if isinstance(i, JunctionItem) and i is not a and i is not b
    ]
    assert len(pasted) == 2
    assert sorted(j.end_marker for j in pasted) == ["filled", "open"]


def test_style_overrides_dict_round_trip():
    ovrs = ComponentStyleOverrides()
    ovrs.set("artwork/1", SvgElementStyleOverride(stroke_width=3.0, opacity=0.5))
    back = ComponentStyleOverrides.from_dict(ovrs.to_dict())
    assert back.get("artwork/1").stroke_width == 3.0
    assert back.get("artwork/1").opacity == 0.5
