"""Illustrator's stray anchor points must never reach an export.

A ``<path d="M40.1,24.1"/>`` — a moveto with no drawing command — has no
geometry. Qt renders it as nothing or a sub-pixel speck, so it is invisible
inside Diagrammer, but it survives into exported SVG and the clipboard's
vector flavor, where VectorCraft and Illustrator show it as a stray anchor
point on the pasted object.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from diagrammer.items.component_item import ComponentItem

DRAWING = set("LlHhVvCcSsQqTtAa")
COMPONENTS = Path(__file__).parent.parent / "src" / "diagrammer" / "components"


def _degenerate_ds(svg_text: str) -> list[str]:
    """Return every ``d`` in *svg_text* that draws nothing."""
    return [d for d in re.findall(r'<[\w:]*path[^>]*\sd="([^"]*)"', svg_text)
            if not any(c in DRAWING for c in d)]


def _tag_count(svg_text: str, tag: str) -> int:
    """Count *tag* elements, ignoring however ElementTree spelled the namespace."""
    root = ET.fromstring(svg_text)
    return sum(1 for e in root.iter()
               if e.tag.split("}")[-1] == tag)


@pytest.fixture
def stray_svg(tmp_path) -> Path:
    """A component carrying one real path and one stray anchor point."""
    path = tmp_path / "stray.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<g id="artwork">'
        '<path d="M5,5 L35,35"/>'
        '<path d="M40.1,24.1"/>'
        '<path d="M12,12"/>'
        '</g>'
        '<g id="ports"><circle id="port:left" cy="20" r="3"/></g>'
        '</svg>'
    )
    return path


def test_prepare_svg_bytes_drops_stray_points(stray_svg):
    out = ComponentItem._prepare_svg_bytes(stray_svg).decode()
    assert _degenerate_ds(out) == []
    # The real geometry is untouched.
    assert "M5,5 L35,35" in out


def test_prepare_svg_bytes_keeps_paths_that_draw(tmp_path):
    path = tmp_path / "curves.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<g id="artwork">'
        '<path d="M5,5 C10,10 20,20 30,30"/>'
        '<path d="M5,5 h10 v10 Z"/>'
        '<path d="M0,0 A5,5 0 0 1 10,10"/>'
        '</g></svg>'
    )
    out = ComponentItem._prepare_svg_bytes(path).decode()
    assert _tag_count(out, "path") == 3


def test_strip_leaves_non_path_elements_alone(tmp_path):
    """Only <path> is swept; a dot drawn as a circle is real artwork."""
    path = tmp_path / "dot.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<g id="artwork"><circle cx="20" cy="20" r="2"/>'
        '<path d="M20,20"/></g></svg>'
    )
    out = ComponentItem._prepare_svg_bytes(path).decode()
    assert _tag_count(out, "circle") == 1
    assert _tag_count(out, "path") == 0


def test_stretched_component_exports_without_stray_points(tmp_path):
    """The stretch path re-parses the file, so it needs its own cleanup.

    This is the case that slipped through first: stripping only in
    ``_prepare_svg_bytes`` left stretched components still exporting the
    stray point, because ``_get_stretched_renderer`` builds its SVG from a
    fresh parse of the file.
    """
    from diagrammer.canvas.scene import DiagramScene
    from diagrammer.io import exporter
    from diagrammer.models.component_def import ComponentDef

    path = tmp_path / "stretchy.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<g id="artwork"><path d="M5,20 L35,20"/><path d="M35,20"/></g>'
        '<g id="ports">'
        '<circle id="port:left" cy="20" r="3"/>'
        '<circle id="port:right" cx="40" cy="20" r="3"/>'
        '</g>'
        '<g id="stretch"><line id="stretch:v" x1="20" y1="0" x2="20" y2="40"/></g>'
        '</svg>'
    )
    comp_def = ComponentDef.from_svg(path)
    assert comp_def.stretch_v_pos is not None

    scene = DiagramScene()
    item = ComponentItem(comp_def)
    scene.addItem(item)
    item.set_stretch(60.0, 0.0)

    rect = exporter._items_rect_with_margin(scene, 5.0)
    data = exporter._render_svg_bytes(scene, rect, 1.0)
    assert data is not None
    assert _degenerate_ds(data.decode()) == []


def test_no_shipped_component_has_stray_points():
    """Guards new parts dropped into the library against the same artifact."""
    offenders = {}
    for svg in sorted(COMPONENTS.rglob("*.svg")):
        stray = _degenerate_ds(svg.read_text())
        if stray:
            offenders[str(svg.relative_to(COMPONENTS))] = stray
    assert not offenders, (
        "stray anchor points found; run scripts/strip_stray_anchor_points.py:\n"
        + "\n".join(f"  {k}: {v}" for k, v in offenders.items())
    )


def test_exported_svg_has_no_stray_points(tmp_path, stray_svg):
    """End to end: a component with a stray point exports a clean SVG."""
    from diagrammer.canvas.scene import DiagramScene
    from diagrammer.io import exporter
    from diagrammer.models.component_def import ComponentDef

    scene = DiagramScene()
    scene.addItem(ComponentItem(ComponentDef.from_svg(stray_svg)))
    rect = exporter._items_rect_with_margin(scene, 5.0)
    data = exporter._render_svg_bytes(scene, rect, 1.0)
    assert data is not None
    text = data.decode()
    assert _degenerate_ds(text) == []
    ET.fromstring(text)  # still well-formed
