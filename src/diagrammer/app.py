"""Application factory for Diagrammer."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

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
    app.setWindowIcon(_load_app_icon())
    _register_bundled_fonts()
    window = MainWindow()
    return app, window
