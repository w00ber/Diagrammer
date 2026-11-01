"""DiagramExporter -- renders the current QGraphicsScene to SVG, PNG, or PDF files."""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsScene

logger = logging.getLogger(__name__)


def _get_export_scale() -> float:
    """Return the user's export scale factor (0.0–1.0+), default 1.0."""
    try:
        from diagrammer.panels.settings_dialog import app_settings
        return app_settings.export_scale
    except Exception:
        logger.debug("Could not load export scale from settings; using default 1.0")
        return 1.0


class DiagramExporter:
    """Static methods for exporting a DiagramScene to various file formats.

    All export methods compute the bounding rect of scene items via
    ``scene.itemsBoundingRect()`` and add an optional margin so that the
    exported image is neatly padded.

    An export scale factor (configured in Settings > Export) is applied
    to every output format so that, e.g., a 100 pt object at 33 %
    becomes ~33 pt in the exported file.

    Note on the grid: the grid is drawn by ``DiagramView.drawBackground``,
    **not** by the scene itself.  Calling ``scene.render()`` therefore never
    includes the grid regardless of the *include_grid* flag.  The parameter
    is accepted for forward-compatibility (e.g. if grid rendering is later
    moved into the scene) but currently has no visible effect.
    """

    # ------------------------------------------------------------------
    # SVG
    # ------------------------------------------------------------------

    @staticmethod
    def export_svg(
        scene: QGraphicsScene,
        path: str,
        include_grid: bool = False,
        margin: float = 20.0,
    ) -> None:
        """Render *scene* to an SVG file at *path*.

        Parameters
        ----------
        scene:
            The graphics scene to export.
        path:
            Destination file path (should end in ``.svg``).
        include_grid:
            Reserved for future use.  The grid is drawn by the view, not the
            scene, so it will not appear in the export regardless.
        margin:
            Extra padding (in scene units) around the items bounding rect.
        """
        from PySide6.QtSvg import QSvgGenerator

        source_rect = _items_rect_with_margin(scene, margin)
        scale = _get_export_scale()
        out_w = source_rect.width() * scale
        out_h = source_rect.height() * scale

        generator = QSvgGenerator()
        generator.setFileName(path)
        generator.setSize(QSize(int(out_w), int(out_h)))
        generator.setViewBox(QRectF(0, 0, out_w, out_h))
        generator.setTitle("Diagrammer Export")
        generator.setDescription("Exported from Diagrammer")

        selected_items = scene.selectedItems()
        _clear_selection_visuals(scene)
        try:
            painter = QPainter()
            painter.begin(generator)
            target_rect = QRectF(0, 0, out_w, out_h)
            scene.render(painter, target_rect, source_rect)
            painter.end()
        finally:
            _restore_selection_visuals(scene, selected_items)

    # ------------------------------------------------------------------
    # PNG
    # ------------------------------------------------------------------

    @staticmethod
    def export_png(
        scene: QGraphicsScene,
        path: str,
        dpi: int = 300,
        include_grid: bool = False,
        margin: float = 20.0,
    ) -> None:
        """Render *scene* to a PNG file at *path*.

        Parameters
        ----------
        scene:
            The graphics scene to export.
        path:
            Destination file path (should end in ``.png``).
        dpi:
            Output resolution in dots per inch.  The default (300) produces
            print-quality images.  The scene is rendered at 1:1 at 96 dpi
            so a higher *dpi* results in a proportionally larger pixel image.
        include_grid:
            Reserved for future use.
        margin:
            Extra padding (in scene units) around the items bounding rect.
        """
        selected_items = scene.selectedItems()
        _clear_selection_visuals(scene)
        try:
            image = render_scene_to_qimage(
                scene, dpi=dpi, margin=margin,
                background=QColor(Qt.GlobalColor.white),
                export_scale=_get_export_scale(),
            )
            image.save(path)
        finally:
            _restore_selection_visuals(scene, selected_items)

    # ------------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------------

    @staticmethod
    def export_pdf(
        scene: QGraphicsScene,
        path: str,
        include_grid: bool = False,
        margin: float = 20.0,
    ) -> None:
        """Render *scene* to a PDF file at *path*.

        Parameters
        ----------
        scene:
            The graphics scene to export.
        path:
            Destination file path (should end in ``.pdf``).
        include_grid:
            Reserved for future use.
        margin:
            Extra padding (in scene units) around the items bounding rect.
        """
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize

        source_rect = _items_rect_with_margin(scene, margin)
        scale = _get_export_scale()
        scaled_size = QSizeF(source_rect.width() * scale, source_rect.height() * scale)

        # QPrinter lives in QtPrintSupport.
        from PySide6.QtPrintSupport import QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)

        # Set a custom page size matching the scaled rect (in points; 1 pt = 1/72 in).
        page_size = QPageSize(scaled_size, QPageSize.Unit.Point)
        page_layout = QPageLayout(page_size, QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0))
        printer.setPageLayout(page_layout)

        selected_items = scene.selectedItems()
        _clear_selection_visuals(scene)
        try:
            painter = QPainter()
            painter.begin(printer)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # The printer's paint device has its own resolution (typically 1200 dpi).
            # Map the scene source rect into the full printable area.
            printer_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            target_rect = QRectF(printer_rect)
            scene.render(painter, target_rect, source_rect)
            painter.end()
        finally:
            _restore_selection_visuals(scene, selected_items)

    # ------------------------------------------------------------------
    # Clipboard (selection → system clipboard)
    # ------------------------------------------------------------------

    @staticmethod
    def copy_selection_to_clipboard(
        scene: QGraphicsScene,
        dpi: int = 300,
        margin: float = 5.0,
    ) -> bool:
        """Copy the selected items to the system clipboard as PDF + SVG + PNG.

        On macOS the PDF and SVG are placed on the pasteboard via native
        APIs so that Illustrator, Keynote, PowerPoint, and SVG-native
        editors receive editable vector data.  A high-DPI PNG is included
        as a universal fallback.

        Selection highlights, handles, and ports are temporarily hidden so
        the clipboard image shows a clean "presentation" view.

        Returns ``True`` if something was copied, ``False`` if the selection
        was empty.
        """
        source_rect = _selection_rect_with_margin(scene, margin)
        if source_rect.isNull() or source_rect.isEmpty():
            return False

        export_scale = _get_export_scale()
        selected_items = scene.selectedItems()
        _clear_selection_visuals(scene)

        try:
            pdf_data = _render_pdf_bytes(scene, source_rect, export_scale)
            png_data = _render_png_bytes(scene, source_rect, dpi, export_scale)
            svg_data = _render_svg_bytes(scene, source_rect, export_scale)
        finally:
            _restore_selection_visuals(scene, selected_items)

        return _set_clipboard(pdf_data, png_data, svg_data)

    @staticmethod
    def copy_all_to_clipboard(
        scene: QGraphicsScene,
        dpi: int = 300,
        margin: float = 5.0,
    ) -> bool:
        """Copy the entire scene to the system clipboard (PDF + SVG + PNG).

        Convenience wrapper that uses the full items bounding rect instead
        of the selection rect.  Returns ``True`` on success.
        """
        source_rect = _items_rect_with_margin(scene, margin)
        if source_rect.isNull() or source_rect.isEmpty():
            return False

        export_scale = _get_export_scale()
        selected_items = scene.selectedItems()
        _clear_selection_visuals(scene)

        try:
            pdf_data = _render_pdf_bytes(scene, source_rect, export_scale)
            png_data = _render_png_bytes(scene, source_rect, dpi, export_scale)
            svg_data = _render_svg_bytes(scene, source_rect, export_scale)
        finally:
            _restore_selection_visuals(scene, selected_items)

        return _set_clipboard(pdf_data, png_data, svg_data)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _items_rect_with_margin(scene: QGraphicsScene, margin: float) -> QRectF:
    """Return the bounding rect of all scene items, expanded by *margin*.

    If the scene is empty the function returns a small default rect so that
    exports don't produce a zero-size output.
    """
    rect = scene.itemsBoundingRect()
    if rect.isNull() or rect.isEmpty():
        rect = QRectF(-50, -50, 100, 100)
    rect = rect.adjusted(-margin, -margin, margin, margin)
    return rect


