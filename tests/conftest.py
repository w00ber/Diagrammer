"""Shared pytest fixtures for Diagrammer tests."""

from __future__ import annotations

import sys

import pytest

# Import PyObjC before anything pulls in Qt.
#
# test_clipboard_svg_paste seeds a real NSPasteboard and reaches AppKit through
# importorskip, i.e. part-way through the run. Importing AppKit's large
# _metadata module after Qt has initialised aborts the interpreter on this
# machine, and *which* test trips it depends on module import order at
# collection — so adding a test module that imports Qt earlier can break a
# clipboard test that has nothing to do with it. That is how this was found
# twice.
#
# conftest is imported before any test module, so doing it here fixes the order
# once instead of constraining what every future test file may import.
try:  # pragma: no cover - platform-dependent
    import AppKit  # noqa: F401
except Exception:  # pragma: no cover - not macOS, or PyObjC absent
    pass


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


@pytest.fixture(autouse=True)
def isolate_component_overlay(tmp_path, monkeypatch):
    """Keep tests out of the real ``~/.diagrammer/components/`` overlay.

    The same trap as ``isolate_app_settings``, one layer down. ``MainWindow``
    now scans the user's copy-on-write overlay *first*, because scan order is
    what makes a fork shadow the shipped component. Seven test modules build a
    MainWindow, so without this they would load whatever the developer happens
    to have forked — and a test asserting on a built-in component would measure
    that person's edit instead.

    Patched on ``diagrammer.models.overrides``, which MainWindow calls through
    by module reference for exactly this reason. Patching a name bound inside
    ``main_window`` would mean importing ``main_window`` in an autouse fixture,
    which pulls Qt and AppKit in for every test in the run — and that reordering
    segfaults the ``importorskip("AppKit")`` in test_clipboard_svg_paste.
    ``overrides`` itself imports no Qt, so this is free.
    """
    from diagrammer.models import overrides

    overlay = tmp_path / "overlay-components"
    monkeypatch.setattr(overrides, "default_overlay_root", lambda: overlay)
    yield overlay


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
