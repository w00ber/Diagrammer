"""Display polarity (View ▸ Invert Polarity, issue #3).

Two halves: the colour rule itself, which is pure Python, and the claim the
feature rests on — the canvas and every scene.render export come out in the
same polarity, while stored colours (items, .dgm, compound export) never move.
"""

from __future__ import annotations

import re

import pytest

from diagrammer.utils import polarity as P

# ==========================================================================
# the rule
# ==========================================================================


@pytest.mark.parametrize("value, expected", [
    ("#000", "#ffffff"),
    ("#000000", "#ffffff"),
    ("#fff", "#000000"),
    ("#333", "#cccccc"),
    ("#323232", "#cdcdcd"),
    ("black", "#ffffff"),
    ("White", "#000000"),
    ("rgb(0, 0, 0)", "#ffffff"),
    ("#000 !important", "#ffffff !important"),
])
def test_neutrals_invert_lightness(value, expected) -> None:
    assert P.map_color_value(value) == expected


@pytest.mark.parametrize("value", [
    "#c66",          # port marker red
    "#e8f4fd",       # flowchart process fill: a pale tint, not a grey
    "#ff2222",
    "red",
    "none",
    "currentColor",
    "url(#abc)",     # a gradient id that happens to look like hex
    "inherit",
])
def test_everything_else_passes_through(value) -> None:
    assert P.map_color_value(value) == value


def test_mid_grey_is_a_fixed_point_and_near_blacks_flip() -> None:
    assert P.invert_rgb(128, 128, 128) == (127, 127, 127)
    r, g, b = P.invert_rgb(0x23, 0x1F, 0x20)        # Illustrator's "black"
    assert min(r, g, b) > 0xD0


def test_the_rule_is_its_own_inverse_on_every_grey() -> None:
    for v in range(256):
        once = P.invert_rgb(v, v, v)
        twice = P.invert_rgb(*once)
        assert all(abs(a - v) <= 1 for a in twice), (v, once, twice)


def test_svg_text_maps_attributes_css_and_inline_style() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        "<style>.st0{fill:none;stroke:#000;stroke-width:2px}.st1{fill:#c66}</style>"
        '<path class="st0" d="M0,0H10"/>'
        '<rect fill="#ffffff" stroke="url(#g1a)" width="1" height="1"/>'
        '<circle style="fill: #333; stroke:red" r="1"/>'
        '<stop offset="0" stop-color="#000"/>'
        "</svg>"
    )
    out = P.map_svg_text(svg)
    assert "stroke:#ffffff" in out
    assert "fill:#c66" in out
    assert 'fill="#000000"' in out
    assert 'stroke="url(#g1a)"' in out
    assert "fill: #cccccc" in out and "stroke:red" in out
    assert 'stop-color="#ffffff"' in out
    assert "stroke-width:2px" in out


def test_implicit_black_fill_is_made_explicit_on_the_root() -> None:
    out = P.map_svg_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0,0"/></svg>')
    root = re.match(r"<svg[^>]*>", out).group(0)
    assert 'fill="#ffffff"' in root and 'color="#ffffff"' in root
    # An explicit root fill is mapped, not duplicated.
    out = P.map_svg_text('<svg fill="#fff"><path d="M0,0"/></svg>')
    assert out.count("fill=") == 1 and 'fill="#000000"' in out


def test_implicit_fill_reaches_a_namespace_prefixed_root() -> None:
    """ElementTree writes <ns0:svg>; the shipped circulator's arrowheads are
    un-styled polygons and vanished (black on black) when this was missed."""
    out = P.map_svg_text('<ns0:svg xmlns:ns0="http://www.w3.org/2000/svg">'
                         '<ns0:polygon points="0 0 1 1 0 1"/></ns0:svg>')
    assert re.match(r'<ns0:svg[^>]*fill="#ffffff"', out)


def test_bytes_pass_through_untouched_when_not_inverted() -> None:
    data = b'<svg><path style="stroke:#000"/></svg>'
    assert P.map_svg_bytes(data) is data


# ==========================================================================
# the canvas and its exports
# ==========================================================================


@pytest.fixture
def inverted():
    """Polarity is process-wide; never let a failing test leave it on."""
    P.set_inverted(False)
    yield lambda on: P.set_inverted(on)
    P.set_inverted(False)


@pytest.fixture
def d():
    from diagrammer.headless import Diagram, load_library
    lib = load_library(include_overlay=False, include_custom=False)
    diagram = Diagram(library=lib)
    diagram.add("PAPER_2pt/circuits/LCR/CAP1", (0, 0), anchor="top", id="C")
    diagram.attach("PAPER_2pt/circuits/gnds/GND1", "top", to=("C", "bottom"),
                   direction="down", id="G")
    diagram.text("label", (80, 40), id="T")
    return diagram


