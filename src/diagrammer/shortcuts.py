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


def _to_key_sequence(val: QKeySequence.StandardKey | QKeySequence | str | int | None) -> QKeySequence:
    """Coerce a binding value (StandardKey/QKeySequence/str/int) to QKeySequence."""
    if val is None:
        return QKeySequence()
    if isinstance(val, QKeySequence.StandardKey):
        return QKeySequence(val)
    if isinstance(val, QKeySequence):
        return val
    if isinstance(val, str):
        return QKeySequence(val)
    if isinstance(val, int):
        return QKeySequence(val)
    return QKeySequence()


class Shortcut:
    """A keyboard shortcut with platform-aware default and optional user override."""

    # Type for binding values accepted by the constructor
    _BindingValue = QKeySequence.StandardKey | QKeySequence | str | int | None

    def __init__(
        self,
        action_id: str,
        mac: _BindingValue,
        win: _BindingValue = None,
        linux: _BindingValue = None,
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
        # User override (portable string form). None = use default.
        self._user_override: str | None = None

    @property
    def default_key_sequence(self) -> QKeySequence:
        """Return the platform default QKeySequence (ignoring user override)."""
        return _to_key_sequence(self._bindings[PLATFORM])

    @property
    def key_sequence(self) -> QKeySequence:
        """Return the active QKeySequence (override if set, else default)."""
        if self._user_override is not None:
            return QKeySequence(self._user_override)
        return self.default_key_sequence

    @property
    def display_text(self) -> str:
        """Human-readable shortcut string for the current platform."""
        seq = self.key_sequence
        text = seq.toString(QKeySequence.SequenceFormat.NativeText)
        return text if text else ""

    @property
    def default_display_text(self) -> str:
        text = self.default_key_sequence.toString(QKeySequence.SequenceFormat.NativeText)
        return text if text else ""

    @property
    def has_binding(self) -> bool:
        return not self.key_sequence.isEmpty()

    @property
    def is_overridden(self) -> bool:
        return self._user_override is not None

    def set_override(self, sequence) -> None:
        """Set a user override (QKeySequence or str). Pass None/empty to clear."""
        if sequence is None:
            self._user_override = None
            return
        if isinstance(sequence, QKeySequence):
            s = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        else:
            s = str(sequence)
        if not s:
            self._user_override = None
            return
        # If override matches the default, store as None so it stays in sync.
        default_portable = self.default_key_sequence.toString(
            QKeySequence.SequenceFormat.PortableText)
        if s == default_portable:
            self._user_override = None
        else:
            self._user_override = s

    def reset(self) -> None:
        self._user_override = None


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
_reg("file.refresh_libraries", _ks("F5"),                  description="Refresh Libraries", category="File")

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
_reg("edit.fine_cw",      _ks("Shift+R"),                  description="Fine Rotate CW 15°", category="Edit")
_reg("edit.fine_ccw",     _ks("R"),                        description="Fine Rotate CCW 15°", category="Edit")
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
_reg("edit.join_wires", _ks("Ctrl+J"),                      description="Join Wires",        category="Edit")

_reg("edit.copy_as_image", _ks("Ctrl+Shift+C"),              description="Copy Selection as Image", category="Edit")
_reg("edit.settings",  _ks("Ctrl+,"),                      description="Settings",          category="Edit")

# ----- View -----
_reg("view.zoom_in",   QKeySequence.StandardKey.ZoomIn,    description="Zoom In",           category="View")
_reg("view.zoom_out",  QKeySequence.StandardKey.ZoomOut,   description="Zoom Out",          category="View")
_reg("view.fit_all",   _ks("A"),                           description="Zoom All / Fit",    category="View")
_reg("view.fit_all2",  _ks("Ctrl+0"),                      description="Zoom All / Fit",    category="View")
_reg("view.zoom_window", _ks("Z"),                         description="Zoom Window",       category="View")
_reg("view.close_tab",  QKeySequence.StandardKey.Close,    description="Close Tab",         category="View")
_reg("view.toggle_grid", _ks("G"),                          description="Show / Hide Grid",  category="View")

# ----- Routing -----
_reg("routing.trace",  _ks("W"),                           description="Trace Routing Mode", category="Routing")

# ----- Draw -----
_reg("draw.text",      _ks("Ctrl+Shift+T"),                 description="Add Text",          category="Draw")

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


# =====================================================================
# User overrides: persistence & conflict detection
# =====================================================================

def load_user_overrides(overrides: dict) -> None:
    """Apply a {action_id: portable_key_string} dict of user overrides."""
    if not overrides:
        return
    for action_id, seq_str in overrides.items():
        s = SHORTCUTS.get(action_id)
        if s is not None:
            s.set_override(seq_str)


def dump_user_overrides() -> dict[str, str]:
    """Return only non-default user overrides as a serializable dict."""
    out: dict[str, str] = {}
    for action_id, s in SHORTCUTS.items():
        if s._user_override is not None:
            out[action_id] = s._user_override
    return out


def reset_all_overrides() -> None:
    for s in SHORTCUTS.values():
        s.reset()


def find_conflicts(
    proposed: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Return key_string → [action_id, ...] for any sequence used by 2+ actions.

    If ``proposed`` is given, it overrides the registry's current state for
    conflict checking (used by the Settings tab while the user is editing).
    Empty strings are skipped.
    """
    by_key: dict[str, list[str]] = {}
    for action_id, s in SHORTCUTS.items():
        if proposed is not None and action_id in proposed:
            seq_str = proposed[action_id]
        else:
            seq = s.key_sequence
            seq_str = seq.toString(QKeySequence.SequenceFormat.PortableText)
        if not seq_str:
            continue
        by_key.setdefault(seq_str, []).append(action_id)
    return {k: ids for k, ids in by_key.items() if len(ids) > 1}
