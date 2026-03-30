"""Centralized, platform-aware keyboard shortcut registry.

Every shortcut in Diagrammer is defined here with per-platform bindings.
Menu actions and key handlers reference shortcuts by ID, ensuring:
- No duplicate/conflicting bindings
- Correct platform conventions (Cmd on macOS, Ctrl on Win/Linux)
- Help documentation generated dynamically from the registry
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

# Detect platform once at import time
PLATFORM: str = (
    "mac" if sys.platform == "darwin"
    else "win" if sys.platform == "win32"
    else "linux"
)


class Shortcut:
    """A keyboard shortcut with platform-aware key binding."""

    def __init__(
        self,
        action_id: str,
        mac,
        win=None,
        linux=None,
        *,
        description: str = "",
        category: str = "",
    ) -> None:
        self.action_id = action_id
        self.description = description
        self.category = category
        # Store bindings; fallback: linux→win→mac
        self._bindings = {
            "mac": mac,
            "win": win if win is not None else mac,
            "linux": linux if linux is not None else (win if win is not None else mac),
        }

    @property
    def key_sequence(self) -> QKeySequence:
        """Return the QKeySequence for the current platform."""
        val = self._bindings[PLATFORM]
        if isinstance(val, QKeySequence.StandardKey):
            return QKeySequence(val)
        if isinstance(val, QKeySequence):
            return val
        if isinstance(val, str):
            return QKeySequence(val)
        if isinstance(val, int):
            return QKeySequence(val)
        return QKeySequence()

    @property
    def display_text(self) -> str:
        """Human-readable shortcut string for the current platform."""
        seq = self.key_sequence
        text = seq.toString(QKeySequence.SequenceFormat.NativeText)
        return text if text else ""

    @property
    def has_binding(self) -> bool:
        return not self.key_sequence.isEmpty()


def _ks(key_str: str) -> QKeySequence:
    """Shorthand for QKeySequence from string."""
    return QKeySequence(key_str)


# =====================================================================
# Shortcut Registry
# =====================================================================

SHORTCUTS: dict[str, Shortcut] = {}


def _reg(action_id, mac, win=None, linux=None, description="", category=""):
    """Register a shortcut."""
    s = Shortcut(action_id, mac, win, linux,
                 description=description, category=category)
    SHORTCUTS[action_id] = s
    return s


# ----- File -----
_reg("file.new",       QKeySequence.StandardKey.New,      description="New diagram",       category="File")
_reg("file.open",      QKeySequence.StandardKey.Open,     description="Open...",           category="File")
_reg("file.save",      QKeySequence.StandardKey.Save,     description="Save",              category="File")
_reg("file.save_as",   QKeySequence.StandardKey.SaveAs,   description="Save As...",        category="File")
_reg("file.quit",      QKeySequence.StandardKey.Quit,     description="Quit",              category="File")

# ----- Edit -----
_reg("edit.undo",      QKeySequence.StandardKey.Undo,     description="Undo",              category="Edit")
_reg("edit.redo",      QKeySequence.StandardKey.Redo,     description="Redo",              category="Edit")
_reg("edit.cut",       QKeySequence.StandardKey.Cut,      description="Cut",               category="Edit")
_reg("edit.copy",      QKeySequence.StandardKey.Copy,     description="Copy",              category="Edit")
_reg("edit.paste",     QKeySequence.StandardKey.Paste,    description="Paste",             category="Edit")
_reg("edit.select_all", QKeySequence.StandardKey.SelectAll, description="Select All",      category="Edit")
_reg("edit.delete",    QKeySequence.StandardKey.Delete,   description="Delete",            category="Edit")

_reg("edit.rotate_ccw",   _ks("Space"),                    description="Rotate CCW 90°",   category="Edit")
_reg("edit.rotate_cw",    _ks("Shift+Space"),              description="Rotate CW 90°",    category="Edit")
_reg("edit.fine_cw",      _ks("R"),                        description="Fine Rotate CW 15°", category="Edit")
_reg("edit.fine_ccw",     _ks("Shift+R"),                  description="Fine Rotate CCW 15°", category="Edit")
_reg("edit.flip_h",       _ks("F"),                        description="Flip Horizontal",   category="Edit")
_reg("edit.flip_v",       _ks("Shift+F"),                  description="Flip Vertical",     category="Edit")

_reg("edit.align_h",   _ks("Ctrl+Shift+H"),               description="Align Horizontally", category="Edit")
_reg("edit.align_v",
     mac=_ks("Ctrl+Shift+V"),
     win=_ks("Ctrl+Shift+I"),
     linux=_ks("Ctrl+Shift+I"),
     description="Align Vertically", category="Edit")

_reg("edit.hide_layer",   _ks("H"),                        description="Hide Active Layer",   category="Edit")
_reg("edit.show_layer",   _ks("Shift+H"),                  description="Show Active Layer",   category="Edit")
_reg("edit.lock_layer",   _ks("L"),                        description="Lock Active Layer",   category="Edit")
_reg("edit.unlock_layer", _ks("Shift+L"),                  description="Unlock Active Layer", category="Edit")

_reg("edit.bring_fwd",    _ks("Ctrl+]"),                   description="Bring Forward",     category="Edit")
_reg("edit.send_bwd",     _ks("Ctrl+["),                   description="Send Backward",     category="Edit")
_reg("edit.bring_front",  _ks("Ctrl+Shift+]"),             description="Bring to Front",    category="Edit")
_reg("edit.send_back",    _ks("Ctrl+Shift+["),             description="Send to Back",      category="Edit")

_reg("edit.group",     _ks("Ctrl+G"),                      description="Group",             category="Edit")
_reg("edit.ungroup",   _ks("Ctrl+Shift+G"),                description="Ungroup",           category="Edit")

_reg("edit.settings",  _ks("Ctrl+,"),                      description="Settings",          category="Edit")

# ----- View -----
_reg("view.zoom_in",   QKeySequence.StandardKey.ZoomIn,    description="Zoom In",           category="View")
_reg("view.zoom_out",  QKeySequence.StandardKey.ZoomOut,   description="Zoom Out",          category="View")
_reg("view.fit_all",   _ks("A"),                           description="Zoom All / Fit",    category="View")
_reg("view.fit_all2",  _ks("Ctrl+0"),                      description="Zoom All / Fit",    category="View")
_reg("view.zoom_window", _ks("Z"),                         description="Zoom Window",       category="View")

# ----- Routing -----
_reg("routing.trace",  _ks("W"),                           description="Trace Routing Mode", category="Routing")

# ----- Draw -----
_reg("draw.text",      _ks("T"),                           description="Add Text",          category="Draw")

# ----- Help -----
_reg("help.help",      QKeySequence.StandardKey.HelpContents, description="Help",           category="Help")

# ----- Canvas (bare keys handled in view.py, not menu actions) -----
_reg("canvas.select_mode", _ks("V"),                       description="Select Mode",       category="Canvas")
_reg("canvas.escape",      _ks("Escape"),                  description="Cancel / Select Mode", category="Canvas")


# =====================================================================
# Utility
# =====================================================================

def get(action_id: str) -> QKeySequence:
    """Look up a shortcut's QKeySequence by action ID."""
    return SHORTCUTS[action_id].key_sequence


def get_shortcut(action_id: str) -> Shortcut:
    """Look up a Shortcut object by action ID."""
    return SHORTCUTS[action_id]


def all_shortcuts() -> list[Shortcut]:
    """Return all registered shortcuts, ordered by category."""
    return sorted(SHORTCUTS.values(), key=lambda s: (s.category, s.action_id))


def shortcuts_by_category() -> dict[str, list[Shortcut]]:
    """Return shortcuts grouped by category."""
    result: dict[str, list[Shortcut]] = {}
    for s in all_shortcuts():
        result.setdefault(s.category, []).append(s)
    return result
