"""Pasting SVG that another app put on the macOS pasteboard.

Qt 6 has no mime converter for the standard ``public.svg-image`` UTI, so an
SVG copied out of graphulator (or Illustrator, or Inkscape) reaches
``QClipboard.mimeData()`` as nothing but a raster flavour -- paste used to be
a silent no-op.  ``diagrammer.io.pasteboard_read`` reads NSPasteboard
directly; these tests cover that, plus the precedence rule that keeps a stale
external SVG from hijacking an ordinary in-app copy/paste.

The pasteboard is global shared state, so these tests clobber the real
clipboard on a Mac.
"""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsView

from diagrammer.clipboard_ops import ClipboardMixin
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.svg_image_item import SvgImageItem

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10" fill="black"/></svg>'
)

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="NSPasteboard is macOS-only"
)


class _StatusBar:
    def __init__(self):
        self.messages: list[str] = []

    def showMessage(self, text, timeout=0):
        self.messages.append(text)


class _Host(ClipboardMixin):
    """Minimal stand-in for MainWindow: just what the mixin touches."""

    def __init__(self, scene, library):
        self._scene = scene
        self._view = QGraphicsView(scene)
        self._library = library
        self._clipboard: list = []
        self._status = _StatusBar()

    def statusBar(self):
        return self._status


@pytest.fixture
def host(library):
    from diagrammer.canvas.scene import DiagramScene
    scene = DiagramScene(library=library)
    return _Host(scene, library)


def _write_pasteboard(flavours):
    """Put *flavours* (``[(uti, bytes)]``) on the pasteboard, PDF-style."""
    AppKit = pytest.importorskip("AppKit", reason="needs PyObjC to seed the pasteboard")
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.declareTypes_owner_([uti for uti, _ in flavours], None)
    for uti, raw in flavours:
        pb.setData_forType_(AppKit.NSData.dataWithBytes_length_(raw, len(raw)), uti)


def _any_def(library):
    defs = library.all_defs()
    if not defs:
        pytest.skip("component library is empty")
    return defs[0]


def test_reads_svg_from_public_svg_image_uti():
    """The flavour Qt cannot see is the one graphulator & friends write."""
    from diagrammer.io import pasteboard_read

    _write_pasteboard([("com.adobe.pdf", b"%PDF-1.4 stub"),
                       ("public.svg-image", SVG)])
    assert pasteboard_read.read_svg() == SVG


def test_reads_svg_from_raw_mime_flavour_name():
    from diagrammer.io import pasteboard_read

    _write_pasteboard([("image/svg+xml", SVG)])
    assert pasteboard_read.read_svg() == SVG


def test_no_svg_on_pasteboard_reads_as_none():
    from diagrammer.io import pasteboard_read

    _write_pasteboard([("com.adobe.pdf", b"%PDF-1.4 stub")])
    assert pasteboard_read.read_svg() is None


def test_paste_adds_svg_item_from_native_flavour(host):
    """End to end: the UTI-only clipboard now pastes as an SvgImageItem."""
    _write_pasteboard([("com.adobe.pdf", b"%PDF-1.4 stub"),
                       ("public.svg-image", SVG)])

    assert host._try_paste_external_svg() is True
    items = [i for i in host._scene.items() if isinstance(i, SvgImageItem)]
    assert len(items) == 1
    assert items[0].svg_data == SVG


def test_internal_copy_beats_stale_external_svg(host, library):
    """An in-app copy wins over SVG that was already on the pasteboard.

    ``_copy`` never writes to the system pasteboard, so without a recency
    check every Ctrl+V after an external SVG copy would paste that SVG
    forever instead of the user's own selection.
    """
    _write_pasteboard([("public.svg-image", SVG)])

    comp = ComponentItem(_any_def(library))
    comp.setPos(QPointF(0, 0))
    host._scene.addItem(comp)
    comp.setSelected(True)
    host._copy()
    host._scene.clearSelection()

    assert host._try_paste_external_svg() is False
    host._paste()
    assert not [i for i in host._scene.items() if isinstance(i, SvgImageItem)]
    pasted = [
        i for i in host._scene.items()
        if isinstance(i, ComponentItem) and i is not comp
    ]
    assert len(pasted) == 1, "internal clipboard should have pasted the component"


def test_external_svg_copied_after_internal_copy_wins(host, library):
    """The reverse order: the newer pasteboard write takes precedence."""
    comp = ComponentItem(_any_def(library))
    host._scene.addItem(comp)
    comp.setSelected(True)
    host._copy()
    host._scene.clearSelection()

    _write_pasteboard([("public.svg-image", SVG)])

    assert host._try_paste_external_svg() is True
    assert len([i for i in host._scene.items() if isinstance(i, SvgImageItem)]) == 1
