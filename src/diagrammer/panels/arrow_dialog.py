"""WireArrowPropertiesDialog — edit a single wire direction arrow.

Per-arrow overrides for style, size, and outline width; each override
row has a "Use default" checkbox that resets the field to follow the
app-wide defaults (Settings → Line Styles). The dialog is passive: the
caller reads ``result_fields()`` on accept and pushes one undo command.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from diagrammer.items.connection_item import WireArrow


class WireArrowPropertiesDialog(QDialog):
    """Dialog for editing one direction arrow's overrides."""

    def __init__(self, arrow: WireArrow, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Direction Arrow Properties")
        self.setMinimumWidth(300)

        from diagrammer.panels.settings_dialog import app_settings

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._dir_combo = QComboBox()
        self._dir_combo.addItems(["Forward (source → target)",
                                  "Backward (target → source)"])
        self._dir_combo.setCurrentIndex(0 if arrow.forward else 1)
        form.addRow("Direction:", self._dir_combo)

        self._style_combo = QComboBox()
        self._style_combo.addItem("Default", None)
        self._style_combo.addItem("Filled", "filled")
        self._style_combo.addItem("Hollow", "open")
        idx = self._style_combo.findData(arrow.style)
        self._style_combo.setCurrentIndex(max(0, idx))
        form.addRow("Style:", self._style_combo)

        default_size = getattr(app_settings, "default_wire_arrow_size", 14.0)
        self._size_row, self._size_default_cb, self._size_spin = self._override_row(
            arrow.size, default_size, 4.0, 60.0, 1.0)
        form.addRow("Size:", self._size_row)

        default_lw = getattr(app_settings, "default_wire_arrow_line_width", 2.0)
        self._lw_row, self._lw_default_cb, self._lw_spin = self._override_row(
            arrow.line_width, default_lw, 0.5, 10.0, 0.5)
        form.addRow("Outline width:", self._lw_row)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def _override_row(
        value: float | None, default: float,
        minimum: float, maximum: float, step: float,
    ) -> tuple[QWidget, QCheckBox, QDoubleSpinBox]:
        """A "Use default" checkbox paired with a spinbox override."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        cb = QCheckBox("Use default")
        cb.setChecked(value is None)
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSuffix(" pt")
        spin.setSingleStep(step)
        spin.setValue(default if value is None else value)
        spin.setEnabled(value is not None)
        cb.toggled.connect(lambda checked, s=spin: s.setEnabled(not checked))
        h.addWidget(cb)
        h.addWidget(spin)
        return row, cb, spin

    def result_fields(self) -> dict:
        """WireArrow field values reflecting the dialog state."""
        return {
            "forward": self._dir_combo.currentIndex() == 0,
            "style": self._style_combo.currentData(),
            "size": None if self._size_default_cb.isChecked() else self._size_spin.value(),
            "line_width": None if self._lw_default_cb.isChecked() else self._lw_spin.value(),
        }
