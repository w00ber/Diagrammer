"""Baked lettering must stay readable when a component is flipped or rotated.

The item transform mirrors and rotates everything the component draws, so text
inside ``<g id="upright">`` carries a compensating transform. These tests check
the composition really cancels — a mirrored "RF" is not something a unit test
catches by accident.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PySide6.QtGui import QTransform

from diagrammer.items.component_item import ComponentItem

COMPONENTS = Path(__file__).resolve().parent.parent / "src/diagrammer/components"
MIXER = COMPONENTS / "PAPER_2pt/circuits/µwave/PASSIVE/MIXER_IQ1.svg"


def _upright_children(svg_bytes: bytes) -> list[ET.Element]:
    root = ET.fromstring(svg_bytes)
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "g" and elem.get("id") == "upright":
            return list(elem)
    return []


def _parse_transform(value: str | None) -> QTransform:
    """Build the QTransform an SVG transform list describes.

    A missing attribute is the identity — knockout rects carry no transform of
    their own until the compensation is prepended.
    """
    t = QTransform()
    if not value:
        return t
    for name, args in re.findall(r"(\w+)\(([^)]*)\)", value):
        nums = [float(v) for v in args.replace(",", " ").split()]
        if name == "translate":
            t.translate(nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale":
            t.scale(nums[0], nums[1] if len(nums) > 1 else nums[0])
        elif name == "rotate":
            t.rotate(nums[0])
        else:
            raise AssertionError(f"unexpected transform {name}")
    return t


def _item_transform(width: float, height: float, flip_h: bool,
                    flip_v: bool, rotation: float) -> QTransform:
    """Mirror of ComponentItem._apply_transform."""
    cx, cy = width / 2, height / 2
    t = QTransform()
    t.translate(cx, cy)
    t.scale(-1.0 if flip_h else 1.0, -1.0 if flip_v else 1.0)
    t.rotate(rotation)
    t.translate(-cx, -cy)
    return t


def test_mixer_ships_an_upright_group():
    """The convention only works if the generator actually emits it."""
    svg = ComponentItem._prepare_svg_bytes(MIXER)
    children = _upright_children(svg)
    assert children, "MIXER_IQ1 has no <g id=\"upright\">"
    assert all(c.get("data-anchor") for c in children), \
        "every upright child needs a data-anchor to pin to"


def test_untransformed_component_is_left_alone():
    """No flip and no rotation means no compensation — and no needless churn."""
    plain = _upright_children(ComponentItem._prepare_svg_bytes(MIXER))
    before = [c.get("transform") for c in plain]
    same = _upright_children(ComponentItem._prepare_svg_bytes(
        MIXER, flip_h=False, flip_v=False, rotation=0.0))
    assert [c.get("transform") for c in same] == before


TRANSFORMS = [
    (True, False, 0.0),
    (False, True, 0.0),
    (True, True, 0.0),
    (False, False, 90.0),
    (False, False, 180.0),
    (True, False, 90.0),
    (True, False, 270.0),
    (True, True, 45.0),
]


def _baseline_and_transformed(flip_h, flip_v, rotation):
    """Pair each upright child's untransformed and compensated transform.

    A child's transform attribute also carries the glyph's own placement (the
    y-flip that converts matplotlib's y-up outline to SVG's y-down), so the
    compensation has to be judged against that baseline rather than against
    the identity.
    """
    base = _upright_children(ComponentItem._prepare_svg_bytes(MIXER))
    full = _upright_children(ComponentItem._prepare_svg_bytes(
        MIXER, flip_h=flip_h, flip_v=flip_v, rotation=rotation))
    assert len(base) == len(full) and base
    return list(zip(base, full))


@pytest.mark.parametrize("flip_h,flip_v,rotation", TRANSFORMS)
def test_lettering_renders_upright(flip_h, flip_v, rotation):
    """Composed with the item transform, the glyph keeps its original
    orientation: the compensation cancels the flip and the rotation exactly."""
    item = _item_transform(120.0, 120.0, flip_h, flip_v, rotation)

    for base_el, full_el in _baseline_and_transformed(flip_h, flip_v, rotation):
        base = _parse_transform(base_el.get("transform"))
        combined = _parse_transform(full_el.get("transform")) * item
        for a, b in ((combined.m11(), base.m11()), (combined.m12(), base.m12()),
                     (combined.m21(), base.m21()), (combined.m22(), base.m22())):
            assert a == pytest.approx(b, abs=1e-6)


@pytest.mark.parametrize("flip_h,flip_v,rotation", TRANSFORMS)
def test_label_follows_its_port(flip_h, flip_v, rotation):
    """Keeping the text upright must not pin it in place — the glyph still
    moves by exactly the displacement the item transform gives its anchor, so
    the "I" label travels to wherever the I port ended up."""
    item = _item_transform(120.0, 120.0, flip_h, flip_v, rotation)

    for base_el, full_el in _baseline_and_transformed(flip_h, flip_v, rotation):
        ax, ay = (float(v) for v in full_el.get("data-anchor").split(","))
        moved_x, moved_y = item.map(ax, ay)
        dx, dy = moved_x - ax, moved_y - ay

        base = _parse_transform(base_el.get("transform"))
        combined = _parse_transform(full_el.get("transform")) * item
        for probe in ((0.0, 0.0), (5.0, 3.0), (-2.0, 7.0)):
            gx, gy = combined.map(*probe)
            bx, by = base.map(*probe)
            assert gx == pytest.approx(bx + dx, abs=1e-6)
            assert gy == pytest.approx(by + dy, abs=1e-6)


def test_component_without_upright_group_is_untouched():
    """Components that bake no lettering must not be perturbed."""
    circ = COMPONENTS / "PAPER_2pt/circuits/µwave/PASSIVE/CIRC1.svg"
    a = ComponentItem._prepare_svg_bytes(circ)
    b = ComponentItem._prepare_svg_bytes(circ, flip_h=True, rotation=90.0)
    assert a == b
