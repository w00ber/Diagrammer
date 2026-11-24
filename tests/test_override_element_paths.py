"""Per-element style overrides must land on the element the user picked.

Issue #49. An override is keyed by element path (``artwork/1``): the layer
id, then the child index at each level, counted on the file as written.
Rendering removes Illustrator's bare anchor points (``<path d="M10,10"/>``)
and stretch tiling inserts clones; a positional lookup after either one
shifts the override onto a neighbour. The paths are now stamped on the
pristine tree and looked up by stamp.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from diagrammer.items.component_item import ComponentItem
from diagrammer.models.svg_style_override import (
    ComponentStyleOverrides,
    SvgElementStyleOverride,
)

RED = "#ff0000"


def _svg(extra_stretch: str = "") -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 40">'
        '<g id="artwork">'
        '<path d="M10,10"/>'
        '<line id="body" x1="10" y1="20" x2="70" y2="20" stroke="#000" stroke-width="2"/>'
        '<rect id="box" x="30" y="10" width="20" height="20" fill="none" stroke="#000" stroke-width="2"/>'
        '</g>'
        '<g id="ports">'
        '<circle id="port:left" cx="0" cy="20" r="3"/>'
        '<circle id="port:right" cx="80" cy="20" r="3"/>'
        '</g>'
        f'{extra_stretch}'
        '</svg>'
    )


@pytest.fixture
def anchored_svg(tmp_path) -> Path:
    path = tmp_path / "anchored.svg"
    path.write_text(_svg())
    return path


def _red_line_override() -> ComponentStyleOverrides:
    ovr = ComponentStyleOverrides()
    ovr.set("artwork/1", SvgElementStyleOverride(stroke_color=RED))
    return ovr


def _by_id(svg_text: str) -> dict[str, ET.Element]:
    root = ET.fromstring(svg_text)
    # Compound export prefixes ids per instance (``c0_body``).
    return {e.get("id").split("_", 1)[-1]: e for e in root.iter() if e.get("id")}


def _assert_line_red_only(svg_text: str) -> None:
    els = _by_id(svg_text)
    assert RED in (els["body"].get("style") or "")
    assert RED not in (els["box"].get("style") or "")
    assert ComponentItem._PATH_ATTR not in svg_text


def test_isolation_paths_count_the_anchor_but_do_not_offer_it(anchored_svg):
    from diagrammer.models.svg_element_info import enumerate_svg_elements

    infos = enumerate_svg_elements(anchored_svg)
    assert [(i.element_path, i.tag) for i in infos] == [
        ("artwork/1", "line"), ("artwork/2", "rect")]


def test_override_lands_on_picked_element(anchored_svg):
    out = ComponentItem._prepare_svg_bytes(
        anchored_svg, style_overrides=_red_line_override()).decode()
    _assert_line_red_only(out)


def test_unstamped_tree_still_applies_positionally(anchored_svg):
    """External callers that hand over an unedited tree keep working."""
    root = ET.parse(anchored_svg).getroot()
    ComponentItem._apply_style_overrides(root, _red_line_override())
    _assert_line_red_only(ET.tostring(root, encoding="unicode"))


def test_stretched_renderer_override_lands_on_picked_element(tmp_path, monkeypatch):
    from diagrammer.items import component_item as ci
    from diagrammer.models.component_def import ComponentDef

    path = tmp_path / "stretchy.svg"
    path.write_text(_svg(
        '<g id="stretch"><line id="stretch:v" x1="20" y1="0" x2="20" y2="40"/></g>'))
    item = ComponentItem(ComponentDef.from_svg(path))
    item.set_style_overrides(_red_line_override())

    captured: list[bytes] = []
    real = ci.svg_renderer
    monkeypatch.setattr(ci, "svg_renderer",
                        lambda data: (captured.append(data), real(data))[1])
    item.set_stretch(20.0, 0.0)
    item._get_stretched_renderer()
    assert captured
    _assert_line_red_only(captured[-1].decode())


@pytest.mark.parametrize("stretch", [0.0, 20.0])
def test_compound_export_override_lands_on_picked_element(tmp_path, stretch):
    from diagrammer.io.compound_export import _render_component
    from diagrammer.models.component_def import ComponentDef

    path = tmp_path / "stretchy.svg"
    path.write_text(_svg(
        '<g id="stretch"><line id="stretch:v" x1="20" y1="0" x2="20" y2="40"/></g>'))
    item = ComponentItem(ComponentDef.from_svg(path))
    item.set_style_overrides(_red_line_override())
    if stretch:
        item.set_stretch(stretch, 0.0)

    parent = ET.Element("{http://www.w3.org/2000/svg}g")
    _render_component(parent, item, 0.0, 0.0)
    _assert_line_red_only(ET.tostring(parent, encoding="unicode"))