def _selection_rect_with_margin(scene: QGraphicsScene, margin: float) -> QRectF:
    """Return the united bounding rect of the currently selected items.

    Falls back to the full items bounding rect when nothing is selected.
    """
    selected = scene.selectedItems()
    if not selected:
        return _items_rect_with_margin(scene, margin)

    rect = QRectF()
    for item in selected:
        rect = rect.united(item.sceneBoundingRect())
    if rect.isNull() or rect.isEmpty():
        return _items_rect_with_margin(scene, margin)
    rect = rect.adjusted(-margin, -margin, margin, margin)
    return rect


# -- Selection visual suppression ----------------------------------------

def _clear_selection_visuals(scene: QGraphicsScene) -> None:
    """Temporarily deselect all items so paint() methods skip highlights.

    Also hides ports on selected components since those become visible
    when their parent is selected.
    """
    for item in scene.selectedItems():
        item.setSelected(False)
        # Hide ports that were visible due to selection
        if hasattr(item, '_ports'):
            for port in item._ports:
                if not getattr(port, 'is_alignment_selected', False):
                    port.setVisible(False)


def _restore_selection_visuals(
    scene: QGraphicsScene,
    items: list,
) -> None:
    """Re-select *items* and restore port visibility."""
    for item in items:
        item.setSelected(True)
        if hasattr(item, '_update_port_visibility'):
            item._update_port_visibility()


