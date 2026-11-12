"""Which components show their ports while a wire is being drawn.

Starting a wire used to force every port on the canvas visible, which on a
busy diagram is a field of blue dots. The ``wiring_port_reveal`` setting
picks "all", "nearby" (source + components near the cursor) or "target"
(only the snap target). Snapping is geometric, so it must not care.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.items.component_item import ComponentItem
from diagrammer.panels.settings_dialog import app_settings


@pytest.fixture
def three_parts(library):
    from diagrammer.canvas.scene import DiagramScene

    defs = [d for d in library.all_defs() if len(d.ports) >= 1]
    if not defs:
        pytest.skip("no component with ports in the library")
    scene = DiagramScene(library=library)
    comps = []
    for x in (0.0, 2000.0, 4000.0):
        c = ComponentItem(defs[0])
        c.setPos(QPointF(x, 0.0))
        scene.addItem(c)
        comps.append(c)
    return scene, comps


def _shown(comp) -> bool:
    return all(p.isVisible() for p in comp.ports)


def _hidden(comp) -> bool:
    return not any(p.isVisible() for p in comp.ports)


def test_all_mode_reveals_every_component(three_parts):
    scene, (a, b, c) = three_parts
    app_settings.wiring_port_reveal = "all"
    scene.begin_connection(a.ports[0])
    assert _shown(a) and _shown(b) and _shown(c)
    scene._cancel_connection()
    assert _hidden(a) and _hidden(b) and _hidden(c)


def test_nearby_mode_follows_the_cursor(three_parts):
    scene, (a, b, c) = three_parts
    app_settings.wiring_port_reveal = "nearby"
    scene.begin_connection(a.ports[0])
    assert _shown(a), "source component's ports should stay visible"
    assert _hidden(b) and _hidden(c)

    scene.update_connection_rubberband(b.sceneBoundingRect().center())
    assert _shown(a) and _shown(b) and _hidden(c)

    scene.update_connection_rubberband(c.sceneBoundingRect().center())
    assert _shown(a) and _hidden(b) and _shown(c)

    scene._cancel_connection()
    assert _hidden(a) and _hidden(b) and _hidden(c)


def test_target_mode_shows_only_the_snap_target(three_parts):
    scene, (a, b, c) = three_parts
    app_settings.wiring_port_reveal = "target"
    scene.begin_connection(a.ports[0])
    assert _hidden(b) and _hidden(c)

    target = b.ports[0]
    scene.update_connection_rubberband(target.scene_center())
    assert scene._current_target_port is target, "snapping must ignore visibility"
    assert target.isVisible()
    assert _hidden(c)

    # Moving away re-hides the former target.
    scene.update_connection_rubberband(QPointF(1000.0, 5000.0))
    assert scene._current_target_port is None
    assert not target.isVisible()
    scene._cancel_connection()
