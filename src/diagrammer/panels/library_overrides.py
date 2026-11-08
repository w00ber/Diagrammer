"""Your edited copies of shipped components, and anything you have hidden.

This is where the one hard question about copy-on-write gets answered. Editing a
shipped component is easy; what is not obvious is what should happen when the
next release changes the component you forked. Preferring your copy forever
means you silently miss upstream fixes. Preferring theirs would throw your work
away. So neither happens automatically: the fork recorded the SHA-256 of the
built-in it came from, and when that changes this dialog says so and offers the
three answers that actually exist — keep mine, revert, or look at both.

"Look at both" opens the Component Inspector in side-by-side mode. That reuse is
not incidental: comparing two versions of a symbol is exactly what the inspector
already does well, and it is why the override bookkeeping and the validator are
one feature rather than two.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

DRIFT_COLOR = QColor("#D98B00")


class LibraryOverridesDialog(QDialog):
    """Three lists: forks, hidden components, and deleted files."""

    def __init__(self, parent=None, *, index, builtin_root: Path | None = None,
                 on_changed=None, on_compare=None):
        super().__init__(parent)
        self.setWindowTitle("Library Overrides")
        self.resize(820, 560)
        self.setSizeGripEnabled(True)
        self._index = index
        self._builtin_root = builtin_root
        self._on_changed = on_changed
        self._on_compare = on_compare

        v = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_forks_tab(), "My Copies")
        self._tabs.addTab(self._build_hidden_tab(), "Hidden")
        self._tabs.addTab(self._build_deleted_tab(), "Deleted")
        v.addWidget(self._tabs, 1)

        if index.read_only:
            warn = QLabel(
                "This override list was written by a newer version of "
                "Diagrammer, so it is shown read-only — changing it here could "
                "drop information that version recorded.")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #8A5A00;")
            v.addWidget(warn)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)

        self._refresh()

    # -- forks ------------------------------------------------------------

    def _build_forks_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        blurb = QLabel(
            "Components you have edited. Each one replaces the version that "
            "ships with Diagrammer; the shipped file is untouched, so you can "
            "revert at any time.")
        blurb.setWordWrap(True)
        v.addWidget(blurb)

        self._forks = QTreeWidget()
        self._forks.setHeaderLabels(["Component", "Status", "Forked from"])
        self._forks.setColumnWidth(0, 340)
        self._forks.setRootIsDecorated(False)
        self._forks.currentItemChanged.connect(self._sync_fork_buttons)
        self._forks.itemDoubleClicked.connect(lambda *_: self._compare())
        v.addWidget(self._forks, 1)

        row = QHBoxLayout()
        self._compare_btn = QPushButton("Compare with Shipped…")
        self._compare_btn.setAutoDefault(False)
        self._compare_btn.setToolTip(
            "Open both versions side by side in the Component Inspector")
        self._compare_btn.clicked.connect(self._compare)
        row.addWidget(self._compare_btn)

        self._keep_btn = QPushButton("Keep Mine")
        self._keep_btn.setAutoDefault(False)
        self._keep_btn.setToolTip(
            "Stop reporting this one until the shipped version changes again")
        self._keep_btn.clicked.connect(self._keep_mine)
        row.addWidget(self._keep_btn)

        self._revert_btn = QPushButton("Revert to Shipped…")
        self._revert_btn.setAutoDefault(False)
        self._revert_btn.setToolTip(
            "Move your copy to the Trash and go back to the shipped version")
        self._revert_btn.clicked.connect(self._revert)
        row.addWidget(self._revert_btn)
        row.addStretch(1)
        v.addLayout(row)
        return tab

    def _build_hidden_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        blurb = QLabel(
            "Components hidden from the Library panel. A shipped component "
            "cannot be deleted — it lives inside the application — so "
            "hiding is how it is removed, and it is always reversible.")
        blurb.setWordWrap(True)
        v.addWidget(blurb)

        self._hidden = QTreeWidget()
        self._hidden.setHeaderLabels(["Component", "Kind", "Hidden"])
        self._hidden.setColumnWidth(0, 420)
        self._hidden.setRootIsDecorated(False)
        v.addWidget(self._hidden, 1)

        row = QHBoxLayout()
        restore = QPushButton("Restore")
        restore.setAutoDefault(False)
        restore.clicked.connect(self._restore)
        row.addWidget(restore)
        tidy = QPushButton("Remove Dead Entries")
        tidy.setAutoDefault(False)
        tidy.setToolTip(
            "Drop tombstones whose component no longer exists — one an "
            "update removed, or a file deleted outside Diagrammer")
        tidy.clicked.connect(self._tidy)
        row.addWidget(tidy)
        row.addStretch(1)
        v.addLayout(row)
        return tab

    def _build_deleted_tab(self) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        blurb = QLabel(
            "Components you deleted outright. The files went to the Trash, so "
            "recovery is the system's job rather than Diagrammer's — this "
            "is the record of where they went.")
        blurb.setWordWrap(True)
        v.addWidget(blurb)
        self._deleted = QTreeWidget()
        self._deleted.setHeaderLabels(["Component", "When", "In Trash"])
        self._deleted.setColumnWidth(0, 300)
        self._deleted.setRootIsDecorated(False)
        v.addWidget(self._deleted, 1)
        return tab

    # -- population -------------------------------------------------------

    def _refresh(self) -> None:
        drifted = {d.rel for d in self._index.drift()}

        self._forks.clear()
        for rel, rec in sorted(self._index.overrides.items()):
            if rec.get("kind") == "fork":
                status = ("Shipped version has changed" if rel in drifted
                          else "Up to date")
                origin = rec.get("forked_app_version") or "?"
                origin = f"Diagrammer {origin}"
            else:
                status = "Your own component"
                origin = "—"
            item = QTreeWidgetItem(self._forks, [rel, status, origin])
            item.setData(0, Qt.ItemDataRole.UserRole, rel)
            if rel in drifted:
                item.setForeground(1, DRIFT_COLOR)
            if self._index.is_fork(rel) and self._index.fork_matches_builtin(rel):
                item.setText(1, "Identical to shipped — no longer needed")
        self._sync_fork_buttons()

        self._hidden.clear()
        for key, rec in sorted(self._index.hidden.items()):
            scope, _, rest = key.partition(":")
            label = "Shipped" if scope == "builtin" else "Yours"
            item = QTreeWidgetItem(self._hidden,
                                   [rest, label, rec.get("at", "")])
            item.setData(0, Qt.ItemDataRole.UserRole, (scope, rest))
            if rec.get("reason"):
                item.setToolTip(0, rec["reason"])

        self._deleted.clear()
        for rec in reversed(self._index.deleted):
            QTreeWidgetItem(self._deleted, [
                rec.get("rel", ""), rec.get("at", ""),
                rec.get("trash") or "(not recorded)"])

        n = len(drifted)
        self._tabs.setTabText(
            0, f"My Copies ({len(self._index.overrides)})"
            + (f" — {n} to review" if n else ""))
        self._tabs.setTabText(1, f"Hidden ({len(self._index.hidden)})")
        self._tabs.setTabText(2, f"Deleted ({len(self._index.deleted)})")

    def _sync_fork_buttons(self, *_args) -> None:
        rel = self._selected_rel()
        is_fork = bool(rel) and self._index.is_fork(rel)
        writable = not self._index.read_only
        # Comparing only reads, so it stays available even on an index a newer
        # version of the app wrote; only the two that would change it are gated.
        self._compare_btn.setEnabled(is_fork)
        self._keep_btn.setEnabled(is_fork and writable)
        self._revert_btn.setEnabled(is_fork and writable)

    def _selected_rel(self) -> str | None:
        item = self._forks.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    # -- actions ----------------------------------------------------------

    def _compare(self) -> None:
        rel = self._selected_rel()
        if not rel or self._on_compare is None:
            return
        # Opening the user's copy is right: the inspector's side-by-side mode
        # compares the candidate against the file it loaded, and the fork is
        # the one that may still need fixing.
        self._on_compare(self._index.overlay_path(rel))

    def _keep_mine(self) -> None:
        rel = self._selected_rel()
        if not rel:
            return
        self._index.accept_upstream_change(rel)
        self._commit()

    def _revert(self) -> None:
        rel = self._selected_rel()
        if not rel:
            return
        overlay = self._index.overlay_path(rel)
        reply = QMessageBox.question(
            self, "Revert to Shipped Version",
            f"Move your copy of {rel} to the Trash and go back to the version "
            "that ships with Diagrammer?\n\n"
            f"{overlay}\n\nYour edits will be in the Trash if you need them.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        from diagrammer.io.trash import move_to_trash

        try:
            trashed = move_to_trash(overlay) if overlay.is_file() else None
        except OSError as exc:
            QMessageBox.critical(self, "Could Not Revert", str(exc))
            return
        self._index.forget(rel)
        self._index.record_delete(rel=rel, trash=trashed)
        self._commit()

    def _restore(self) -> None:
        item = self._hidden.currentItem()
        if item is None:
            return
        scope, key = item.data(0, Qt.ItemDataRole.UserRole)
        self._index.unhide(scope, key)
        self._commit()

    def _tidy(self) -> None:
        dead = self._index.dead_tombstones()
        if not dead:
            QMessageBox.information(self, "Nothing to Tidy",
                                    "Every hidden component still exists.")
            return
        listing = "\n".join(f"  {k.partition(':')[2]}" for k in dead[:15])
        if len(dead) > 15:
            listing += f"\n  …and {len(dead) - 15} more"
        reply = QMessageBox.question(
            self, "Remove Dead Entries",
            f"Drop {len(dead)} hidden entr{'y' if len(dead) == 1 else 'ies'} "
            "whose component no longer exists?\n\n" + listing,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if reply != QMessageBox.StandardButton.Yes:
            return
        for key in dead:
            self._index.hidden.pop(key, None)
        self._commit()

    def _commit(self) -> None:
        if self._on_changed is not None:
            self._on_changed()
        self._refresh()
