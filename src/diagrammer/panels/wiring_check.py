"""Routing ▸ Check Wiring…: audit an existing diagram's wires (#47 stage 3).

Regularize Wiring mode only sees a wire as it is drawn. This panel is what
fixes a diagram that already exists: it lists every finding from
:mod:`diagrammer.wirecheck`, grouped by check like the component Validation
tab, and lets each one be accepted or rejected.

* **Selecting** a finding selects its wires and frames them in the view. The
  selection *is* the highlight, and it puts the wire in Properties.
* **Accept** applies the chosen fix (only fixes :func:`wirecheck.verified`
  passes are offered) as one undo step. *Accept all like this* applies the
  same kind of fix to every finding of that check, still one undo step.
* **Reject** marks the wires ``style_deliberate`` (saved in the ``.dgm``):
  the checks skip them from then on. *Show rejected* brings those back,
  greyed, with *Restore* to clear the mark.

It is a dock, not a tab like the Validation report: zoom-to is useless if
the list covers the canvas. It follows the active diagram, and it never goes
stale: any change on the diagram's undo stack re-checks it (debounced),
which also covers an accept, a reject and undoing either.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diagrammer import wirecheck

#: What each check is called in the list.
CHECK_TITLES = {
    "wire-width": "Wire width differs from its lead",
    "wire-color": "Wire colour differs from its lead",
    "leads-disagree": "Leads at the ends differ in width",
    "leads-disagree-color": "Leads at the ends differ in colour",
    "net-mixed-width": "Joined wires differ in width",
    "net-mixed-color": "Joined wires differ in colour",
}
LIVE_COLOR = QColor("#D98B00")
REJECTED_COLOR = QColor("#8A8A8A")
_ROLE = Qt.ItemDataRole.UserRole

#: Re-check this long after the last change; a drag pushes many commands.
RECHECK_MS = 150
#: Zoom-to never magnifies past this, so a single short wire stays legible
#: in its surroundings instead of filling the view.
MAX_ZOOM = 2.0


class WiringCheckPanel(QDockWidget):
    def __init__(self, parent=None) -> None:
        super().__init__("Check Wiring", parent)
        self.setObjectName("WiringCheckPanel")
        self._scene = None
        self._view = None
        self._stack = None
        # (finding, rejected) per row, by row id; rebuilt on every check.
        self._rows: dict[int, tuple[wirecheck.Finding, bool]] = {}
        self._findings: list[wirecheck.Finding] = []
        self._rejected: list[wirecheck.Finding] = []
        self._snap = None
        # Set while a rebuild puts the user's row back: re-selecting it must
        # not move the view or replace the canvas selection.
        self._restoring = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RECHECK_MS)
        self._timer.timeout.connect(self.recheck)

        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(6, 6, 6, 6)

        top = QHBoxLayout()
        self._show_rejected = QCheckBox("Show rejected")
        self._show_rejected.setToolTip(
            "Also list findings hidden because their wires are marked as "
            "kept on purpose: a rejected suggestion, or a width already "
            "chosen for leads that disagree.")
        self._show_rejected.toggled.connect(self._refresh_tree)
        top.addWidget(self._show_rejected)
        top.addStretch(1)
        again = QPushButton("Re-check")
        again.setAutoDefault(False)
        again.clicked.connect(self.recheck)
        top.addWidget(again)
        v.addLayout(top)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Finding", "Wires"])
        self._tree.setColumnWidth(0, 300)
        self._tree.currentItemChanged.connect(self._on_current)
        self._tree.itemDoubleClicked.connect(lambda *_: self._accept())
        v.addWidget(self._tree, 1)

        self._detail = QLabel()
        self._detail.setWordWrap(True)
        v.addWidget(self._detail)

        fix_row = QHBoxLayout()
        fix_row.addWidget(QLabel("Fix:"))
        self._fix_combo = QComboBox()
        self._fix_combo.currentIndexChanged.connect(self._sync_buttons)
        fix_row.addWidget(self._fix_combo, 1)
        v.addLayout(fix_row)

        buttons = QHBoxLayout()
        self._accept_btn = QPushButton("Accept")
        self._accept_btn.setToolTip("Apply the chosen fix (one undo step).")
        self._accept_btn.clicked.connect(self._accept)
        self._accept_all_btn = QPushButton("Accept all like this")
        self._accept_all_btn.setToolTip(
            "Apply the same fix to every finding of this kind, as one undo "
            "step. A finding with a single fix takes that fix; one with a "
            "choice takes the option with the same name.")
        self._accept_all_btn.clicked.connect(self._accept_all)
        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setToolTip(
            "Keep these wires as they are: mark them deliberate, so no check "
            "flags them again. Saved with the diagram.")
        self._reject_btn.clicked.connect(self._reject)
        for b in (self._accept_btn, self._accept_all_btn, self._reject_btn):
            b.setAutoDefault(False)
            buttons.addWidget(b)
        v.addLayout(buttons)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        v.addWidget(self._summary)

        self.setWidget(body)
        self._sync_buttons()

    # -- binding ------------------------------------------------------------

    def set_document(self, scene, view) -> None:
        """Follow *scene* (and frame findings in *view*)."""
        if self._stack is not None:
            try:
                self._stack.indexChanged.disconnect(self._schedule)
            except (RuntimeError, TypeError):
                pass
        self._scene, self._view = scene, view
        self._stack = getattr(scene, "undo_stack", None)
        if self._stack is not None:
            self._stack.indexChanged.connect(self._schedule)
        # A hidden panel checks when shown (showEvent), not on every tab
        # switch.
        if self.isVisible():
            self.recheck()
        else:
            self._snap, self._findings, self._rejected = None, [], []

    def _schedule(self, *_args) -> None:
        if self.isVisible():
            self._timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.recheck()

    # -- checking -----------------------------------------------------------

    def recheck(self) -> None:
        self._timer.stop()
        if self._scene is None:
            self._snap, self._findings, self._rejected = None, [], []
        else:
            from diagrammer.wirecheck.scene import snapshot
            snap = snapshot(self._scene)
            self._snap = snap
            self._findings = [self._verified(snap, f)
                              for f in wirecheck.check(snap)]
            self._rejected = wirecheck.rejected(snap)
        self._refresh_tree()

    def _verified(self, snap, finding):
        """*finding* with only its verified fixes, in the order offered:
        swap, lead, wire; the one matching the diagram first in each."""
        from diagrammer.wirecheck.scene import ordered_fixes
        good = ordered_fixes(self._scene, [
            fx for fx in finding.fixes if wirecheck.verified(snap, finding, fx)])
        return wirecheck.Finding(finding.check_id, finding.message,
                                 finding.wire_ids, tuple(good), finding.data)

    def findings(self, *, include_rejected: bool = False):
        """What the list shows: ``(finding, rejected)`` pairs."""
        out = [(f, False) for f in self._findings]
        if include_rejected:
            out += [(f, True) for f in self._rejected]
        return out

    # -- the tree -----------------------------------------------------------

    def _refresh_tree(self) -> None:
        cur = self._current()
        current_key = None if cur is None else cur[0].key
        expanded = {self._tree.topLevelItem(i).data(0, _ROLE)
                    for i in range(self._tree.topLevelItemCount())
                    if self._tree.topLevelItem(i).isExpanded()}
        had_tree = self._tree.topLevelItemCount() > 0

        self._tree.blockSignals(True)
        self._tree.clear()
        self._rows.clear()
        groups: dict[str, list] = {}
        for f, rej in self.findings(
                include_rejected=self._show_rejected.isChecked()):
            groups.setdefault(f.check_id, []).append((f, rej))
        reselect = None
        for check_id in sorted(groups, key=lambda c: (-len(groups[c]), c)):
            rows = groups[check_id]
            live = sum(1 for _f, r in rows if not r)
            title = CHECK_TITLES.get(check_id, check_id)
            count = f"{live}" + (f" (+{len(rows) - live} rejected)"
                                 if live < len(rows) else "")
            head = QTreeWidgetItem(self._tree, [f"{title} — {count}", ""])
            head.setData(0, _ROLE, check_id)
            head.setForeground(0, LIVE_COLOR if live else REJECTED_COLOR)
            head.setExpanded(check_id in expanded if had_tree else True)
            for f, rej in rows:
                text = self._describe(f) + ("  — rejected" if rej else "")
                child = QTreeWidgetItem(head, [text, str(len(f.wire_ids))])
                child.setToolTip(0, f.message)
                child.setData(0, _ROLE, check_id)
                self._rows[id(child)] = (f, rej)
                if rej:
                    for col in range(2):
                        child.setForeground(col, REJECTED_COLOR)
                if f.key == current_key:
                    reselect = child
        self._tree.blockSignals(False)
        self._restoring = True
        try:
            if reselect is not None:
                self._tree.setCurrentItem(reselect)
            else:
                self._on_current(self._tree.currentItem(), None)
        finally:
            self._restoring = False

        n, r = len(self._findings), len(self._rejected)
        if self._scene is None:
            text = "No diagram."
        elif not n:
            text = "Every wire matches its leads."
        else:
            text = f"{n} finding{'s' if n != 1 else ''}."
        if r:
            text += f" {r} rejected" + (" (shown)." if
                                        self._show_rejected.isChecked()
                                        else " (hidden).")
        self._summary.setText(text)

    def _describe(self, finding) -> str:
        """The row text: the parts involved, then what is wrong."""
        snap = self._snap
        labels, seen = [], set()
        if snap is not None:
            for wid in finding.wire_ids:
                w = snap.wires.get(wid)
                if w is None:
                    continue
                for e in w.ends():
                    # By instance, not by label: a PAPER and a SLIDES IND1
                    # print alike and are exactly the pair worth showing.
                    if e.kind == "part" and (e.ref, e.port) not in seen:
                        seen.add((e.ref, e.port))
                        labels.append(e.label or f"{e.ref}:{e.port}")
        where = " – ".join(labels[:3]) + ("…" if len(labels) > 3 else "")
        return f"{where}: {finding.message}" if where else finding.message

    def _current(self):
        item = self._tree.currentItem()
        return None if item is None else self._rows.get(id(item))

    def _on_current(self, item, _prev) -> None:
        self._fix_combo.blockSignals(True)
        self._fix_combo.clear()
        row = None if item is None else self._rows.get(id(item))
        if row is None:
            self._detail.setText("")
        else:
            f, rej = row
            if rej:
                self._fix_combo.addItem("Restore (clear the kept mark)", None)
                self._detail.setText(
                    f"{f.message}\n\nThese wires are marked as kept on "
                    "purpose, so the checks skip them.")
            else:
                for fx in f.fixes:
                    self._fix_combo.addItem(fx.label, fx)
                self._detail.setText(
                    f.message if f.fixes else
                    f"{f.message}\n\nNo fix clears this without raising "
                    "another; Reject keeps it as it is.")
            if not self._restoring:
                self.zoom_to(f)
        self._fix_combo.blockSignals(False)
        self._sync_buttons()

    def _sync_buttons(self, *_args) -> None:
        row = self._current()
        live = row is not None and not row[1]
        has_fix = live and self._fix_combo.currentData() is not None
        self._accept_btn.setText("Restore" if row and row[1] else "Accept")
        self._accept_btn.setEnabled(row is not None and (has_fix or row[1]))
        self._accept_all_btn.setEnabled(has_fix)
        self._reject_btn.setEnabled(live)

    # -- zoom-to ------------------------------------------------------------

    def zoom_to(self, finding) -> None:
        """Select the finding's wires and frame them in the view."""
        if self._scene is None:
            return
        conns = self._connections(finding.wire_ids)
        if not conns:
            return
        self._scene.clearSelection()
        rect = QRectF()
        for c in conns:
            c.setSelected(True)
            rect = rect.united(c.sceneBoundingRect())
        view = self._view
        if view is None:
            return
        margin = max(rect.width(), rect.height(), 80.0) * 0.4
        rect.adjust(-margin, -margin, margin, margin)
        view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        scale = view.transform().m11()
        if scale > MAX_ZOOM:
            view.scale(MAX_ZOOM / scale, MAX_ZOOM / scale)
            view.centerOn(rect.center())

    def _connections(self, wire_ids):
        from diagrammer.items.connection_item import ConnectionItem
        want = set(wire_ids)
        return [i for i in self._scene.items()
                if isinstance(i, ConnectionItem) and i.instance_id in want]

    # -- actions ------------------------------------------------------------

    def _apply(self, fix, text: str) -> None:
        from diagrammer.wirecheck.scene import apply_fix
        if fix.changes:
            apply_fix(self._scene, fix, text)
        self.recheck()

    def _accept(self) -> None:
        row = self._current()
        if row is None:
            return
        f, rej = row
        if rej:
            self._apply(wirecheck.Fix("Restore wiring check", {
                wid: {"deliberate": False} for wid in f.wire_ids}),
                "Restore wiring check")
            return
        fix = self._fix_combo.currentData()
        if fix is not None:
            self._apply(fix, f"Wiring: {fix.label}")

    def accept_all_like(self, finding, fix) -> int:
        """Apply *fix*'s kind to every live finding of *finding*'s check as
        one undo step. Returns how many findings it resolved."""
        picks = []
        for f in self._findings:
            if f.check_id != finding.check_id:
                continue
            if f.key == finding.key:
                choice = fix
            else:
                choice = next((fx for fx in f.fixes if _alike(fx, fix, f)),
                              None)
            if choice is not None:
                picks.append((f, choice))
        title = CHECK_TITLES.get(finding.check_id, finding.check_id)
        combined, taken = wirecheck.combine(
            self._snap, picks, f"Wiring: accept all — {title}")
        self._apply(combined, combined.label)
        return taken

    def _accept_all(self) -> None:
        row = self._current()
        fix = self._fix_combo.currentData()
        if row is None or row[1] or fix is None:
            return
        self.accept_all_like(row[0], fix)

    def _reject(self) -> None:
        row = self._current()
        if row is None or row[1]:
            return
        f = row[0]
        self._apply(wirecheck.Fix("Reject wiring finding", {
            wid: {"deliberate": True} for wid in f.wire_ids}),
            "Reject wiring finding")

    # -- for tests ------------------------------------------------------------

    def select_finding(self, finding) -> bool:
        """Make the row for *finding* current (matched by key)."""
        for i in range(self._tree.topLevelItemCount()):
            head = self._tree.topLevelItem(i)
            for j in range(head.childCount()):
                child = head.child(j)
                row = self._rows.get(id(child))
                if row and row[0].key == finding.key:
                    self._tree.setCurrentItem(child)
                    return True
        return False


def _alike(fx, fix, finding) -> bool:
    """Whether *fx* is "the same fix" as *fix*, for Accept all like this.

    The same kind (swap, lead, wire) toward the same value. The one
    exception is a wire fix on a ``wire-<attr>`` finding: that is "take
    the lead's value", the same fix whatever the value, so every such
    finding takes its own lead's.
    """
    if fx.kind != fix.kind:
        return False
    if fx.kind == "wire" and finding.check_id in ("wire-width", "wire-color"):
        return True
    a, b = fx.target, fix.target
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= wirecheck.WIDTH_TOL
    return a == b or fx.label == fix.label

