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


@pytest.fixture(autouse=True)
def isolate_app_settings(tmp_path, monkeypatch):
    """Run every test against factory settings, not the developer's own.

    ``app_settings`` is a module-level singleton whose ``__init__`` ends in
    ``load()``, so merely importing the app reads
    ``~/.diagrammer/settings.json``. Any test that asserts on default
    behaviour then measures whoever happens to be running it. That is not
    hypothetical: with "Show junction dots" switched off in a real install,
    six junction-dot tests in ``test_crossovers``, ``test_tee_junction`` and
    ``test_compound_export`` fail on that machine and pass everywhere else —
    which is what the "fails on a clean tree" note in CLAUDE.md was really
    describing, since a clean checkout still reads the same home directory.

    Redirecting ``_SETTINGS_FILE`` also closes the write direction: anything
    reaching ``app_settings.save()`` would otherwise overwrite the real file
    with whatever a test had just set.

    The redirect target is created empty, because ``reset_all`` derives
    ``_first_launch`` from whether the file exists and ``MainWindow`` applies
    first-launch library defaults when it is True. Tests should see an
    ordinary installed app, not a fresh one.
    """
    from diagrammer.panels import settings_dialog as sd

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}")
    monkeypatch.setattr(sd, "_SETTINGS_FILE", settings_file)
    sd.app_settings.reset_all()
    try:
        yield sd.app_settings
    finally:
        # The singleton outlives the test, so leave it at defaults rather
        # than at whatever this test set.
        sd.app_settings.reset_all()


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
