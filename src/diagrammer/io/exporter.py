"""DiagramExporter -- renders the current QGraphicsScene to SVG, PNG, or PDF files."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsScene


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
