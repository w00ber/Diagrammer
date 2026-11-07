"""Move, remove and revert a library component.

These are file-system operations on the component library, and they are
deliberately **not** QUndoCommands, even though CLAUDE.md requires every canvas
mutation to be one. Three reasons:

1. *Scope.* The undo stack belongs to a document; a library edit is global and
   outlives it. Cmd-Z after closing and reopening a diagram would either do
   nothing or revert a library change made an hour ago in a different drawing.
2. *``undo()`` must be infallible* for the stack to stay coherent. Un-deleting
   from the Trash, or restoring a file whose folder the user has since renamed
   in the Finder, can fail — and a failed undo corrupts the stack's model of
   the world.
3. *Mental model.* On a canvas, undo means "put that symbol back", not "revert
   my library".

Reversibility comes instead from affordances that suit file operations:
tombstones are reversible by construction, deletes go to the Trash so the OS
owns the undo, overwrites keep a timestamped backup, and a fork leaves the
shipped file untouched. The adjacent case is already safe — a saved component
does not alter open canvases, because placed items keep their ComponentDef
references by design.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from diagrammer.models.component_def import ComponentDef
from diagrammer.models.overrides import rel_posix

logger = logging.getLogger(__name__)

NEW_CATEGORY = "← New category…"


class _MoveDialog(QDialog):
    """Pick the category to move a component into."""

    def __init__(self, parent, comp_def: ComponentDef, categories: list[str],
                 *, forking: bool):
        super().__init__(parent)
        self.setWindowTitle(f"Move “{comp_def.name}”")
        v = QVBoxLayout(self)

        if forking:
            note = QLabel(
                f"<b>{comp_def.name}</b> ships with Diagrammer, so it cannot be "
                "moved in place. Your copy will appear in the category you "
                "choose, and the shipped one will be hidden — both "
                "reversible from Library › Library Overrides.")
        else:
            note = QLabel(f"Move <b>{comp_def.name}</b> to a different "
                          "category. The file moves with it.")
        note.setWordWrap(True)
        v.addWidget(note)

        form = QFormLayout()
        self._combo = QComboBox()
        self._combo.addItems(categories)
        self._combo.addItem(NEW_CATEGORY)
        if comp_def.category in categories:
            self._combo.setCurrentText(comp_def.category)
        self._combo.currentTextChanged.connect(self._sync)
        form.addRow("Category:", self._combo)

        self._new = QLineEdit()
        self._new.setPlaceholderText("e.g. circuits/µwave/filters")
        self._new.setVisible(False)
        form.addRow("New category:", self._new)
        self._new_label = form.labelForField(self._new)
        self._new_label.setVisible(False)
        v.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _sync(self, text: str) -> None:
        is_new = text == NEW_CATEGORY
        self._new.setVisible(is_new)
        self._new_label.setVisible(is_new)
        if is_new:
            self._new.setFocus()

    def category(self) -> str:
        if self._combo.currentText() == NEW_CATEGORY:
            return self._new.text().strip().strip("/")
        return self._combo.currentText()


class ComponentActions:
    """The library-management actions, bound to a MainWindow.

    A separate object rather than methods on the panel because the context menu
    is built by a shared factory that is handed only a ComponentDef — and
    because every one of these needs the override index, the builtin root and
    the overlay root, which the window owns.
    """

    def __init__(self, window):
        self._w = window

    # -- helpers ----------------------------------------------------------

    @property
    def index(self):
        return self._w._overrides

    @property
    def builtin_root(self) -> Path:
        return self._w._builtin_components_path

    @property
    def overlay_root(self) -> Path:
        return self._w._overlay_root

    def _save_index(self) -> bool:
        from diagrammer import __version__

        if not self.index.save(__version__):
            QMessageBox.warning(
                self._w, "Could Not Record the Change",
                "The change was not recorded in "
                f"{self.index.index_path}.\n\nIf that file was written by a "
                "newer version of Diagrammer it is kept read-only so nothing "
                "it recorded is lost.")
            return False
        return True

    def _refresh(self, *, full: bool = True) -> None:
        if full:
            self._w._rebuild_library()
        self._w._library_panel._refresh_views()

    def _builtin_rel(self, comp_def: ComponentDef) -> str | None:
        return rel_posix(comp_def.svg_path, self.builtin_root)

    def tombstone_key(self, comp_def: ComponentDef) -> tuple[str, str] | None:
        """``(scope, key)`` for hiding this component, or None if we cannot."""
        if comp_def.is_builtin:
            rel = self._builtin_rel(comp_def)
            return ("builtin", rel) if rel else None
        return ("custom", str(comp_def.svg_path))

    # -- inspect ----------------------------------------------------------

    def inspect(self, comp_def: ComponentDef) -> None:
        if comp_def.is_embedded or not comp_def.svg_path:
            QMessageBox.information(
                self._w, "Nothing to Inspect",
                "This component is embedded in the document rather than loaded "
                "from a library file, so there is no file to check.")
            return
        self._w.open_component_inspector(Path(comp_def.svg_path))

    def validate_category(self, category: str, defs) -> None:
        """Open the batch report scoped to one category's folder.

        Scoped by *folder* rather than by the category's component list,
        because a category can be fed by more than one root once the user has
        an overlay — and the folder is what the report knows how to sweep.
        """
        roots: list[tuple[str, Path]] = []
        seen: set[Path] = set()
        for d in defs:
            if not d.svg_path:
                continue
            folder = Path(d.svg_path).parent
            if folder in seen:
                continue
            seen.add(folder)
            label = category if len(seen) == 1 else f"{category} ({folder.name})"
            roots.append((label, folder))
        if not roots:
            return
        self._w._show_validation_report(roots)

    # -- move -------------------------------------------------------------

    def move_to_category(self, comp_def: ComponentDef, parent=None) -> None:
        """Move a component, forking a built-in into the new category.

        For a built-in this is a fork *plus* a tombstone, written in **one**
        index save. That composition is precisely why forks and tombstones live
        in the same file: a half-applied move that recorded the fork but lost
        the tombstone would show the component in both categories at once.
        """
        if comp_def.is_embedded or not comp_def.svg_path:
            return
        categories = sorted(self._w._library.categories)
        dlg = _MoveDialog(parent or self._w, comp_def, categories,
                          forking=comp_def.is_builtin)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        target_cat = dlg.category()
        if not target_cat or target_cat == comp_def.category:
            return

        source = Path(comp_def.svg_path)
        if comp_def.is_builtin:
            self._move_builtin(comp_def, source, target_cat)
        else:
            self._move_user_file(comp_def, source, target_cat)

    def _move_builtin(self, comp_def: ComponentDef, source: Path,
                      target_cat: str) -> None:
        rel = self._builtin_rel(comp_def)
        if rel is None:
            return
        target = self.overlay_root / target_cat / source.name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        except OSError as exc:
            QMessageBox.critical(self._w, "Could Not Move", str(exc))
            return
        new_rel = rel_posix(target, self.overlay_root) or \
            f"{target_cat}/{source.name}"
        # Both halves, then one save.
        self.index.record_fork(new_rel, origin=source,
                               note=f"moved from {comp_def.category}")
        self.index.hide("builtin", rel,
                        reason=f"moved to {target_cat}")
        if not self._save_index():
            return
        self._refresh()
        self._w.statusBar().showMessage(
            f"Your copy of “{comp_def.name}” is now in "
            f"{target_cat}; the shipped one is hidden", 8000)

    def _move_user_file(self, comp_def: ComponentDef, source: Path,
                        target_cat: str) -> None:
        root = comp_def.source_root or source.parent
        target = root / target_cat / source.name
        if target.exists():
            QMessageBox.warning(
                self._w, "Already There",
                f"{target_cat} already has a component called "
                f"{source.stem}.")
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        except OSError as exc:
            QMessageBox.critical(self._w, "Could Not Move", str(exc))
            return
        # Keep the index's key in step with the file.
        old_rel = rel_posix(source, self.overlay_root)
        new_rel = rel_posix(target, self.overlay_root)
        if old_rel and new_rel and old_rel in self.index.overrides:
            self.index.overrides[new_rel] = self.index.overrides.pop(old_rel)
            self._save_index()
        self._refresh()
        self._w.statusBar().showMessage(
            f"Moved “{comp_def.name}” to {target_cat}", 6000)

    # -- remove -----------------------------------------------------------

    def remove(self, comp_def: ComponentDef, parent=None) -> None:
        """Hide a built-in; offer hide *or* a real delete for the user's own.

        A shipped component cannot be deleted — the tree may be inside a
        signed, read-only .app bundle — so hiding is the only thing that can
        happen, and it is reversible. For a component the user created or
        forked, respecting their agency means offering a genuine delete as
        well: tombstoning everything just accumulates junk.
        """
        if comp_def.is_embedded or not comp_def.svg_path:
            return
        owner = parent or self._w
        scope_key = self.tombstone_key(comp_def)
        if scope_key is None:
            QMessageBox.warning(self._w, "Cannot Remove",
                                "This component's location is not recognised.")
            return
        scope, key = scope_key

        if comp_def.is_builtin:
            reply = QMessageBox.question(
                owner, f"Hide “{comp_def.name}”?",
                f"<b>{comp_def.name}</b> ships with Diagrammer, so its file "
                "cannot be deleted — it lives inside the application. "
                "It can be hidden from the Library panel instead, and brought "
                "back at any time from Library › Library Overrides.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes)
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.index.hide(scope, key)
            if not self._save_index():
                return
            self._refresh()
            self._w.statusBar().showMessage(
                f"Hid “{comp_def.name}” — restore it from "
                "Library › Library Overrides", 8000)
            return

        box = QMessageBox(owner)
        box.setWindowTitle(f"Remove “{comp_def.name}”")
        box.setText(f"<b>{comp_def.name}</b> is your own component.")
        box.setInformativeText(
            f"{comp_def.svg_path}\n\n"
            "Hiding keeps the file and can be undone from Library › "
            "Library Overrides. Moving it to the Trash deletes it — "
            "recoverable from the Trash, but not from Diagrammer.")
        hide_btn = box.addButton("Hide", QMessageBox.ButtonRole.AcceptRole)
        trash_btn = box.addButton("Move to Trash",
                                  QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(hide_btn)
        box.exec()
        clicked = box.clickedButton()

        if clicked is hide_btn:
            self.index.hide(scope, key)
            if not self._save_index():
                return
            self._refresh()
            self._w.statusBar().showMessage(
                f"Hid “{comp_def.name}”", 6000)
        elif clicked is trash_btn:
            self._trash(comp_def)

    def _trash(self, comp_def: ComponentDef) -> None:
        from diagrammer.io.trash import move_to_trash

        source = Path(comp_def.svg_path)
        try:
            landed = move_to_trash(source)
        except OSError as exc:
            QMessageBox.critical(
                self._w, "Could Not Delete",
                f"{exc}\n\nThe file was left where it is.")
            return
        rel = rel_posix(source, self.overlay_root) or str(source)
        self.index.forget(rel)
        self.index.record_delete(rel=rel, trash=landed)
        # A tombstone for a file that no longer exists is the junk the user
        # asked not to accumulate, so drop it as the file goes.
        scope_key = self.tombstone_key(comp_def)
        if scope_key:
            self.index.unhide(*scope_key)
        self._save_index()
        self._refresh()
        self._w.statusBar().showMessage(
            f"Moved “{comp_def.name}” to the Trash", 6000)

    # -- revert / restore -------------------------------------------------

    def can_revert(self, comp_def: ComponentDef) -> bool:
        rel = rel_posix(Path(comp_def.svg_path), self.overlay_root) \
            if comp_def.svg_path else None
        return bool(rel) and self.index.is_fork(rel)

    def revert_to_builtin(self, comp_def: ComponentDef, parent=None) -> None:
        rel = rel_posix(Path(comp_def.svg_path), self.overlay_root)
        if not rel or not self.index.is_fork(rel):
            return
        reply = QMessageBox.question(
            parent or self._w, "Revert to Shipped Version",
            f"Move your copy of <b>{comp_def.name}</b> to the Trash and go "
            "back to the version that ships with Diagrammer?<br><br>"
            "Your edits will be in the Trash if you need them.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._trash(comp_def)

    def compare_with_builtin(self, comp_def: ComponentDef) -> None:
        self._w.open_component_inspector(Path(comp_def.svg_path))

    def shadowed_builtin(self, key: str) -> Path | None:
        """The shipped file a fork is hiding, if any."""
        paths = self._w._library.shadowed_paths(key)
        for p in paths:
            if rel_posix(p, self.builtin_root) is not None:
                return p
        return None
