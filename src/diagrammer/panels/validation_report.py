"""Validate a whole library and list what it found.

The design constraint here is noise. The engine deliberately reports only faults
that break something on the canvas as errors, but warnings are still plentiful —
74 shipped files declare a round linecap mid-migration — so a flat list of every
finding would be a wall nobody reads. Two things fix that: findings are grouped
by *check*, not by file, so the linecap migration is one row rather than 74; and
a check whose fix is ``bulk_safe`` offers to repair every file at once, which
turns that migration into a single deliberate act.

It lives in a tab beside the diagrams rather than a window of its own: it is
one long-lived list you come back to, with nothing to OK or cancel. The
Component Inspector stays a window, because you want it next to the external
editor you are fixing a part in.

**It never goes stale.** A report that still lists a fault you have just fixed
reads as "the fix did not work", so it re-checks without being asked:

* the moment the inspector saves a file (``file_saved``, called by the window);
* whenever the tab is shown again or the app regains focus, any listed file
  whose size or mtime changed — which covers an external editor, a ``git
  checkout``, or another report's bulk fix (``refresh_stale``).

Saving a *shipped* component from the inspector usually writes the user's own
copy into the overlay instead of the file listed here. The shipped file is
unchanged, so its findings stay — but its rows are marked as replaced by that
copy, and opening one opens the copy, which is what the canvas now uses.
"""

from __future__ import annotations

import logging
from pathlib import Path

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QThreadPool, QRunnable, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
SHADOWED_COLOR = QColor("#8A8A8A")


