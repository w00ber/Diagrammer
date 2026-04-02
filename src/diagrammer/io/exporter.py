"""DiagramExporter -- renders the current QGraphicsScene to SVG, PNG, or PDF files."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsScene


class DiagramExporter:
    """Static methods for exporting a DiagramScene to various file formats.

    All export methods compute the bounding rect of scene items via
    ``scene.itemsBoundingRect()`` and add an optional margin so that the
    exported image is neatly padded.

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

        generator = QSvgGenerator()
        generator.setFileName(path)
        generator.setSize(QSize(int(source_rect.width()), int(source_rect.height())))
        generator.setViewBox(QRectF(0, 0, source_rect.width(), source_rect.height()))
        generator.setTitle("Diagrammer Export")
        generator.setDescription("Exported from Diagrammer")

        painter = QPainter()
        painter.begin(generator)
        # Map the source region of the scene into the full generator viewport.
        target_rect = QRectF(0, 0, source_rect.width(), source_rect.height())
        scene.render(painter, target_rect, source_rect)
        painter.end()

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
        source_rect = _items_rect_with_margin(scene, margin)

        # Scale factor: scene coordinates are nominally at 96 dpi.
        scale = dpi / 96.0
        pixel_width = max(1, int(source_rect.width() * scale))
        pixel_height = max(1, int(source_rect.height() * scale))

        image = QImage(QSize(pixel_width, pixel_height), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDotsPerMeterX(int(dpi / 0.0254))
        image.setDotsPerMeterY(int(dpi / 0.0254))
        image.fill(QColor(Qt.GlobalColor.white))

        painter = QPainter()
        painter.begin(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        target_rect = QRectF(0, 0, pixel_width, pixel_height)
        scene.render(painter, target_rect, source_rect)
        painter.end()

        image.save(path)

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
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize

        source_rect = _items_rect_with_margin(scene, margin)

        # QPrinter lives in QtPrintSupport.
        from PySide6.QtPrintSupport import QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)

        # Set a custom page size matching the scene rect (in points; 1 pt = 1/72 in).
        page_size = QPageSize(source_rect.size(), QPageSize.Unit.Point)
        page_layout = QPageLayout(page_size, QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0))
        printer.setPageLayout(page_layout)

        painter = QPainter()
        painter.begin(printer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # The printer's paint device has its own resolution (typically 1200 dpi).
        # Map the scene source rect into the full printable area.
        printer_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
        target_rect = QRectF(printer_rect)
        scene.render(painter, target_rect, source_rect)
        painter.end()

    # ------------------------------------------------------------------
    # Clipboard (selection → system clipboard)
    # ------------------------------------------------------------------

    @staticmethod
    def copy_selection_to_clipboard(
        scene: QGraphicsScene,
        dpi: int = 300,
        margin: float = 20.0,
    ) -> bool:
        """Copy the selected items to the system clipboard as PNG + PDF.

        The receiving application picks the best available format:
        PDF for vector-aware apps (Illustrator, Keynote, PowerPoint on macOS),
        PNG as a universal fallback.

        Returns ``True`` if something was copied, ``False`` if the selection
        was empty.
        """
        from PySide6.QtCore import QMimeData

        source_rect = _selection_rect_with_margin(scene, margin)
        if source_rect.isNull() or source_rect.isEmpty():
            return False

        # --- Render PNG at high DPI ---
        scale = dpi / 96.0
        pixel_w = max(1, int(source_rect.width() * scale))
        pixel_h = max(1, int(source_rect.height() * scale))

        image = QImage(QSize(pixel_w, pixel_h), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDotsPerMeterX(int(dpi / 0.0254))
        image.setDotsPerMeterY(int(dpi / 0.0254))
        image.fill(QColor(Qt.GlobalColor.white))

        painter = QPainter()
        painter.begin(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scene.render(painter, QRectF(0, 0, pixel_w, pixel_h), source_rect)
        painter.end()

        # --- Render PDF into a byte buffer ---
        pdf_data = _render_pdf_bytes(scene, source_rect)

        # --- Build MIME data with both formats ---
        mime = QMimeData()
        mime.setImageData(image)
        if pdf_data:
            mime.setData("application/pdf", pdf_data)

        QApplication.clipboard().setMimeData(mime)
        return True

    @staticmethod
    def copy_all_to_clipboard(
        scene: QGraphicsScene,
        dpi: int = 300,
        margin: float = 20.0,
    ) -> bool:
        """Copy the entire scene to the system clipboard (PNG + PDF).

        Convenience wrapper that uses the full items bounding rect instead
        of the selection rect.  Returns ``True`` on success.
        """
        from PySide6.QtCore import QMimeData

        source_rect = _items_rect_with_margin(scene, margin)
        if source_rect.isNull() or source_rect.isEmpty():
            return False

        scale = dpi / 96.0
        pixel_w = max(1, int(source_rect.width() * scale))
        pixel_h = max(1, int(source_rect.height() * scale))

        image = QImage(QSize(pixel_w, pixel_h), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDotsPerMeterX(int(dpi / 0.0254))
        image.setDotsPerMeterY(int(dpi / 0.0254))
        image.fill(QColor(Qt.GlobalColor.white))

        painter = QPainter()
        painter.begin(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scene.render(painter, QRectF(0, 0, pixel_w, pixel_h), source_rect)
        painter.end()

        pdf_data = _render_pdf_bytes(scene, source_rect)

        mime = QMimeData()
        mime.setImageData(image)
        if pdf_data:
            mime.setData("application/pdf", pdf_data)

        QApplication.clipboard().setMimeData(mime)
        return True


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


def _render_pdf_bytes(scene: QGraphicsScene, source_rect: QRectF) -> QByteArray | None:
    """Render *source_rect* of *scene* to an in-memory PDF and return raw bytes.

    Returns ``None`` if PDF generation fails (e.g. QtPrintSupport unavailable).
    """
    try:
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrinter
    except ImportError:
        return None

    # QPrinter requires a real file path; render to a temp file and read back.
    import tempfile, os

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)

    try:
        printer.setOutputFileName(tmp_path)
        page_size = QPageSize(source_rect.size(), QPageSize.Unit.Point)
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
            data = QByteArray(f.read())
        return data
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