# -- Platform-aware clipboard writer -------------------------------------

# Clipboard method used for the most recent copy (for diagnostics).
# One of: "native", "subprocess", "qt", or "failed".
last_clipboard_method: str = ""

# Flavors present on the most recent copy, as UTIs (for diagnostics).
last_clipboard_flavors: list[str] = []

# macOS pasteboard UTIs.  Spelled out rather than imported from AppKit so
# that the ctypes and subprocess paths agree with the PyObjC one.
_UTI_PDF = "com.adobe.pdf"
_UTI_SVG = "public.svg-image"
_UTI_SVG_ADOBE = "com.adobe.svg"
_UTI_PNG = "public.png"
_UTI_TEXT = "public.utf8-plain-text"

# Also advertise the SVG source as plain text.  Illustrator does this, and
# several vector editors (Figma, Sketch) read SVG from the pasteboard only
# through the text flavor.  The cost is that pasting into a *plain-text*
# field — BBEdit, a terminal, a code editor — yields SVG source instead of
# nothing.  Rich-text and image consumers are unaffected: they ask for
# their own flavor and never see this one.  Set to False to drop it.
INCLUDE_SVG_AS_TEXT = True


def _pasteboard_flavors(
    pdf_data: bytes | None,
    png_data: bytes | None,
    svg_data: bytes | None,
) -> list[tuple[str, bytes]]:
    """Build the (UTI, payload) list to place on the macOS pasteboard.

    Ordered richest-first, which is what Illustrator does and what an app
    that walks an item's types in order expects.  The text flavor goes
    last so that nothing scanning in order prefers it over the artwork.
    """
    flavors: list[tuple[str, bytes]] = []
    if pdf_data:
        flavors.append((_UTI_PDF, bytes(pdf_data)))
    if svg_data:
        flavors.append((_UTI_SVG, bytes(svg_data)))
        flavors.append((_UTI_SVG_ADOBE, bytes(svg_data)))
    if png_data:
        flavors.append((_UTI_PNG, bytes(png_data)))
    if svg_data and INCLUDE_SVG_AS_TEXT:
        flavors.append((_UTI_TEXT, bytes(svg_data)))
    return flavors


def _set_clipboard(
    pdf_data: bytes | None,
    png_data: bytes | None,
    svg_data: bytes | None = None,
) -> bool:
    """Place *pdf_data*, *svg_data* and *png_data* on the system clipboard.

    On macOS, uses the Objective-C runtime via ctypes to write a single
    ``NSPasteboardItem`` carrying every flavor with its correct UTI
    (``com.adobe.pdf``, ``public.svg-image``, ``public.png``, ...).  This
    requires no third-party packages — only ``libobjc.dylib``, which ships
    with every macOS install.

    The SVG flavor matters beyond file fidelity: some vector editors gate
    their *keyboard* paste path on the pasteboard advertising a vector
    type they recognise, and fall back to a generic importer only for
    menu and context-menu paste.  A PDF-only pasteboard pastes fine from
    the menu in those apps and does nothing on Cmd+V.

    On other platforms falls back to Qt's QMimeData.

    Sets the module-level ``last_clipboard_method`` and
    ``last_clipboard_flavors`` to indicate which path was taken.
    """
    global last_clipboard_method, last_clipboard_flavors
    import sys

    if sys.platform == "darwin" and pdf_data:
        flavors = _pasteboard_flavors(pdf_data, png_data, svg_data)

        # 1) ctypes — always available, no dependencies
        try:
            _set_clipboard_macos_ctypes(flavors)
            last_clipboard_method = "native (ctypes)"
            last_clipboard_flavors = [uti for uti, _ in flavors]
            return True
        except Exception:
            logger.debug("ctypes clipboard method failed, trying next", exc_info=True)

        # 2) PyObjC — if installed in this interpreter
        try:
            _set_clipboard_macos_pyobjc(flavors)
            last_clipboard_method = "native (PyObjC)"
            last_clipboard_flavors = [uti for uti, _ in flavors]
            return True
        except Exception:
            logger.debug("PyObjC clipboard method failed, trying next", exc_info=True)

        # 3) subprocess — try system Python which usually has PyObjC
        ok, method = _set_clipboard_macos_subprocess(flavors)
        if ok:
            last_clipboard_method = method
            last_clipboard_flavors = [uti for uti, _ in flavors]
            return True

    last_clipboard_method = "qt"
    last_clipboard_flavors = []
    return _set_clipboard_qt(pdf_data, png_data, svg_data)


