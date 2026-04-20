"""Application factory for Diagrammer."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from diagrammer.main_window import MainWindow

# Background color used for the canvas and the library panel regardless
# of the chrome theme. Diagrammer's grid, component SVGs, connection
# colors, and annotation text defaults are all authored for a white
# background, so even in dark mode we pin these surfaces to light.
CANVAS_BACKGROUND = QColor(255, 255, 255)

# Valid theme values. "system" uses the platform-native style (honors
# macOS/Windows dark mode for the chrome); "light" and "dark" force a
# Fusion palette in the corresponding mode.
THEMES = ("system", "light", "dark")


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
    """Return an explicit light QPalette (Fusion-style)."""
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

    for role, color in (
        (QPalette.ColorRole.WindowText, mid_gray),
        (QPalette.ColorRole.Text, mid_gray),
        (QPalette.ColorRole.ButtonText, mid_gray),
        (QPalette.ColorRole.Base, near_white),
        (QPalette.ColorRole.Highlight, mid_gray),
        (QPalette.ColorRole.HighlightedText, white),
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, color)
    return palette


def _build_dark_palette() -> QPalette:
    """Return an explicit dark QPalette (Fusion-style).

    Loosely based on the commonly-used Qt Fusion dark recipe.
    """
    palette = QPalette()
    window_bg = QColor(53, 53, 53)
    base_bg = QColor(42, 42, 42)
    alt_base = QColor(66, 66, 66)
    button_bg = QColor(53, 53, 53)
    text = QColor(220, 220, 220)
    disabled_text = QColor(127, 127, 127)
    bright_text = QColor(255, 80, 80)
    highlight = QColor(42, 130, 218)
    white = QColor(255, 255, 255)
    black = QColor(0, 0, 0)

    palette.setColor(QPalette.ColorRole.Window, window_bg)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base_bg)
    palette.setColor(QPalette.ColorRole.AlternateBase, alt_base)
    palette.setColor(QPalette.ColorRole.ToolTipBase, window_bg)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, disabled_text)
    palette.setColor(QPalette.ColorRole.Button, button_bg)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, bright_text)
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, black)
    palette.setColor(QPalette.ColorRole.Link, QColor(100, 170, 255))

    for role, color in (
        (QPalette.ColorRole.WindowText, disabled_text),
        (QPalette.ColorRole.Text, disabled_text),
        (QPalette.ColorRole.ButtonText, disabled_text),
        (QPalette.ColorRole.Base, window_bg),
        (QPalette.ColorRole.Highlight, QColor(80, 80, 80)),
        (QPalette.ColorRole.HighlightedText, white),
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, color)
    return palette


def hint_text_color() -> str:
    """Return a hex color string for muted hint/caveat text.

    Returns a medium-gray that's legible against both light and dark
    chrome backgrounds, picked by inspecting the current application
    palette. Callers use this in inline stylesheets instead of hard-
    coding ``color: #555``, which disappears on a dark Window color.
    """
    from PySide6.QtGui import QPalette as _QPalette
    app = QApplication.instance()
    if app is None:
        return "#555555"
    window = app.palette().color(_QPalette.ColorRole.Window)
    # Window.lightness() is 0..255; <128 = dark-mode chrome.
    return "#a8a8a8" if window.lightness() < 128 else "#555555"


# Keep a handle on the style we saw at startup so "system" mode can
# restore the native look after switching away from Fusion.
_native_style_name: str | None = None


def apply_theme(app: QApplication, mode: str) -> None:
    """Apply a theme to the running QApplication.

    ``mode`` is one of :data:`THEMES`. Safe to call at startup and at
    runtime (e.g. from a menu toggle) — calling it again reapplies the
    requested style and palette so existing widgets repaint.
    """
    if mode not in THEMES:
        mode = "system"

    global _native_style_name
    if _native_style_name is None:
        current = app.style()
        if current is not None:
            _native_style_name = current.objectName()

    if mode == "system":
        # Hand control back to the platform style. We deliberately
        # re-instantiate it so any palette we previously set on the
        # app gets replaced by the style's standardPalette().
        style_name = _native_style_name or "Fusion"
        style = QStyleFactory.create(style_name)
        if style is not None:
            app.setStyle(style)
            app.setPalette(style.standardPalette())
        return

    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    if mode == "dark":
        app.setPalette(_build_dark_palette())
    else:
        app.setPalette(_build_light_palette())


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

    # Apply the user's saved theme preference. Default is "system".
    # Chrome (menus, dialogs, dock frames) follows this choice; the
    # canvas and library panel remain on CANVAS_BACKGROUND regardless.
    from diagrammer.panels.settings_dialog import app_settings
    apply_theme(app, app_settings.theme)

    app.setWindowIcon(_load_app_icon())
    _register_bundled_fonts()
    window = MainWindow()
    return app, window
