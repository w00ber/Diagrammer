"""Floating, context-sensitive keyboard-shortcut hint panel.

An optional cheat-sheet that lowers the learning curve for Diagrammer's
keyboard-driven workflow: it shows a curated set of the shortcuts relevant to
the current selection (nothing / component / wire / annotation) in a chosen
corner of the canvas.

The panel is a plain Qt widget parented to the diagram view's *viewport* (the
``QGraphicsView`` widget that paints the scene), so it is layered *over* the
canvas but is never part of the ``QGraphicsScene`` -- it can't appear in PNG/
SVG exports or "Copy as Image" clipboard copies, which are rendered from the
scene, not from the view's child widgets. It repositions itself when the
viewport resizes and never takes focus, so it never interferes with typing or
single-key shortcuts.

The widget is content-agnostic: the main window feeds it ``(keys, label)`` rows
for the current context via :meth:`ShortcutOverlay.set_rows`. Key strings are
resolved live from the shortcut registry (``diagrammer.shortcuts``) by the
caller, so a rebinding in Settings is reflected here automatically.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

# Corner keyword -> (anchor to the right?, anchor to the bottom?)
_CORNERS = {
    "top-left": (False, False),
    "top-right": (True, False),
    "bottom-left": (False, True),
    "bottom-right": (True, True),
}

_MARGIN = 12  # px inset from the viewport edge


class ShortcutOverlay(QWidget):
    """A small translucent panel of context-relevant shortcut hints."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._corner = "top-right"
        self.setObjectName("shortcutOverlay")
        # Never steal focus (matches the canvas), so single-key shortcuts and
        # text fields keep working while it is visible.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(0)
        self._label = QLabel(self)
        self._label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        # Pin the text dark: the panel background is always light, so without
        # this the text follows the system palette and turns white (unreadable)
        # under macOS dark/night mode. Mirrors the "paper" convention used by
        # HelpWindow.
        self._label.setStyleSheet("color: #111111;")
        layout.addWidget(self._label)

        # objectName-scoped stylesheet so it doesn't bleed onto child labels of
        # other widgets; translucent so the diagram shows through faintly.
        self.setStyleSheet(
            "#shortcutOverlay {"
            "  background-color: rgba(250, 250, 250, 235);"
            "  border: 1px solid rgba(0, 0, 0, 40);"
            "  border-radius: 8px;"
            "}"
        )
        # Reposition whenever the viewport resizes.
        host.installEventFilter(self)
        self.hide()

    # ---- public API ---------------------------------------------------

    def set_corner(self, corner: str) -> None:
        """Set the anchor corner ('top-left'|'top-right'|'bottom-left'|'bottom-right')."""
        if corner in _CORNERS:
            self._corner = corner
            self._reposition()

    def set_rows(self, title: str, rows) -> None:
        """Populate with a context title and ``[(keys, label), ...]`` rows.

        Hides the panel when there are no rows.
        """
        if not rows:
            self.hide()
            return
        body = "".join(
            "<tr>"
            f"<td style='padding:1px 10px 1px 0; white-space:nowrap;'>"
            f"<span style='font-family:monospace; font-weight:bold;'>{keys}</span></td>"
            f"<td style='padding:1px 0;'>{label}</td>"
            "</tr>"
            for keys, label in rows
        )
        html = (
            f"<div style='font-weight:bold; padding-bottom:4px;'>{title}</div>"
            f"<table style='border-collapse:collapse;'>{body}</table>"
        )
        self._label.setText(html)
        self.adjustSize()
        self._clamp_to_parent()
        self._reposition()

    def _clamp_to_parent(self) -> None:
        """Keep the panel within the viewport so a long list can't spill
        off-screen; content taller than the viewport is clipped at the bottom."""
        parent = self.parentWidget()
        if parent is None:
            return
        max_h = max(60, parent.height() - 2 * _MARGIN)
        max_w = max(60, parent.width() - 2 * _MARGIN)
        if self.height() > max_h or self.width() > max_w:
            self.resize(min(self.width(), max_w), min(self.height(), max_h))

    # ---- internals ----------------------------------------------------

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        right, bottom = _CORNERS.get(self._corner, (True, False))
        pw, ph = parent.width(), parent.height()
        w, h = self.width(), self.height()
        x = pw - w - _MARGIN if right else _MARGIN
        y = ph - h - _MARGIN if bottom else _MARGIN
        self.move(max(_MARGIN, x), max(_MARGIN, y))

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self._reposition()
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._reposition()
        self.raise_()