# -- macOS: ctypes (no dependencies) ------------------------------------

def _set_clipboard_macos_ctypes(flavors: list[tuple[str, bytes]]) -> None:
    """Write *flavors* to the macOS pasteboard via the ObjC runtime.

    Uses ctypes to call ``libobjc.dylib`` directly — works on every macOS
    install without any third-party packages.  Writes one
    ``NSPasteboardItem`` via ``-writeObjects:`` (the modern API) so that a
    copy bumps ``changeCount`` exactly once; the older
    ``-declareTypes:owner:`` path bumped it twice per copy, which apps
    that poll ``changeCount`` see as a phantom second change.

    Raises on failure.
    """
    import ctypes
    import ctypes.util

    if not flavors:
        raise ValueError("no pasteboard flavors to write")

    # Load the Objective-C runtime
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))

    # Configure objc_msgSend signatures
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    # Generic messenger — we cast per-call as needed
    msg = objc.objc_msgSend
    msg.restype = ctypes.c_void_p
    msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    def cls(name: str) -> ctypes.c_void_p:
        return objc.objc_getClass(name.encode())

    def sel(name: str) -> ctypes.c_void_p:
        return objc.sel_registerName(name.encode())

    def send(obj, selector, *args):
        return msg(obj, selector, *args)

    # NSData from bytes
    def make_nsdata(raw: bytes) -> ctypes.c_void_p:
        NSData = cls("NSData")
        buf = ctypes.create_string_buffer(raw)
        # +[NSData dataWithBytes:length:]
        fn = ctypes.cast(msg, ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_uint64,
        ))
        return fn(
            NSData, sel("dataWithBytes:length:"),
            buf, len(raw),
        )

    # NSString from Python str
    def make_nsstring(s: str) -> ctypes.c_void_p:
        NSString = cls("NSString")
        encoded = s.encode("utf-8")
        buf = ctypes.create_string_buffer(encoded)
        fn = ctypes.cast(msg, ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_uint64,
        ))
        return fn(
            NSString, sel("stringWithUTF8String:"),
            buf, 0,  # second arg ignored for this selector
        )

    # NSArray from list of ObjC objects
    def make_nsarray(items: list) -> ctypes.c_void_p:
        NSArray = cls("NSArray")
        arr_type = ctypes.c_void_p * len(items)
        c_arr = arr_type(*items)
        fn = ctypes.cast(msg, ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint64,
        ))
        return fn(
            NSArray, sel("arrayWithObjects:count:"),
            c_arr, len(items),
        )

    # Build one NSPasteboardItem carrying every flavor.
    NSPasteboardItem = cls("NSPasteboardItem")
    item = send(send(NSPasteboardItem, sel("alloc")), sel("init"))
    if not item:
        raise RuntimeError("Failed to allocate NSPasteboardItem")

    fn_set = ctypes.cast(msg, ctypes.CFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    ))
    for uti, payload in flavors:
        if not fn_set(
            item, sel("setData:forType:"),
            make_nsdata(payload), make_nsstring(uti),
        ):
            raise RuntimeError(f"NSPasteboardItem rejected flavor {uti}")

    # Get the general pasteboard, clear it (one changeCount bump), write.
    NSPasteboard = cls("NSPasteboard")
    pb = send(NSPasteboard, sel("generalPasteboard"))
    if not pb:
        raise RuntimeError("Failed to get NSPasteboard")

    send(pb, sel("clearContents"))

    fn_write = ctypes.cast(msg, ctypes.CFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ))
    if not fn_write(pb, sel("writeObjects:"), make_nsarray([item])):
        raise RuntimeError("NSPasteboard writeObjects: failed")

    send(item, sel("release"))


# -- macOS: PyObjC -------------------------------------------------------

