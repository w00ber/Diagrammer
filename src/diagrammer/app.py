"""Application factory for Diagrammer."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from diagrammer.main_window import MainWindow


def create_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Create and return the QApplication and MainWindow instances."""
    if argv is None:
        argv = sys.argv
    app = QApplication(argv)
    app.setApplicationName("Diagrammer")
    app.setOrganizationName("Diagrammer")
    window = MainWindow()
    return app, window
