"""Shared pytest fixtures for Diagrammer tests."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="session", autouse=True)
def _ensure_qapp():
    """Create a QApplication instance for the test session.

    Many Diagrammer classes (ComponentItem, DiagramScene, etc.) require a
    running QApplication. This fixture creates one once and reuses it.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    yield app


@pytest.fixture
def scene():
    """Create a fresh DiagramScene for each test."""
    from diagrammer.canvas.scene import DiagramScene
    from diagrammer.models.library import ComponentLibrary

    library = ComponentLibrary()
    return DiagramScene(library=library)


@pytest.fixture
def library():
    """Create a ComponentLibrary loaded with built-in components."""
    from pathlib import Path

    from diagrammer.models.library import ComponentLibrary

    components_dir = Path(__file__).parent.parent / "src" / "diagrammer" / "components"
    lib = ComponentLibrary()
    if components_dir.is_dir():
        lib.scan(components_dir)
    return lib