def _set_clipboard_macos_pyobjc(flavors: list[tuple[str, bytes]]) -> None:
    """Write *flavors* to the macOS pasteboard via AppKit (PyObjC).

    Raises ImportError if PyObjC is not available.
    """
    from AppKit import NSPasteboard, NSPasteboardItem

    if not flavors:
        raise ValueError("no pasteboard flavors to write")

    item = NSPasteboardItem.alloc().init()
    for uti, payload in flavors:
        if not item.setData_forType_(payload, uti):
            raise RuntimeError(f"NSPasteboardItem rejected flavor {uti}")

    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    if not pb.writeObjects_([item]):
        raise RuntimeError("NSPasteboard writeObjects: failed")


# -- macOS: subprocess ----------------------------------------------------

def _set_clipboard_macos_subprocess(
    flavors: list[tuple[str, bytes]],
) -> tuple[bool, str]:
    """Fallback: write *flavors* to the macOS pasteboard via a subprocess.

    Tries several Python interpreters that may have AppKit available.
    Each payload is handed over as a temp file.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    if not flavors:
        return False, ""

    tmp_paths: list[str] = []
    try:
        pairs: list[tuple[str, str]] = []
        for uti, payload in flavors:
            fd, tmp_path = tempfile.mkstemp(suffix=".bin")
            os.close(fd)
            tmp_paths.append(tmp_path)
            with open(tmp_path, "wb") as f:
                f.write(payload)
            pairs.append((uti, tmp_path))

        script = _PASTEBOARD_SCRIPT.format(flavors=repr(pairs))

        candidates = ["/usr/bin/python3"]
        for name in ("python3", "python"):
            found = shutil.which(name)
            if found and found not in candidates:
                candidates.append(found)

        for python in candidates:
            try:
                result = subprocess.run(
                    [python, "-c", script],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return True, f"subprocess ({python})"
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("Subprocess clipboard via %s failed: %s", python, exc)
                continue

        return False, ""
    except Exception:
        logger.debug("Subprocess clipboard fallback failed entirely", exc_info=True)
        return False, ""
    finally:
        for tmp_path in tmp_paths:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


_PASTEBOARD_SCRIPT = '''\
from AppKit import NSPasteboard, NSPasteboardItem

flavors = {flavors}

item = NSPasteboardItem.alloc().init()
for uti, path in flavors:
    with open(path, "rb") as f:
        payload = f.read()
    if not item.setData_forType_(payload, uti):
        raise SystemExit("rejected flavor " + uti)

pb = NSPasteboard.generalPasteboard()
pb.clearContents()
if not pb.writeObjects_([item]):
    raise SystemExit("writeObjects: failed")
'''


def _set_clipboard_qt(
    pdf_data: bytes | None,
    png_data: bytes | None,
    svg_data: bytes | None = None,
) -> bool:
    """Write to clipboard via Qt QMimeData (Linux / Windows fallback)."""
    from PySide6.QtCore import QMimeData

    mime = QMimeData()
    if pdf_data:
        mime.setData("application/pdf", QByteArray(pdf_data))
    if svg_data:
        # Inkscape and GTK apps read vector artwork from image/svg+xml.
        mime.setData("image/svg+xml", QByteArray(svg_data))
    if png_data:
        image = QImage()
        image.loadFromData(QByteArray(png_data), "PNG")
        if not image.isNull():
            mime.setImageData(image)

    QApplication.clipboard().setMimeData(mime)
    return True


# -- Rendering helpers ---------------------------------------------------

def render_scene_to_qimage(
    scene: QGraphicsScene,
    dpi: int = 96,
    margin: float = 10.0,
    background: QColor | None = None,
    export_scale: float = 1.0,
) -> QImage:
    """Render *scene* to a QImage at the given DPI.

    Used by both PNG export and the Examples dialog previews. Scene coords
    are treated as nominally 96 dpi, so a *dpi* of 192 produces a 2x image.

    *export_scale* shrinks or enlarges the output dimensions relative to
    the scene size (e.g. 0.33 produces an image 33 % as large).

    If *background* is None the image is transparent. The returned QImage
    has its physical DPI set so that downstream consumers (e.g. ``QImage.save``)
    embed the correct resolution.
    """
    source_rect = _items_rect_with_margin(scene, margin)

    scale = dpi / 96.0 * export_scale
    pixel_width = max(1, int(source_rect.width() * scale))
    pixel_height = max(1, int(source_rect.height() * scale))

    # Guard against allocating an image too large for available memory
    megapixels = pixel_width * pixel_height / 1_000_000
    if megapixels > 100:
        logger.warning(
            "Export image would be %.0f MP (%d x %d px) — capping to avoid "
            "excessive memory usage. Reduce DPI or export scale.",
            megapixels, pixel_width, pixel_height,
        )
        # Scale down proportionally to fit within 100 MP
        shrink = (100_000_000 / (pixel_width * pixel_height)) ** 0.5
        pixel_width = max(1, int(pixel_width * shrink))
        pixel_height = max(1, int(pixel_height * shrink))

    image = QImage(
        QSize(pixel_width, pixel_height),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.setDotsPerMeterX(int(dpi / 0.0254))
    image.setDotsPerMeterY(int(dpi / 0.0254))
    image.fill(background if background is not None else Qt.GlobalColor.transparent)

    painter = QPainter()
    painter.begin(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    target_rect = QRectF(0, 0, pixel_width, pixel_height)
    scene.render(painter, target_rect, source_rect)
    painter.end()

    return image


def _render_png_bytes(
    scene: QGraphicsScene,
    source_rect: QRectF,
    dpi: int,
    export_scale: float = 1.0,
) -> bytes | None:
    """Render *source_rect* of *scene* to PNG bytes at the given DPI."""
    from PySide6.QtCore import QBuffer, QIODevice

    scale = dpi / 96.0 * export_scale
    pixel_w = max(1, int(source_rect.width() * scale))
    pixel_h = max(1, int(source_rect.height() * scale))

    # Guard against allocating an image too large for available memory
    megapixels = pixel_w * pixel_h / 1_000_000
    if megapixels > 100:
        logger.warning(
            "Export image would be %.0f MP (%d x %d px) — capping to avoid "
            "excessive memory usage.",
            megapixels, pixel_w, pixel_h,
        )
        shrink = (100_000_000 / (pixel_w * pixel_h)) ** 0.5
        pixel_w = max(1, int(pixel_w * shrink))
        pixel_h = max(1, int(pixel_h * shrink))

    image = QImage(QSize(pixel_w, pixel_h), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDotsPerMeterX(int(dpi / 0.0254))
    image.setDotsPerMeterY(int(dpi / 0.0254))
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter()
    painter.begin(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scene.render(painter, QRectF(0, 0, pixel_w, pixel_h), source_rect)
    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def _render_pdf_bytes(
    scene: QGraphicsScene,
    source_rect: QRectF,
    export_scale: float = 1.0,
) -> bytes | None:
    """Render *source_rect* of *scene* to PDF bytes."""
    try:
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrinter
    except ImportError:
        return None

    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)

    try:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(tmp_path)
        scaled_size = QSizeF(
            source_rect.width() * export_scale,
            source_rect.height() * export_scale,
        )
        page_size = QPageSize(scaled_size, QPageSize.Unit.Point)
        page_layout = QPageLayout(
            page_size, QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)
        )
        printer.setPageLayout(page_layout)

        painter = QPainter()
        painter.begin(printer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        printer_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
        scene.render(painter, QRectF(printer_rect), source_rect)
        painter.end()

        with open(tmp_path, "rb") as f:
            return f.read()
    except Exception:
        logger.debug("Failed to render PDF bytes", exc_info=True)
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _render_svg_bytes(
    scene: QGraphicsScene,
    source_rect: QRectF,
    export_scale: float = 1.0,
) -> bytes | None:
    """Render *source_rect* of *scene* to SVG bytes.

    Mirrors ``DiagramExporter.export_svg`` but writes to memory, for the
    clipboard's vector flavor.
    """
    try:
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtSvg import QSvgGenerator
    except ImportError:
        return None

    out_w = source_rect.width() * export_scale
    out_h = source_rect.height() * export_scale
    if out_w <= 0 or out_h <= 0:
        return None

    try:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)

        generator = QSvgGenerator()
        generator.setOutputDevice(buf)
        generator.setSize(QSize(max(1, round(out_w)), max(1, round(out_h))))
        generator.setViewBox(QRectF(0, 0, out_w, out_h))
        generator.setTitle("Diagrammer Export")
        generator.setDescription("Exported from Diagrammer")

        painter = QPainter()
        painter.begin(generator)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(painter, QRectF(0, 0, out_w, out_h), source_rect)
        # QSvgGenerator only flushes the closing tags on end().
        painter.end()
        buf.close()

        data = bytes(buf.data())
        return data or None
    except Exception:
        logger.debug("Failed to render SVG bytes", exc_info=True)
        return None
