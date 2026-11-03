"""open_with — launch a component's SVG file in an external application.

The library panel's context menus use this to offer "Open in Default App"
and an "Open With" submenu. The submenu lists, in order:

1. the applications the user has explicitly chosen before, persisted in
   ``app_settings.external_editors`` so they survive a restart;
2. any well-known SVG editor detected on this machine that isn't already
   in that list (a convenience on a fresh install — nothing is persisted
   until the user actually picks one);
3. "Choose Application\u2026", which opens a file dialog rooted at the
   platform's applications folder.

Component SVGs under ``src/diagrammer/components/`` are data files, so
editing one in Inkscape/Illustrator and re-scanning the library is a
legitimate workflow — see the component spec for the layer contract an
external editor must preserve.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import QMenu

logger = logging.getLogger(__name__)

# Well-known SVG/vector editors, probed by bundle/binary name. Order is the
# order they appear in the submenu. These are only *suggestions*: an entry
# shows up solely when it exists on disk, and is persisted only once the
# user picks it.
_KNOWN_APPS_DARWIN = (
    "Inkscape",
    "Adobe Illustrator",
    "Affinity Designer 2",
    "Affinity Designer",
    "Boxy SVG",
    "VectorCraft",
    "Sketch",
    "Figma",
    "Visual Studio Code",
    "Sublime Text",
)
_KNOWN_APPS_WIN = (
    r"C:\Program Files\Inkscape\bin\inkscape.exe",
    r"C:\Program Files\Inkscape\inkscape.exe",
)
_KNOWN_APPS_LINUX = ("inkscape", "gimp", "code", "gedit")


def display_name(app: str) -> str:
    """Return a short menu label for *app* (a bundle path, exe path, or name).

    Splits on both separators rather than going through ``Path``, so a
    Windows-style path still reduces to its basename when inspected on
    another platform (settings files travel between machines).
    """
    name = re.split(r"[\\/]", app)[-1]
    for suffix in (".app", ".exe", ".AppImage"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name or app


def detect_apps() -> list[str]:
    """Return well-known editors present on this machine, as launchable paths/names."""
    found: list[str] = []
    if sys.platform == "darwin":
        roots = (Path("/Applications"), Path.home() / "Applications")
        for name in _KNOWN_APPS_DARWIN:
            for root in roots:
                bundle = root / f"{name}.app"
                if bundle.exists():
                    found.append(str(bundle))
                    break
    elif sys.platform == "win32":
        for exe in _KNOWN_APPS_WIN:
            if Path(exe).exists():
                found.append(exe)
    else:
        import shutil
        for name in _KNOWN_APPS_LINUX:
            resolved = shutil.which(name)
            if resolved:
                found.append(resolved)
    return found


def open_in_default_app(path: Path) -> bool:
    """Open *path* in whatever application the OS associates with it."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            import os
            os.startfile(str(path))  # noqa: S606 (documented Windows API)
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except (OSError, ValueError) as exc:
        logger.warning("Failed to open %s in the default app: %s", path, exc)
        return False


def open_with_app(path: Path, app: str) -> bool:
    """Open *path* in *app*.

    On macOS *app* is handed to ``open -a``, which accepts either a bundle
    path or a bare application name. Elsewhere it must be an executable
    path or a name resolvable on ``PATH``, and the file is passed as its
    first argument.
    """
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", app, str(path)])
        else:
            subprocess.Popen([app, str(path)])
        return True
    except (OSError, ValueError) as exc:
        logger.warning("Failed to open %s in '%s': %s", path, app, exc)
        return False


def remember_app(app: str) -> None:
    """Add *app* to the front of the persisted external-editor list."""
    from diagrammer.panels.settings_dialog import app_settings
    editors = [e for e in app_settings.external_editors if e != app]
    editors.insert(0, app)
    app_settings.external_editors = editors[:10]
    app_settings.save()


def choose_app(parent=None) -> str | None:
    """Prompt for an application and return its path, or None if cancelled."""
    from PySide6.QtWidgets import QFileDialog

    if sys.platform == "darwin":
        start, filt = "/Applications", "Applications (*.app);;All Files (*)"
    elif sys.platform == "win32":
        start, filt = "C:\\Program Files", "Programs (*.exe);;All Files (*)"
    else:
        start, filt = "/usr/bin", "All Files (*)"
    path, _ = QFileDialog.getOpenFileName(parent, "Choose Application", start, filt)
    return path or None


def add_open_with_actions(menu, path: Path, parent=None) -> None:
    """Append "Open in Default App" and an "Open With" submenu to *menu*.

    The actions are wired with ``triggered`` handlers, so callers that
    compare ``menu.exec()``'s return value against their own actions can
    ignore these entirely.
    """
    from diagrammer.panels.settings_dialog import app_settings

    def _open(app: str | None) -> None:
        if app is None:
            open_in_default_app(path)
            return
        if open_with_app(path, app):
            remember_app(app)

    def _choose() -> None:
        app = choose_app(parent)
        if app:
            _open(app)

    menu.addAction("Open in Default App").triggered.connect(lambda: _open(None))

    # Construct the submenu with *menu* as its parent rather than using
    # menu.addMenu("Open With"): the latter hands the new QMenu back to
    # Python without transferring ownership, so it is garbage-collected
    # as soon as this function returns and its entries die with it.
    sub = QMenu("Open With", menu)
    menu.addMenu(sub)
    saved = list(app_settings.external_editors)
    for app in saved:
        sub.addAction(display_name(app)).triggered.connect(
            lambda _checked=False, a=app: _open(a))
    detected = [a for a in detect_apps() if a not in saved]
    if detected:
        if saved:
            sub.addSeparator()
        for app in detected:
            sub.addAction(display_name(app)).triggered.connect(
                lambda _checked=False, a=app: _open(a))
    if saved or detected:
        sub.addSeparator()
    sub.addAction("Choose Application\u2026").triggered.connect(
        lambda _checked=False: _choose())
