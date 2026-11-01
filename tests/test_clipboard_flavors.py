"""Pasteboard flavors placed on the clipboard by "Copy as Image".

A PDF-only pasteboard pastes into Illustrator and Preview but is invisible
to vector editors that gate their keyboard-paste path on recognising a
vector type; those apps paste the same clipboard fine from the Edit menu
and do nothing on Cmd+V.  So the copy must advertise SVG as well.

These tests never touch the real system pasteboard: the macOS writers are
monkeypatched out, and the Qt writer is in-process under the offscreen
platform plugin.
"""

import sys
from xml.etree import ElementTree

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsScene

from diagrammer.io import exporter as exp


@pytest.fixture
def clean_qt_clipboard():
    """Leave the Qt clipboard empty.

    Under the offscreen platform plugin Qt's clipboard is in-process and
    separate from NSPasteboard, so anything left on it would be seen by
    ``_try_paste_external_svg`` in later tests — which checks the Qt mime
    data before falling back to reading NSPasteboard directly.
    """
    from PySide6.QtWidgets import QApplication

    yield
    QApplication.clipboard().clear()


@pytest.fixture
def art_scene():
    """A plain QGraphicsScene with one selected item."""
    s = QGraphicsScene()
    item = s.addRect(QRectF(0, 0, 120, 80), QPen(QColor("black"), 2))
    s.addLine(0, 40, 200, 40, QPen(QColor("red"), 3))
    item.setSelected(True)
    return s


# -- flavor list ---------------------------------------------------------

def test_flavors_are_ordered_richest_first():
    flavors = exp._pasteboard_flavors(b"pdf", b"png", b"<svg/>")
    assert [uti for uti, _ in flavors] == [
        exp._UTI_PDF,
        exp._UTI_SVG,
        exp._UTI_SVG_ADOBE,
        exp._UTI_PNG,
        exp._UTI_TEXT,
    ]


def test_svg_flavors_carry_the_svg_payload():
    payloads = dict(exp._pasteboard_flavors(b"pdf", b"png", b"<svg/>"))
    assert payloads[exp._UTI_SVG] == b"<svg/>"
    assert payloads[exp._UTI_SVG_ADOBE] == b"<svg/>"
    assert payloads[exp._UTI_TEXT] == b"<svg/>"
    assert payloads[exp._UTI_PDF] == b"pdf"
    assert payloads[exp._UTI_PNG] == b"png"


def test_text_flavor_can_be_disabled(monkeypatch):
    monkeypatch.setattr(exp, "INCLUDE_SVG_AS_TEXT", False)
    utis = [uti for uti, _ in exp._pasteboard_flavors(b"pdf", b"png", b"<svg/>")]
    assert exp._UTI_TEXT not in utis
    assert exp._UTI_SVG in utis


def test_no_svg_flavors_when_svg_render_failed():
    utis = [uti for uti, _ in exp._pasteboard_flavors(b"pdf", b"png", None)]
    assert utis == [exp._UTI_PDF, exp._UTI_PNG]


# -- in-memory SVG render ------------------------------------------------

def test_render_svg_bytes_is_parseable_svg(art_scene):
    data = exp._render_svg_bytes(art_scene, QRectF(0, 0, 200, 100))
    assert data
    root = ElementTree.fromstring(data)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox") == "0 0 200 100"


def test_render_svg_bytes_honours_export_scale(art_scene):
    data = exp._render_svg_bytes(art_scene, QRectF(0, 0, 200, 100), export_scale=0.5)
    root = ElementTree.fromstring(data)
    assert root.get("viewBox") == "0 0 100 50"


def test_render_svg_bytes_rejects_empty_rect(art_scene):
    assert exp._render_svg_bytes(art_scene, QRectF(0, 0, 0, 0)) is None


# -- end to end ----------------------------------------------------------

def test_copy_selection_advertises_svg(art_scene, monkeypatch):
    """The macOS copy path reaches the writer with an SVG flavor."""
    captured: list[list[tuple[str, bytes]]] = []

    # _set_clipboard does `import sys` at call time, so this reaches it.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        exp, "_set_clipboard_macos_ctypes", lambda flavors: captured.append(flavors),
    )

    assert exp.DiagramExporter.copy_selection_to_clipboard(art_scene)
    assert exp.last_clipboard_flavors[:2] == [exp._UTI_PDF, exp._UTI_SVG]

    payloads = dict(captured[0])
    assert payloads[exp._UTI_SVG].startswith(b"<?xml")
    assert payloads[exp._UTI_PDF].startswith(b"%PDF")
    assert payloads[exp._UTI_PNG].startswith(b"\x89PNG")


def test_qt_fallback_sets_svg_mime(art_scene, clean_qt_clipboard):
    svg = exp._render_svg_bytes(art_scene, QRectF(0, 0, 200, 100))
    assert exp._set_clipboard_qt(b"%PDF-1.4", None, svg)

    from PySide6.QtWidgets import QApplication

    mime = QApplication.clipboard().mimeData()
    assert mime.hasFormat("image/svg+xml")
    assert bytes(mime.data("image/svg+xml")) == svg
    assert bytes(mime.data("application/pdf")) == b"%PDF-1.4"
