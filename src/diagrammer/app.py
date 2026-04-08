"""Application factory for Diagrammer."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from diagrammer.main_window import MainWindow


def _load_app_icon() -> QIcon:
    """Load the application icon from the iconset directory."""
    icon = QIcon()
    icons_dir = Path(__file__).resolve().parent / "icons"
    if icons_dir.is_dir():
        for png in sorted(icons_dir.glob("icon_*.png")):
            icon.addFile(str(png))
    return icon


def _register_bundled_fonts() -> None:
    """Register bundled CMU (Computer Modern Unicode) fonts with Qt.

    This makes CMU Serif and CMU Sans Serif available on all platforms
    without requiring the user to install them system-wide.
    """
    fonts_dir = Path(__file__).resolve().parent / "fonts"
    if not fonts_dir.is_dir():
        return
    for otf in fonts_dir.glob("*.otf"):
        QFontDatabase.addApplicationFont(str(otf))


def _build_light_palette() -> QPalette:
    """Return an explicit light QPalette.

    Qt on Windows will otherwise follow the system color scheme, which
    turns the canvas, library panel, and various dialogs dark and makes
    the diagram hard to read. Diagrammer's canvas, grid, and component
    artwork are all authored against a white background, so we pin the
    whole UI to a light palette regardless of the OS theme.
    """
    palette = QPalette()
    white = QColor(255, 255, 255)
    near_white = QColor(245, 245, 245)
    light_gray = QColor(240, 240, 240)
    mid_gray = QColor(200, 200, 200)
    dark_gray = QColor(120, 120, 120)
    black = QColor(0, 0, 0)
    highlight = QColor(0, 120, 215)

    palette.setColor(QPalette.ColorRole.Window, light_gray)
    palette.setColor(QPalette.ColorRole.WindowText, black)
    palette.setColor(QPalette.ColorRole.Base, white)
    palette.setColor(QPalette.ColorRole.AlternateBase, near_white)
    palette.setColor(QPalette.ColorRole.ToolTipBase, white)
    palette.setColor(QPalette.ColorRole.ToolTipText, black)
    palette.setColor(QPalette.ColorRole.Text, black)
    palette.setColor(QPalette.ColorRole.PlaceholderText, dark_gray)
    palette.setColor(QPalette.ColorRole.Button, light_gray)
    palette.setColor(QPalette.ColorRole.ButtonText, black)
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, white)
    palette.setColor(QPalette.ColorRole.Link, highlight)

    # Disabled state — keep things readable but clearly inactive.
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, mid_gray
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, mid_gray
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, mid_gray
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, near_white
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, mid_gray
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, white
    )
    return palette


def create_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Create and return the QApplication and MainWindow instances."""
    if argv is None:
        argv = sys.argv

    # On Windows, force software OpenGL to avoid black canvas on machines
    # with incompatible GPU drivers (common with integrated Intel/AMD GPUs).
    if sys.platform == "win32":
        from PySide6.QtCore import Qt
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)

    app = QApplication(argv)
    app.setApplicationName("Diagrammer")
    app.setOrganizationName("Diagrammer")

    # Pin the UI to a light theme. Diagrammer's canvas and component
    # artwork are designed for a white background, and on Windows the
    # native style otherwise follows the system dark/light setting,
    # which leaves the canvas and library panel illegible in dark mode.
    # Fusion is a self-contained Qt style that ignores the OS color
    # scheme, so combining it with an explicit light palette gives a
    # consistent look on every platform.
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    app.setPalette(_build_light_palette())

    app.setWindowIcon(_load_app_icon())
    _register_bundled_fonts()
    window = MainWindow()
    return app, window
