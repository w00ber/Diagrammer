"""The on-canvas notice for a wire that joins disagreeing leads (#47).

Regularize Wiring mode never blocks a connection: wiring a 2 px lead to a
3 px lead still makes the wire, and this notice appears beside it with the
choices — one button per verified fix, in the order swap the part to its
twin, match the part's lead, set the wire (*Use 2 pt*, *Use 3 pt*), then
*Ignore* — plus a line saying which library most of the diagram uses.

It sits on the view's viewport, not in the scene: it is UI, so it is never
exported, never selected, and never on the undo stack (the fix it applies
is). It fades after :data:`LINGER_MS` unless the pointer is over it, and a
new notice replaces the old one.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

#: How long the notice stays before fading, unless hovered.
LINGER_MS = 8000
FADE_MS = 600


class WiringNotice(QFrame):
    def __init__(self, view, findings, anchor) -> None:
        super().__init__(view.viewport())
        self.setObjectName("WiringNotice")
        self._view = view
        self._scene = view.scene()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # Opaque, and explicit: the palette's tooltip colour is translucent
        # on macOS, which let the wiring show through the text. Light card
        # on the normal canvas, dark one when polarity is inverted.
        from diagrammer.utils.polarity import is_inverted
        bg, fg, border = (("#202020", "#eeeeee", "#5a5a5a") if is_inverted()
                          else ("#fffdf3", "#111111", "#c9b46a"))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            f"#WiringNotice {{ background-color: {bg}; color: {fg};"
            f" border: 1px solid {border}; border-radius: 6px; }}"
            f"#WiringNotice QLabel {{ background: transparent; color: {fg}; }}")

        from diagrammer.wirecheck.scene import (
            ordered_fixes,
            recommended_fix,
            swap_hint,
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        finding = findings[0]
        self._finding = finding
        title = QLabel("<b>Leads don't match</b>")
        lay.addWidget(title)
        msg = QLabel(_plain(finding.message))
        msg.setWordWrap(True)
        lay.addWidget(msg)
        hint = swap_hint(self._scene, finding)
        if hint:
            tip = QLabel(f"<i>{hint}</i>")
            tip.setWordWrap(True)
            lay.addWidget(tip)

        # The fix that matches the rest of the diagram goes first, in bold,
        # so the reason in the hint and the button order agree.
        # Swap to twin, match this part's lead, set the wire: in that order.
        best = recommended_fix(self._scene, finding)
        fixes = ordered_fixes(self._scene, finding)
        row = QHBoxLayout()
        row.setSpacing(6)
        for fix in fixes:
            btn = QPushButton(fix.label)
            btn.setAutoDefault(False)
            if fix is best:
                btn.setStyleSheet("font-weight: bold;")
                btn.setToolTip("Recommended: matches the rest of the diagram")
            btn.clicked.connect(lambda _=False, f=fix: self._apply(f))
            row.addWidget(btn)
        ignore = QPushButton("Ignore")
        ignore.setAutoDefault(False)
        ignore.clicked.connect(self.dismiss)
        row.addWidget(ignore)
        row.addStretch()
        lay.addLayout(row)

        self.setMaximumWidth(360)
        self.adjustSize()
        self._place(anchor)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InQuad)
        self._fade.finished.connect(self.dismiss)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade.start)
        self._timer.start(LINGER_MS)
        self.show()
        self.raise_()

    @property
    def finding(self):
        return self._finding

    def _place(self, anchor) -> None:
        """Beside the wire, where it covers the least of the drawing.

        Tries spots around *anchor* (the middle of the new wire) — right,
        left, above, below — keeps each inside the viewport, and picks the
        one whose footprint touches the fewest parts and wires. Ties go to
        the first, so with nothing nearby it sits to the right.
        """
        from PySide6.QtCore import QRect

        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        vp = self._view.viewport()
        a = self._view.mapFromScene(anchor)
        w, h, gap = self.width(), self.height(), 28
        spots = [
            QPoint(a.x() + gap, a.y() - h // 2),          # right
            QPoint(a.x() - w - gap, a.y() - h // 2),      # left
            QPoint(a.x() - w // 2, a.y() - h - gap),      # above
            QPoint(a.x() - w // 2, a.y() + gap),          # below
            QPoint(a.x() + gap, a.y() + gap),             # below-right
            QPoint(a.x() + gap, a.y() - h - gap),         # above-right
            QPoint(a.x() - w - gap, a.y() + gap),         # below-left
            QPoint(a.x() - w - gap, a.y() - h - gap),     # above-left
        ]

        def clamp(p: QPoint) -> QPoint:
            return QPoint(max(4, min(p.x(), vp.width() - w - 4)),
                          max(4, min(p.y(), vp.height() - h - 4)))

        def cost(p: QPoint) -> int:
            rect = QRect(p, self.size())
            area = self._view.mapToScene(rect).boundingRect()
            hits = self._scene.items(area, Qt.ItemSelectionMode.IntersectsItemShape)
            return sum(isinstance(i, (ComponentItem, ConnectionItem))
                       for i in hits)

        candidates = [clamp(p) for p in spots]
        self.move(min(candidates, key=cost))

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        self._fade.stop()
        self._effect.setOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.start(LINGER_MS // 2)
        super().leaveEvent(event)

    def _apply(self, fix) -> None:
        from diagrammer.wirecheck.scene import apply_fix
        apply_fix(self._scene, fix, text=f"Regularize wiring: {fix.label}")
        self.dismiss()

    def dismiss(self) -> None:
        self._timer.stop()
        self.hide()
        self.deleteLater()
        if getattr(self._view, "_wiring_notice", None) is self:
            self._view._wiring_notice = None


def _plain(message: str) -> str:
    """The engine's message, reworded for a person mid-gesture."""
    return (message.replace("leads at this wire's ends differ in width: ",
                            "This wire joins ")
            .replace("leads at this wire's ends differ in colour: ",
                     "Lead colours differ: "))


def show_wiring_notice(view, findings, anchor) -> WiringNotice | None:
    """Replace any notice on *view* with one for *findings*."""
    old = getattr(view, "_wiring_notice", None)
    if old is not None:
        old.dismiss()
    if not findings:
        return None
    view._wiring_notice = WiringNotice(view, findings, anchor)
    return view._wiring_notice
