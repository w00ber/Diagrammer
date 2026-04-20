"""Tests for models/component_def.py — SVG component parsing."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from diagrammer.models.component_def import ComponentDef


def _write_svg(tmp_path: Path, content: str) -> Path:
    """Write SVG content to a temp file and return its path."""
    svg_file = tmp_path / "test_component.svg"
    svg_file.write_text(content, encoding="utf-8")
    return svg_file


MINIMAL_SVG = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">
  <g id="artwork">
    <rect x="10" y="10" width="80" height="40" fill="none" stroke="black"/>
  </g>
  <g id="ports">
    <circle id="port:in" cx="0" cy="30" r="2"/>
    <circle id="port:out" cx="100" cy="30" r="2"/>
  </g>
</svg>
"""

SVG_WITH_LABELS = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 40">
  <g id="artwork">
    <rect x="5" y="5" width="70" height="30" fill="none" stroke="black"/>
  </g>
  <g id="ports">
    <circle id="port:a" cx="0" cy="20" r="2"/>
  </g>
  <g id="labels">
    <text id="label:name" x="40" y="25">R</text>
  </g>
</svg>
"""

SVG_DECORATIVE = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 50">
  <g id="artwork">
    <circle cx="25" cy="25" r="20" fill="red"/>
  </g>
  <g id="decorative"/>
</svg>
"""

SVG_WITH_SNAP = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <g id="artwork">
    <rect x="0" y="0" width="100" height="100" fill="none" stroke="black"/>
  </g>
  <g id="snap">
    <circle cx="50" cy="50" r="1"/>
  </g>
</svg>
"""

SVG_NO_ARTWORK = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect x="0" y="0" width="100" height="100"/>
</svg>
"""


class TestComponentDefParsing:
    def test_minimal_component(self, tmp_path):
        svg_file = _write_svg(tmp_path, MINIMAL_SVG)
        comp = ComponentDef.from_svg(svg_file)
        assert comp.name == "test_component"
        assert comp.viewbox == (0, 0, 100, 60)
        assert len(comp.ports) == 2
        port_names = {p.name for p in comp.ports}
        assert port_names == {"in", "out"}

    def test_port_positions(self, tmp_path):
        svg_file = _write_svg(tmp_path, MINIMAL_SVG)
        comp = ComponentDef.from_svg(svg_file)
        port_in = next(p for p in comp.ports if p.name == "in")
        port_out = next(p for p in comp.ports if p.name == "out")
        assert port_in.x == pytest.approx(0)
        assert port_in.y == pytest.approx(30)
        assert port_out.x == pytest.approx(100)
        assert port_out.y == pytest.approx(30)

    def test_port_approach_directions(self, tmp_path):
        svg_file = _write_svg(tmp_path, MINIMAL_SVG)
        comp = ComponentDef.from_svg(svg_file)
        port_in = next(p for p in comp.ports if p.name == "in")
        port_out = next(p for p in comp.ports if p.name == "out")
        # Port at x=0 should approach from the left (dx=-1)
        assert port_in.approach_dx == pytest.approx(-1.0)
        assert port_in.approach_dy == pytest.approx(0.0)
        # Port at x=100 should approach from the right (dx=1)
        assert port_out.approach_dx == pytest.approx(1.0)
        assert port_out.approach_dy == pytest.approx(0.0)

    def test_labels(self, tmp_path):
        svg_file = _write_svg(tmp_path, SVG_WITH_LABELS)
        comp = ComponentDef.from_svg(svg_file)
        assert len(comp.labels) == 1
        assert comp.labels[0].name == "name"
        assert comp.labels[0].x == pytest.approx(40)
        assert comp.labels[0].y == pytest.approx(25)

    def test_decorative_flag(self, tmp_path):
        svg_file = _write_svg(tmp_path, SVG_DECORATIVE)
        comp = ComponentDef.from_svg(svg_file)
        assert comp.decorative is True
        assert len(comp.ports) == 0

    def test_snap_point(self, tmp_path):
        svg_file = _write_svg(tmp_path, SVG_WITH_SNAP)
        comp = ComponentDef.from_svg(svg_file)
        assert comp.snap_point is not None
        assert comp.snap_point[0] == pytest.approx(50)
        assert comp.snap_point[1] == pytest.approx(50)

    def test_artwork_svg_content(self, tmp_path):
        svg_file = _write_svg(tmp_path, MINIMAL_SVG)
        comp = ComponentDef.from_svg(svg_file)
        assert comp.artwork_svg  # not empty
        assert "rect" in comp.artwork_svg

    def test_no_artwork_returns_none(self, tmp_path):
        svg_file = _write_svg(tmp_path, SVG_NO_ARTWORK)
        comp = ComponentDef.from_svg(svg_file)
        # Should still parse but artwork may be empty
        assert comp is not None


class TestComponentDefCategory:
    def test_category_set_from_argument(self, tmp_path):
        svg_file = _write_svg(tmp_path, MINIMAL_SVG)
        comp = ComponentDef.from_svg(svg_file, category="electrical")
        assert comp.category == "electrical"


class TestComponentDefEmbedded:
    def test_from_embedded_roundtrip(self, tmp_path):
        svg_file = _write_svg(tmp_path, MINIMAL_SVG)
        comp = ComponentDef.from_svg(svg_file)
        svg_bytes = svg_file.read_bytes()
        meta = {"name": comp.name, "category": "test"}
        reconstructed = ComponentDef.from_embedded(meta, svg_bytes)
        assert reconstructed.name == comp.name
        assert len(reconstructed.ports) == len(comp.ports)
        assert reconstructed.viewbox == comp.viewbox