def _luma_histogram(png: bytes) -> tuple[int, int]:
    """(dark pixels, light pixels) of a rendered PNG."""
    from PySide6.QtGui import QImage
    img = QImage.fromData(png)
    dark = light = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            v = c.lightness()
            if v < 60:
                dark += 1
            elif v > 200:
                light += 1
    return dark, light


def test_render_follows_polarity_and_toggling_back_is_exact(d, inverted) -> None:
    normal = d.render_png()
    n_dark, n_light = _luma_histogram(normal)
    assert n_light > 5 * n_dark, "black ink on a white page"

    inverted(True)
    d.scene.refresh_polarity()
    flipped = d.render_png()
    f_dark, f_light = _luma_histogram(flipped)
    assert f_dark > 5 * f_light, "white ink on a black page"
    # Same ink, same page, swapped: the counts trade places (within AA noise).
    assert f_light == pytest.approx(n_dark, rel=0.25)

    inverted(False)
    d.scene.refresh_polarity()
    assert d.render_png() == normal


def test_stored_colours_never_change(d, inverted) -> None:
    from diagrammer.items.annotation_item import AnnotationItem
    from diagrammer.items.connection_item import ConnectionItem
    wire = next(i for i in d.scene.items() if isinstance(i, ConnectionItem))
    text = next(i for i in d.scene.items() if isinstance(i, AnnotationItem))
    before = (wire.line_color.name(), text.text_color.name())
    inverted(True)
    d.scene.refresh_polarity()
    assert (wire.line_color.name(), text.text_color.name()) == before


def _ink_on_grey(svg_path) -> tuple[int, int]:
    """Render an exported (transparent-page) SVG onto mid-grey and count
    (black ink, white ink) pixels. Asserting on what is *drawn* rather than
    on colour strings: QSvgGenerator wraps everything in state groups that
    carry the painter's default black pen whether or not it paints."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(str(svg_path))
    size = r.defaultSize() * 3
    img = QImage(size, QImage.Format.Format_ARGB32)
    img.fill(QColor(128, 128, 128))
    p = QPainter(img)
    r.render(p, QRectF(0, 0, size.width(), size.height()))
    p.end()
    black = white = 0
    for y in range(img.height()):
        for x in range(img.width()):
            v = img.pixelColor(x, y).lightness()
            black += v < 40
            white += v > 215
    return black, white


def test_svg_export_carries_the_polarity(d, inverted, tmp_path) -> None:
    d.export(tmp_path / "normal.svg")
    inverted(True)
    d.scene.refresh_polarity()
    d.export(tmp_path / "inverted.svg")

    n_black, n_white = _ink_on_grey(tmp_path / "normal.svg")
    f_black, f_white = _ink_on_grey(tmp_path / "inverted.svg")
    assert n_black > 50 and n_white < n_black / 10
    assert f_white > 50 and f_black < f_white / 10
    assert f_white == pytest.approx(n_black, rel=0.25)


def test_compound_export_stays_canonical(d, inverted, tmp_path) -> None:
    """Its output is a library part; the canvas inverts it when placed."""
    from diagrammer.io.compound_export import export_compound_component
    items = [i for i in d.scene.items() if i.parentItem() is None]
    inverted(False)
    export_compound_component(d.scene, items, tmp_path / "a.svg", "a")
    inverted(True)
    d.scene.refresh_polarity()
    export_compound_component(d.scene, items, tmp_path / "b.svg", "b")
    strip = lambda p: re.sub(r'id="[^"]*"', "", p.read_text())  # noqa: E731
    assert strip(tmp_path / "a.svg").replace(">a<", ">b<") == \
        strip(tmp_path / "b.svg")


def test_stretched_component_rerenders_on_toggle(inverted) -> None:
    from diagrammer.headless import Diagram, load_library
    lib = load_library(include_overlay=False, include_custom=False)
    d = Diagram(library=lib)
    d.add("PAPER_2pt/circuits/µwave/coax and twp/COAX1_MED", (0, 0),
          anchor="left", id="TL")
    d.stretch("TL", by=80)
    normal = d.render_png()
    inverted(True)
    d.scene.refresh_polarity()
    assert d.render_png() != normal
    n_dark, _ = _luma_histogram(normal)
    _, f_light = _luma_histogram(d.render_png())
    assert f_light == pytest.approx(n_dark, rel=0.25)
