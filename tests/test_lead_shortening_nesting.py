"""Lead shortening must reach leads nested inside sub-groups.

Illustrator nests its own groups inside a layer, and 14 shipped components
keep their leads two deep — ``<g id="leads"><g id="leads1"><g id="leads2">
<path/>``. Looking only one level down found a ``<g>``, skipped it, and
silently shortened nothing: the wire's rounded corner then overlapped the
component instead of meeting it. Invisible unless you knew what the join is
supposed to look like.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from diagrammer.items.component_item import ComponentItem
from diagrammer.io.svg_parse import parse_svg
from diagrammer.models.component_def import ComponentDef

COMPONENTS = Path(__file__).parent.parent / "src" / "diagrammer" / "components"

NESTED = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 100">
  <g id="artwork"><path d="M10,50H50"/></g>
  <g id="leads">
    <g id="leads1">
      <g id="leads2">
        <line id="deep" x1="30" y1="0" x2="30" y2="40"/>
      </g>
    </g>
    <line id="shallow" x1="30" y1="100" x2="30" y2="60"/>
  </g>
  <g id="ports">
    <circle id="port:top" cx="30" cy="0" r="3"/>
    <circle id="port:bottom" cx="30" cy="100" r="3"/>
  </g>
</svg>
'''


def _shorten(text: str, amount: float = 8.0):
    root = ET.fromstring(text)
    comp = ComponentDef.from_svg_text(text, path=Path("x.svg"),
                                      infer_stretch=False)
    shortening = {p.name: amount for p in comp.ports}
    xy = [(p.x, p.y) for p in comp.ports]
    names = {(round(p.x, 1), round(p.y, 1)): p.name for p in comp.ports}
    ComponentItem._shorten_leads_in_svg(root, shortening, xy, names)
    return {el.get("id"): (el.get("y1"), el.get("y2"))
            for el in root.iter()
            if (el.tag.split("}")[-1]) == "line" and el.get("id")}


def test_a_lead_nested_two_groups_deep_is_shortened() -> None:
    ends = _shorten(NESTED)
    assert ends["deep"][0] != "0", (
        "the nested lead should have been pulled back from its port")


def test_a_direct_child_lead_still_works() -> None:
    ends = _shorten(NESTED)
    assert ends["shallow"][0] != "100"


def test_each_lead_is_shortened_exactly_once() -> None:
    """A tagged lead layer nested inside another would otherwise be walked
    twice and shortened twice."""
    doubled = NESTED.replace('<g id="leads1">', '<g id="leads-top">').replace(
        "</g>\n    </g>", "</g>\n    </g>", 1)
    once = _shorten(NESTED)["deep"]
    twice = _shorten(doubled)["deep"]
    assert once == twice, "nesting a tagged layer must not double the shortening"


@pytest.mark.skipif(not COMPONENTS.is_dir(), reason="components not present")
@pytest.mark.parametrize("rel", [
    "PAPER_2pt/circuits/µwave/filters/FILTER_HP.svg",
    "PAPER_2pt/circuits/µwave/PASSIVE/BIAS_TEE0.svg",
    "PAPER_2pt/circuits/µwave/PASSIVE/ISOLATOR1.svg",
])
def test_shipped_components_with_nested_leads_now_shorten(rel: str) -> None:
    path = COMPONENTS / rel
    if not path.is_file():
        pytest.skip(f"{rel} not present")
    comp = ComponentDef.from_svg(path)
    shortening = {p.name: 8.0 for p in comp.ports}
    xy = [(p.x, p.y) for p in comp.ports]
    names = {(round(p.x, 1), round(p.y, 1)): p.name for p in comp.ports}

    root = parse_svg(path).getroot()
    before = ET.tostring(root, encoding="unicode")
    ComponentItem._shorten_leads_in_svg(root, shortening, xy, names)
    assert ET.tostring(root, encoding="unicode") != before, (
        f"{rel} still shortens nothing")
