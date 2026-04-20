"""ShapePropertiesDialog — edit stroke, fill, and size of simple shapes."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from diagrammer.items.shape_item import LineItem, ShapeItem


class ShapePropertiesDialog(QDialog):
    """Dialog for editing shape stroke, fill, and dimensions."""

    def __init__(self, shape: ShapeItem | LineItem, parent=None):
        super().__init__(parent)
        self._shape = shape
        self.setWindowTitle("Shape Properties")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Stroke color
        self._stroke_color = QColor(shape.stroke_color)
        self._stroke_btn = QPushButton()
        self._stroke_btn.setFixedSize(60, 24)
        self._update_color_btn(self._stroke_btn, self._stroke_color)
        self._stroke_btn.clicked.connect(self._pick_stroke_color)
        form.addRow("Stroke color:", self._stroke_btn)

        # Stroke width
        self._stroke_width_spin = QDoubleSpinBox()
        self._stroke_width_spin.setRange(0.5, 20.0)
        self._stroke_width_spin.setValue(shape.stroke_width)
        self._stroke_width_spin.setSuffix(" pt")
        self._stroke_width_spin.setSingleStep(0.5)
        form.addRow("Stroke width:", self._stroke_width_spin)

        # Fill color (only for ShapeItem, not LineItem)
        if isinstance(shape, ShapeItem):
            self._fill_color = QColor(shape.fill_color)
            self._fill_btn = QPushButton()
            self._fill_btn.setFixedSize(60, 24)
            self._update_color_btn(self._fill_btn, self._fill_color)
            self._fill_btn.clicked.connect(self._pick_fill_color)
            form.addRow("Fill color:", self._fill_btn)

            # Dimensions
            self._width_spin = QDoubleSpinBox()
            self._width_spin.setRange(10, 2000)
            self._width_spin.setValue(shape.shape_width)
            self._width_spin.setSuffix(" pt")
            form.addRow("Width:", self._width_spin)

            self._height_spin = QDoubleSpinBox()
            self._height_spin.setRange(10, 2000)
            self._height_spin.setValue(shape.shape_height)
            self._height_spin.setSuffix(" pt")
            form.addRow("Height:", self._height_spin)

        layout.addLayout(form)

        # OK / Cancel
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _update_color_btn(self, btn: QPushButton, color: QColor) -> None:
        btn.setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid #888;"
        )

    def _pick_stroke_color(self) -> None:
        c = QColorDialog.getColor(self._stroke_color, self, "Stroke Color")
        if c.isValid():
            self._stroke_color = c
            self._update_color_btn(self._stroke_btn, c)

    def _pick_fill_color(self) -> None:
        c = QColorDialog.getColor(
            self._fill_color, self, "Fill Color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if c.isValid():
            self._fill_color = c
            self._update_color_btn(self._fill_btn, c)

    def apply(self) -> None:
        """Apply the dialog values to the shape."""
        self._shape.stroke_color = self._stroke_color
        self._shape.stroke_width = self._stroke_width_spin.value()
        if isinstance(self._shape, ShapeItem):
            self._shape.fill_color = self._fill_color
            self._shape.resize(self._width_spin.value(), self._height_spin.value())
