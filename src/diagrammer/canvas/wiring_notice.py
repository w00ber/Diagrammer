"""The on-canvas notice for a wire that joins disagreeing leads (#47).

Regularize Wiring mode never blocks a connection: wiring a 2 px lead to a
3 px lead still makes the wire, and this notice appears beside it with the
choices — one button per verified fix (*Use 2 px*, *Use 3 px*) and *Ignore*
— plus a line naming the part that has a twin in the other library, the real
fix once Convert to Library exists.

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
        self.setStyleSheet(
            "#WiringNotice { background: palette(tooltip-base);"
            " color: palette(tooltip-text);"
            " border: 1px solid palette(mid); border-radius: 6px; }"
            "#WiringNotice QLabel { background: transparent;"
            " color: palette(tooltip-text); }")

        from diagrammer.wirecheck.scene import swap_hint
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

        row = QHBoxLayout()
        row.setSpacing(6)
        for fix in finding.fixes:
            btn = QPushButton(fix.label)
            btn.setAutoDefault(False)
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
        """Just below-right of the wire's end, kept inside the viewport."""
        vp = self._view.viewport()
        p = self._view.mapFromScene(anchor) + QPoint(16, 16)
        x = max(4, min(p.x(), vp.width() - self.width() - 4))
        y = max(4, min(p.y(), vp.height() - self.height() - 4))
        self.move(x, y)

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
