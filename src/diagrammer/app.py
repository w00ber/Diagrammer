"""Application factory for Diagrammer."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
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


def create_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Create and return the QApplication and MainWindow instances."""
    if argv is None:
        argv = sys.argv
    app = QApplication(argv)
    app.setApplicationName("Diagrammer")
    app.setOrganizationName("Diagrammer")
    app.setWindowIcon(_load_app_icon())
    window = MainWindow()
    return app, window
