"""Validate a whole library and list what it found.

The design constraint here is noise. The engine deliberately reports only faults
that break something on the canvas as errors, but warnings are still plentiful —
74 shipped files declare a round linecap mid-migration — so a flat list of every
finding would be a wall nobody reads. Two things fix that: findings are grouped
by *check*, not by file, so the linecap migration is one row rather than 74; and
a check whose fix is ``bulk_safe`` offers to repair every file at once, which
turns that migration into a single deliberate act.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, QRunnable, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diagrammer import svglint

logger = logging.getLogger(__name__)

SEVERITY_COLOR = {
    "error": QColor("#D0342C"),
    "warning": QColor("#D98B00"),
    "info": QColor("#2C6FD0"),
}
SEVERITY_GLYPH = {"error": "✕", "warning": "⚠", "info": "ℹ"}


class _SweepSignals(QObject):
    progress = Signal(int, int)
    done = Signal(list)


class _SweepTask(QRunnable):
    """Run the checks off the GUI thread.

    229 files is a second or two — enough that the window would look hung, not
    enough to be worth cancelling mid-file. The engine touches no Qt, so it is
    safe to run here; only the signals cross back.
    """

    def __init__(self, root: Path, overrides):
        super().__init__()
        self.root = root
        self.overrides = overrides
        self.signals = _SweepSignals()

    def run(self) -> None:
        try:
            files = svglint.iter_svgs([self.root])
            total = len(files)
            reports = []
            for i, path in enumerate(files, 1):
                profile = svglint.profile_for(path, overrides=self.overrides)
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    findings = svglint.run_checks(text, path=path,
                                                  profile=profile)
                    read_error = None
                except OSError as exc:
                    findings, read_error = [], str(exc)
                reports.append(svglint.FileReport(
                    path=path, profile=profile,
                    findings=tuple(findings), read_error=read_error))
                if i % 10 == 0 or i == total:
                    self.signals.progress.emit(i, total)
            self.signals.done.emit(reports)
        except Exception:
            logger.exception("validation sweep failed")
            self.signals.done.emit([])


class ValidationReportDialog(QDialog):
    """Findings across a library, grouped by check, with click-through."""

    def __init__(self, parent=None, *, roots: list[tuple[str, Path]],
                 profile_overrides=None, on_open=None):
        super().__init__(parent)
        self.setWindowTitle("Validate Component Libraries")
        self.resize(900, 620)
        self.setSizeGripEnabled(True)
        self._roots = roots
        self._overrides = profile_overrides
        self._on_open = on_open
        self._reports: list = []

        v = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Scope:"))
        self._scope = QComboBox()
        for label, path in roots:
            self._scope.addItem(label, path)
        row.addWidget(self._scope, 1)
        self._errors_only = QCheckBox("Errors only")
        self._errors_only.setToolTip(
            "Hide warnings. Errors are the faults that misbehave on the "
            "canvas; a warning may well be deliberate.")
        self._errors_only.toggled.connect(self._refresh_tree)
        row.addWidget(self._errors_only)
        run = QPushButton("Validate")
        run.setAutoDefault(False)
        run.clicked.connect(self._start)
        row.addWidget(run)
        v.addLayout(row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        v.addWidget(self._progress)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Component / finding", "Check", "Library"])
        self._tree.setColumnWidth(0, 440)
        self._tree.setColumnWidth(1, 200)
        self._tree.itemDoubleClicked.connect(self._open_selected)
        v.addWidget(self._tree, 1)

        self._summary = QLabel("Choose a scope and press Validate.")
        self._summary.setWordWrap(True)
        v.addWidget(self._summary)

        buttons = QHBoxLayout()
        self._bulk_btn = QPushButton("Fix in all files…")
        self._bulk_btn.setAutoDefault(False)
        self._bulk_btn.setEnabled(False)
        self._bulk_btn.setToolTip(
            "Apply the selected check's fix to every file it affects. Offered "
            "only for fixes that cannot move geometry.")
        self._bulk_btn.clicked.connect(self._bulk_fix)
        buttons.addWidget(self._bulk_btn)

        open_btn = QPushButton("Inspect…")
        open_btn.setAutoDefault(False)
        open_btn.clicked.connect(self._open_selected)
        buttons.addWidget(open_btn)

        buttons.addStretch(1)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        v.addLayout(buttons)

        self._tree.currentItemChanged.connect(self._sync_bulk_button)
        if roots:
            self._start()

    # -- running ----------------------------------------------------------

    def _start(self) -> None:
        root = self._scope.currentData()
        if root is None:
            return
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._summary.setText(f"Validating {root}…")
        task = _SweepTask(Path(root), self._overrides)
        task.signals.progress.connect(self._on_progress)
        task.signals.done.connect(self._on_done)
        QThreadPool.globalInstance().start(task)

    def _on_progress(self, done: int, total: int) -> None:
        self._progress.setRange(0, total)
        self._progress.setValue(done)

    def _on_done(self, reports: list) -> None:
        self._progress.setVisible(False)
        self._reports = reports
        self._refresh_tree()

    # -- the tree ---------------------------------------------------------

    def _visible_findings(self):
        for report in self._reports:
            for f in report.findings:
                if self._errors_only.isChecked() and f.severity != "error":
                    continue
                yield report, f

    def _refresh_tree(self) -> None:
        self._tree.clear()
        by_check: dict[str, list] = {}
        for report, f in self._visible_findings():
            by_check.setdefault(f.check_id, []).append((report, f))

        # Worst first, then widest: an error in one file outranks a warning in
        # seventy, because the error is the one that is actually broken.
        def order(item):
            check_id, rows = item
            worst = min(f.rank for _r, f in rows)
            return (worst, -len(rows), check_id)

        for check_id, rows in sorted(by_check.items(), key=order):
            severity = min((f for _r, f in rows), key=lambda f: f.rank).severity
            head = QTreeWidgetItem(self._tree, [
                f"{SEVERITY_GLYPH.get(severity, '')} {check_id} "
                f"— {len(rows)} file(s)", check_id, ""])
            head.setForeground(0, SEVERITY_COLOR.get(severity, QColor("#333")))
            head.setData(0, Qt.ItemDataRole.UserRole + 1, check_id)
            head.setExpanded(len(rows) <= 12)
            for report, f in sorted(rows, key=lambda rf: str(rf[0].path)):
                child = QTreeWidgetItem(head, [
                    f"{report.path.name} — {f.message}",
                    f.check_id, report.profile.label])
                child.setToolTip(0, f"{report.path}\n\n{f.detail or f.message}")
                child.setData(0, Qt.ItemDataRole.UserRole, report.path)
                child.setData(0, Qt.ItemDataRole.UserRole + 1, check_id)

        files = len(self._reports)
        failing = sum(1 for r in self._reports if r.errors)
        warning = sum(1 for r in self._reports
                      if not r.errors and r.warnings)
        self._summary.setText(
            f"{files - failing - warning} sound, {warning} with warnings, "
            f"{failing} failing (of {files} components). "
            "Double-click a row to inspect it.")
        self._sync_bulk_button()

    def _current_check_id(self) -> str | None:
        item = self._tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole + 1)

    def _sync_bulk_button(self, *_args) -> None:
        check_id = self._current_check_id()
        fix = self._bulk_fix_for(check_id) if check_id else None
        self._bulk_btn.setEnabled(fix is not None)
        self._bulk_btn.setText(
            f"Fix in all {len(self._files_for(check_id))} files…"
            if fix is not None else "Fix in all files…")

    def _bulk_fix_for(self, check_id: str | None):
        """The one bulk-safe fix this check offers, if it has exactly one.

        Nothing that moves geometry is bulk-safe, which is why there is no
        "snap every port to the grid" button anywhere: 97 of 208 shipped files
        miss the grid and most do so by design.
        """
        if not check_id:
            return None
        candidates = {
            fid
            for report in self._reports
            for f in report.findings
            if f.check_id == check_id
            for fid in f.fix_ids
        }
        fixes = [svglint.FIXES_BY_ID[i] for i in candidates
                 if i in svglint.FIXES_BY_ID
                 and svglint.FIXES_BY_ID[i].bulk_safe]
        return fixes[0] if len(fixes) == 1 else None

    def _files_for(self, check_id: str | None) -> list[Path]:
        if not check_id:
            return []
        return [r.path for r in self._reports
                if any(f.check_id == check_id for f in r.findings)]

    # -- actions ----------------------------------------------------------

    def _open_selected(self, *_args) -> None:
        item = self._tree.currentItem()
        if item is None or self._on_open is None:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path is None and item.childCount():
            path = item.child(0).data(0, Qt.ItemDataRole.UserRole)
        if path is not None:
            self._on_open(Path(path))

    def _bulk_fix(self) -> None:
        check_id = self._current_check_id()
        fix = self._bulk_fix_for(check_id)
        if fix is None:
            return
        files = self._files_for(check_id)
        listing = "\n".join(f"  {p.name}" for p in files[:15])
        if len(files) > 15:
            listing += f"\n  …and {len(files) - 15} more"
        reply = QMessageBox.question(
            self, "Fix in All Files",
            f"Apply “{fix.title}” to {len(files)} file(s)?\n\n"
            f"{listing}\n\n"
            "Each file is written in place. This cannot be undone from the "
            "app, so make sure the tree is committed or backed up first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        changed = failed = 0
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
                findings = svglint.check_file(path)
                candidate, _notes = svglint.apply_fixes(
                    text, [fix], findings,
                    profile=svglint.profile_for(path, overrides=self._overrides),
                    path=path)
                if candidate != text:
                    from diagrammer.io.component_writer import write_text_atomic
                    write_text_atomic(candidate, path)
                    changed += 1
            except Exception:
                logger.warning("bulk fix failed on %s", path, exc_info=True)
                failed += 1
        QMessageBox.information(
            self, "Done",
            f"{changed} file(s) changed"
            + (f", {failed} failed — see the log." if failed else "."))
        self._start()