def _stamp(path: Path) -> tuple[int, int] | None:
    """Size and mtime: enough to tell that a file changed since it was read."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _key(path: Path) -> Path:
    """Reports are matched by resolved path: the writer and ``rglob`` can
    spell the same file differently (``/tmp`` vs ``/private/tmp`` on macOS)."""
    try:
        return path.resolve()
    except OSError:
        return path


def check_one(path: Path, overrides) -> tuple[svglint.FileReport,
                                              tuple[int, int] | None]:
    """Check one file, stamping it *before* the read so that a write landing
    mid-check leaves a stale stamp (re-checked later) rather than a fresh one
    over stale findings."""
    stamp = _stamp(path)
    profile = svglint.profile_for(path, overrides=overrides)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        findings = svglint.run_checks(text, path=path, profile=profile)
        read_error = None
    except OSError as exc:
        findings, read_error = [], str(exc)
    return svglint.FileReport(path=path, profile=profile,
                              findings=tuple(findings),
                              read_error=read_error), stamp


class _SweepSignals(QObject):
    progress = Signal(int, int)
    # ``object``, not list/dict: those convert through QVariant, which would
    # not keep Path keys.
    done = Signal(int, object, object)


class _SweepTask(QRunnable):
    """Run the checks off the GUI thread.

    229 files is a second or two — enough that the window would look hung, not
    enough to be worth cancelling mid-file. The engine touches no Qt, so it is
    safe to run here; only the signals cross back.
    """

    def __init__(self, generation: int, root: Path, overrides):
        super().__init__()
        self.generation = generation
        self.root = root
        self.overrides = overrides
        self.signals = _SweepSignals()

    def run(self) -> None:
        reports, stamps = [], {}
        try:
            files = svglint.iter_svgs([self.root])
            total = len(files)
            for i, path in enumerate(files, 1):
                report, stamp = check_one(path, self.overrides)
                reports.append(report)
                stamps[_key(path)] = stamp
                if i % 10 == 0 or i == total:
                    self.signals.progress.emit(i, total)
        except Exception:
            logger.exception("validation sweep failed")
            reports, stamps = [], {}
        self.signals.done.emit(self.generation, reports, stamps)


class ValidationReportView(QWidget):
    """Findings across a library, grouped by check, with click-through.

    ``shadowed_by(path)`` returns the user's own copy that replaces a shipped
    file on the canvas, or None; the window supplies it from the override
    index. Without it nothing is marked.
    """

    def __init__(self, parent=None, *, roots: list[tuple[str, Path]],
                 profile_overrides=None, on_open=None,
                 shadowed_by: Callable[[Path], Path | None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Validate Component Libraries")
        self._overrides = profile_overrides
        self._on_open = on_open
        self._shadowed_by = shadowed_by
        self._reports: list = []
        self._stamps: dict[Path, tuple[int, int] | None] = {}
        # The root the current reports were swept from; a re-check of a file
        # outside it is not this report's business.
        self._scanned_root: Path | None = None
        self._sweep_root: Path | None = None
        # Each sweep gets a number so a slow one finishing after a newer one
        # started cannot overwrite the newer results.
        self._generation = 0
        self._sweeping = False
        # Saved while a sweep was in flight: the sweep may have read the file
        # before the save, so these are re-checked once it lands.
        self._pending: set[Path] = set()
        self._note = ""

        v = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Scope:"))
        self._scope = QComboBox()
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
        v.addLayout(buttons)

        self._tree.currentItemChanged.connect(self._sync_bulk_button)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state_changed)
        self.set_roots(roots)

    def set_roots(self, roots: list[tuple[str, Path]]) -> None:
        """Offer these scopes and sweep the first. Reopening the report from
        another library's menu lands here rather than in a second tab."""
        self._scope.clear()
        for label, path in roots:
            self._scope.addItem(label, path)
        if roots:
            self._start()

    # -- running ----------------------------------------------------------

    def _start(self) -> None:
        root = self._scope.currentData()
        if root is None:
            return
        self._generation += 1
        self._sweeping = True
        self._sweep_root = Path(root)
        self._note = ""
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._summary.setText(f"Validating {root}…")
        task = _SweepTask(self._generation, Path(root), self._overrides)
        task.signals.progress.connect(self._on_progress)
        task.signals.done.connect(self._on_done)
        QThreadPool.globalInstance().start(task)

    def _on_progress(self, done: int, total: int) -> None:
        self._progress.setRange(0, total)
        self._progress.setValue(done)

    def _on_done(self, generation: int, reports: list, stamps: dict) -> None:
        if generation != self._generation:
            return  # superseded by a newer sweep
        self._sweeping = False
        self._progress.setVisible(False)
        self._scanned_root = self._sweep_root
        self._reports = reports
        self._stamps = stamps
        pending, self._pending = self._pending, set()
        if pending:
            self.recheck(pending)
        else:
            self._refresh_tree()

    # -- staying current ----------------------------------------------------

    def file_saved(self, path: Path, *, replaces: Path | None = None) -> None:
        """The inspector wrote ``path``. ``replaces`` is the shipped file it
        stands in for when the save made the user's own copy."""
        targets = {_key(Path(path))}
        if replaces is not None:
            targets.add(_key(Path(replaces)))
        self.recheck(targets)
        if self._sweeping:
            return
        name = Path(path).name
        if replaces is not None and not self._in_scope(_key(Path(path))):
            self._note = (
                f"Saved your copy of {name}. The shipped file listed here is "
                "unchanged, so it keeps its findings; it is marked as "
                "replaced, and opening it opens your copy. Choose “My "
                "Components” to check the copy itself.")
        else:
            report = self._report_for(_key(Path(path)))
            self._note = (f"Re-checked {name} after it was saved: "
                          f"{self._describe(report)}.")
        self._refresh_tree()

    def refresh_stale(self) -> None:
        """Re-check every listed file whose size or mtime moved."""
        if self._sweeping or not self._reports:
            return
        stale = {_key(r.path) for r in self._reports
                 if _stamp(r.path) != self._stamps.get(_key(r.path))}
        if stale:
            self._note = (f"Re-checked {len(stale)} file(s) that changed on "
                          "disk.")
            self.recheck(stale)

    def recheck(self, paths) -> None:
        """Re-run the checks on ``paths`` (resolved) that fall in scope:
        replace their rows, add a new file, drop a deleted one."""
        if self._sweeping:
            self._pending.update(paths)
            return
        for key in paths:
            if not self._in_scope(key):
                continue
            old = self._report_for(key)
            # Keep the sweep's spelling of a listed file, so the row the user
            # had selected is still recognised after the rebuild.
            path = old.path if old is not None else key
            self._reports = [r for r in self._reports if r is not old]
            self._stamps.pop(key, None)
            if path.is_file():
                report, stamp = check_one(path, self._overrides)
                self._reports.append(report)
                self._stamps[key] = stamp
        # Re-render even when nothing in scope changed: a fork elsewhere still
        # changes which rows here are marked as replaced.
        self._refresh_tree()

    def _in_scope(self, key: Path) -> bool:
        root = self._scanned_root
        if root is None or key.suffix.lower() != ".svg":
            return False
        try:
            rel = key.relative_to(_key(root))
        except ValueError:
            return False
        # The sweep's own rule (svglint.iter_svgs): no dot-prefixed parts.
        return not any(part.startswith(".") for part in rel.parts)

    def _report_for(self, key: Path):
        return next((r for r in self._reports if _key(r.path) == key), None)

    @staticmethod
    def _describe(report) -> str:
        if report is None:
            return "no longer present"
        if report.read_error:
            return "could not be read"
        e, w = len(report.errors), len(report.warnings)
        if not e and not w:
            return "now clean"
        parts = []
        if e:
            parts.append(f"{e} error{'s' if e != 1 else ''}")
        if w:
            parts.append(f"{w} warning{'s' if w != 1 else ''}")
        return ", ".join(parts)

    def _shadow_of(self, path: Path) -> Path | None:
        if self._shadowed_by is None:
            return None
        try:
            return self._shadowed_by(Path(path))
        except Exception:
            logger.debug("shadow lookup failed for %s", path, exc_info=True)
            return None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_stale()

    def _on_app_state_changed(self, state) -> None:
        # Back from an external editor: pick up what it wrote.
        if state == Qt.ApplicationState.ApplicationActive and self.isVisible():
            self.refresh_stale()

    # -- the tree ---------------------------------------------------------

    def _visible_findings(self):
        for report in self._reports:
            for f in report.findings:
                if self._errors_only.isChecked() and f.severity != "error":
                    continue
                yield report, f

    def _refresh_tree(self) -> None:
        # A re-check rebuilds the tree; keep the user's place in it.
        expanded = {
            self._tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole + 1)
            for i in range(self._tree.topLevelItemCount())
            if self._tree.topLevelItem(i).isExpanded()
        }
        had_tree = self._tree.topLevelItemCount() > 0
        cur = self._tree.currentItem()
        current = None if cur is None else (
            cur.data(0, Qt.ItemDataRole.UserRole + 1),
            cur.data(0, Qt.ItemDataRole.UserRole))
        reselect = None

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
            head.setExpanded(check_id in expanded if had_tree
                             else len(rows) <= 12)
            if current == (check_id, None):
                reselect = head
            for report, f in sorted(rows, key=lambda rf: str(rf[0].path)):
                library = report.profile.label
                copy = self._shadow_of(report.path)
                if copy is not None:
                    library = f"{library} — replaced by your copy"
                child = QTreeWidgetItem(head, [
                    f"{report.path.name} — {f.message}",
                    f.check_id, library])
                tip = f"{report.path}\n\n{f.detail or f.message}"
                if copy is not None:
                    tip += (f"\n\nThe canvas uses your copy instead:\n{copy}"
                            "\nOpening this row opens the copy.")
                    for col in range(3):
                        child.setForeground(col, SHADOWED_COLOR)
                child.setToolTip(0, tip)
                child.setData(0, Qt.ItemDataRole.UserRole, report.path)
                child.setData(0, Qt.ItemDataRole.UserRole + 1, check_id)
                if current is not None and current == (check_id, report.path):
                    reselect = child
        if reselect is not None:
            self._tree.setCurrentItem(reselect)

        files = len(self._reports)
        failing = sum(1 for r in self._reports if r.errors)
        warning = sum(1 for r in self._reports
                      if not r.errors and r.warnings)
        replaced = sum(1 for r in self._reports
                       if r.findings and self._shadow_of(r.path) is not None)
        text = (f"{files - failing - warning} sound, {warning} with warnings, "
                f"{failing} failing (of {files} components)")
        if replaced:
            text += f"; {replaced} of those replaced by your own copies"
        text += ". Double-click a row to inspect it."
        if self._note:
            text = f"{self._note}\n{text}"
        self._summary.setText(text)
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
            # The copy, if there is one: it is what the canvas uses, and
            # inspecting the shipped file again would only offer to fork it
            # a second time over the edits already made.
            self._on_open(self._shadow_of(Path(path)) or Path(path))

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

