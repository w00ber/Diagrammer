"""Parity between the path-based and text-based SVG entry points.

The component inspector checks and renders a *candidate* — the original file
text with fixes applied — and must never write a temp file to do it. That needs
two text entry points beside the existing path-only ones:

* ``ComponentDef.from_svg_text`` for the parse, and
* ``prepare_svg_bytes_from_root`` for the render.

Both were split out of the path versions, so the only thing worth asserting is
that the split changed nothing: given the same document, each must agree with
the function it came from, field for field and byte for byte. If these drift,
the inspector starts validating something other than what the canvas draws.
"""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from diagrammer.io.svg_parse import parse_svg
from diagrammer.items.component_item import (
    DEFAULT_HIDDEN_LAYERS,
    ComponentItem,
    prepare_svg_bytes_from_root,
)
from diagrammer.models.component_def import ComponentDef

COMPONENTS = Path(__file__).parent.parent / "src" / "diagrammer" / "components"


def _sample_components(limit: int = 12) -> list[Path]:
    """A spread of real components: a few from each shipped library.

    Real files rather than fixtures, because the faults these entry points have
    to survive — Illustrator's namespace prefixes, inferred stretch axes, CSS
    classes — are properties of the shipped artwork, not of anything we would
    think to write by hand.
    """
    picked: list[Path] = []
    for lib in sorted(p for p in COMPONENTS.iterdir() if p.is_dir()):
        picked.extend(sorted(lib.rglob("*.svg"))[: limit // 4 or 1])
    return picked


SAMPLE = _sample_components()


@pytest.mark.skipif(not COMPONENTS.is_dir(), reason="component library not present")
@pytest.mark.parametrize("svg", SAMPLE, ids=lambda p: p.stem)
def test_from_svg_text_matches_from_svg(svg: Path) -> None:
    """Every ComponentDef field agrees between the two parse entry points.

    Including the stretch fields, which is the non-obvious half: inference runs
    through QSvgRenderer, so ``from_svg_text`` has to hand it the document bytes
    rather than the path or the axes would come back unset.
    """
    from_path = ComponentDef.from_svg(svg)
    from_text = ComponentDef.from_svg_text(svg.read_text(encoding="utf-8"), path=svg)

    for field in dataclasses.fields(ComponentDef):
        assert getattr(from_text, field.name) == getattr(from_path, field.name), (
            f"{field.name} differs for {svg.name}"
        )


@pytest.mark.skipif(not COMPONENTS.is_dir(), reason="component library not present")
@pytest.mark.parametrize("svg", SAMPLE, ids=lambda p: p.stem)
def test_prepare_from_root_matches_prepare_from_path(svg: Path) -> None:
    """The render-prep split is byte-identical, so thumbnails cannot shift."""
    from_path = ComponentItem._prepare_svg_bytes(svg)
    from_root = prepare_svg_bytes_from_root(parse_svg(svg).getroot())
    assert from_root == from_path


def _layer_display(svg_bytes: bytes) -> dict[str, str | None]:
    root = ET.fromstring(svg_bytes)
    return {
        elem.get("id"): elem.get("display")
        for elem in root.iter()
        if (elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag) == "g"
        and elem.get("id")
    }


def test_hide_layers_default_hides_the_position_only_layers() -> None:
    """The canvas default still hides ports/labels/stretch/snap/decorative."""
    svg = next(iter(SAMPLE))
    display = _layer_display(prepare_svg_bytes_from_root(parse_svg(svg).getroot()))
    hidden = {k for k, v in display.items() if v == "none"}
    assert hidden, f"{svg.name} declares none of {DEFAULT_HIDDEN_LAYERS}"
    assert hidden <= set(DEFAULT_HIDDEN_LAYERS)


def test_hide_layers_empty_keeps_every_layer_visible() -> None:
    """``hide_layers=()`` is what lets the inspector show ports at all.

    Without it the inspector would mark up geometry the viewer cannot see,
    which is the one thing a port inspector must not do.
    """
    svg = next(iter(SAMPLE))
    display = _layer_display(
        prepare_svg_bytes_from_root(parse_svg(svg).getroot(), hide_layers=())
    )
    assert display, f"{svg.name} has no identified groups"
    assert all(v is None for v in display.values()), display


def test_from_svg_text_does_not_touch_the_file(tmp_path: Path) -> None:
    """A candidate parse must never write to disk.

    ``parse_svg`` repairs duplicate attributes *in place* when ET rejects a
    file. That is right for loading a library and wrong for a candidate, whose
    text does not correspond to what is saved. ``from_svg_text`` normalizes the
    same way but only in memory.
    """
    broken = (
        '<?xml version="1.0"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns="http://www.w3.org/2000/svg"'
        ' viewBox="0 0 40 40">\n'
        '  <g id="artwork"><path d="M0,20H40"/></g>\n'
        '  <g id="ports"><circle id="port:left" cx="0" cy="20" r="3"/></g>\n'
        "</svg>\n"
    )
    target = tmp_path / "dup_attr.svg"
    target.write_text(broken, encoding="utf-8")
    before = target.read_bytes()

    comp = ComponentDef.from_svg_text(broken, path=target)

    assert target.read_bytes() == before, "from_svg_text rewrote the file"
    assert [p.name for p in comp.ports] == ["left"]
    assert comp.viewbox == (0.0, 0.0, 40.0, 40.0)


def test_from_svg_text_reads_the_text_not_the_path(tmp_path: Path) -> None:
    """The whole point: the text wins over whatever is saved at ``path``.

    This is what makes a live fix preview possible — the dialog re-checks
    candidate text while the original is still the file on disk.
    """
    saved = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">\n'
        '  <g id="ports"><circle id="port:left" cx="0" cy="20" r="3"/></g>\n'
        "</svg>\n"
    )
    candidate = saved.replace('id="port:left"', 'id="port:right"')
    target = tmp_path / "widget.svg"
    target.write_text(saved, encoding="utf-8")

    assert [p.name for p in ComponentDef.from_svg(target).ports] == ["left"]
    assert [
        p.name for p in ComponentDef.from_svg_text(candidate, path=target).ports
    ] == ["right"]
    # name and svg_path still come from the path, not the text
    comp = ComponentDef.from_svg_text(candidate, path=target)
    assert comp.name == "widget"
    assert comp.svg_path == target
