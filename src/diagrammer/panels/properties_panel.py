"""PropertiesPanel — shows and edits properties of the selected item."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PropertiesPanel(QDockWidget):
    """Dock widget that shows editable properties for the currently selected item."""

    def __init__(self, parent=None):
        super().__init__("Properties", parent)
        self._current_item = None
        self._scene = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._live_refresh)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(4)

        self._info_label = QLabel("No selection")
        self._info_label.setStyleSheet("color: #888;")
        self._layout.addWidget(self._info_label)

        self._form_widget = QWidget()
        self._form = QFormLayout(self._form_widget)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._form_widget)
        self._form_widget.hide()

        self._layout.addStretch()
        self.setWidget(container)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

    def _push_style(self, item, prop: str, old_val, new_val) -> None:
        """Push an undoable style change onto the scene's undo stack."""
        if old_val == new_val:
            return
        from diagrammer.commands.style_command import ChangeStyleCommand
        if self._scene and hasattr(self._scene, 'undo_stack'):
            cmd = ChangeStyleCommand(item, prop, old_val, new_val)
            self._scene.undo_stack.push(cmd)
        else:
            # Fallback: direct mutation
            setattr(item, prop, new_val)
            item.update()

    def update_for_selection(self, scene) -> None:
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import LineItem, ShapeItem

        self._scene = scene
        selected = scene.selectedItems()
        self._clear_form()
        self._current_item = None
        self._refresh_timer.stop()

        if len(selected) == 0:
            self._info_label.setText("No selection")
            self._info_label.show()
            self._form_widget.hide()
            return
        if len(selected) > 1:
            self._info_label.hide()
            self._form_widget.show()
            self._build_multi_form(selected)
            return

        item = selected[0]
        self._current_item = item
        self._info_label.hide()
        self._form_widget.show()

        if isinstance(item, ConnectionItem):
            self._build_connection_form(item)
        elif isinstance(item, ComponentItem):
            self._build_component_form(item)
        elif isinstance(item, (ShapeItem, LineItem)):
            self._build_shape_form(item)
        elif isinstance(item, AnnotationItem):
            self._build_annotation_form(item)
        elif isinstance(item, JunctionItem):
            self._info_label.setText("Junction")
            self._info_label.show()
            self._form_widget.hide()
        else:
            self._info_label.setText(type(item).__name__)
            self._info_label.show()
            self._form_widget.hide()

    def _clear_form(self) -> None:
        while self._form.rowCount() > 0:
            self._form.removeRow(0)
        self._live_widgets = {}

    def _live_refresh(self) -> None:
        """Periodically update read-only fields to reflect transforms."""
        item = self._current_item
        if item is None or item.scene() is None:
            self._refresh_timer.stop()
            return
        for key, widget in self._live_widgets.items():
            if key == "pos_x" and hasattr(item, 'pos'):
                widget.setText(f"{item.pos().x():.1f}")
            elif key == "pos_y" and hasattr(item, 'pos'):
                widget.setText(f"{item.pos().y():.1f}")
            elif key == "rotation" and hasattr(item, 'rotation_angle'):
                widget.setText(f"{item.rotation_angle:.1f}\u00b0")
            elif key == "flip" and hasattr(item, 'flip_h'):
                parts = []
                if item.flip_h:
                    parts.append("H")
                if item.flip_v:
                    parts.append("V")
                widget.setText(", ".join(parts) if parts else "None")

    # -- Connection form --

    def _build_connection_form(self, item) -> None:
        self._form.addRow(QLabel("<b>Connection</b>"))

        width_spin = QDoubleSpinBox()
        width_spin.setRange(0.5, 20.0)
        width_spin.setValue(item.line_width)
        width_spin.setSuffix(" pt")
        width_spin.setSingleStep(0.5)
        width_spin.valueChanged.connect(
            lambda v, it=item: self._push_style(it, 'line_width', it.line_width, v)
        )
        self._form.addRow("Width:", width_spin)

        radius_spin = QDoubleSpinBox()
        radius_spin.setRange(0.0, 50.0)
        radius_spin.setValue(item.corner_radius)
        radius_spin.setSuffix(" pt")
        radius_spin.setSingleStep(1.0)
        radius_spin.valueChanged.connect(
            lambda v, it=item: self._push_style(it, 'corner_radius', it.corner_radius, v)
        )
        self._form.addRow("Corner radius:", radius_spin)

        color_btn = self._make_color_button(
            item.line_color,
            lambda c, it=item: self._push_style(it, 'line_color', it.line_color, c),
        )
        self._form.addRow("Color:", color_btn)

    # -- Component form --

    def _build_component_form(self, item) -> None:
        self._form.addRow(QLabel(f"<b>{item.component_def.name}</b>"))

        # Position (live-updating)
        pos_x = QLabel(f"{item.pos().x():.1f}")
        self._form.addRow("X:", pos_x)
        pos_y = QLabel(f"{item.pos().y():.1f}")
        self._form.addRow("Y:", pos_y)

        rot_label = QLabel(f"{item.rotation_angle:.1f}\u00b0")
        self._form.addRow("Rotation:", rot_label)

        flip_parts = []
        if item.flip_h:
            flip_parts.append("H")
        if item.flip_v:
            flip_parts.append("V")
        flip_label = QLabel(", ".join(flip_parts) if flip_parts else "None")
        self._form.addRow("Flip:", flip_label)

        self._live_widgets = {
            "pos_x": pos_x,
            "pos_y": pos_y,
            "rotation": rot_label,
            "flip": flip_label,
        }
        self._refresh_timer.start()

        layer_idx = getattr(item, '_layer_index', 0)
        self._form.addRow("Layer:", QLabel(str(layer_idx)))

        if item.component_def.stretch_h or item.component_def.stretch_v:
            self._form.addRow("Stretch:", QLabel(f"dx={item.stretch_dx:.0f}, dy={item.stretch_dy:.0f}"))

    # -- Shape form --

    def _build_shape_form(self, item) -> None:
        from diagrammer.items.shape_item import ShapeItem
        self._form.addRow(QLabel(f"<b>{type(item).__name__}</b>"))

        width_spin = QDoubleSpinBox()
        width_spin.setRange(0.5, 20.0)
        width_spin.setValue(item.stroke_width)
        width_spin.setSuffix(" pt")
        width_spin.setSingleStep(0.5)
        width_spin.valueChanged.connect(
            lambda v, it=item: self._push_style(it, 'stroke_width', it.stroke_width, v)
        )
        self._form.addRow("Stroke:", width_spin)

        color_btn = self._make_color_button(
            item.stroke_color,
            lambda c, it=item: self._push_style(it, 'stroke_color', it.stroke_color, c),
        )
        self._form.addRow("Stroke color:", color_btn)

        if isinstance(item, ShapeItem):
            fill_btn = self._make_color_button(
                item.fill_color,
                lambda c, it=item: self._push_style(it, 'fill_color', it.fill_color, c),
                alpha=True,
            )
            self._form.addRow("Fill:", fill_btn)

            w_spin = QDoubleSpinBox()
            w_spin.setRange(10, 2000)
            w_spin.setValue(item.shape_width)
            w_spin.setSuffix(" pt")
            w_spin.valueChanged.connect(lambda v: item.resize(v, item.shape_height))
            self._form.addRow("Width:", w_spin)

            h_spin = QDoubleSpinBox()
            h_spin.setRange(10, 2000)
            h_spin.setValue(item.shape_height)
            h_spin.setSuffix(" pt")
            h_spin.valueChanged.connect(lambda v: item.resize(item.shape_width, v))
            self._form.addRow("Height:", h_spin)

    # -- Annotation form --

    def _build_annotation_form(self, item) -> None:
        from diagrammer.items.annotation_item import FONT_FAMILIES
        self._form.addRow(QLabel("<b>Text Annotation</b>"))

        # Font family
        family_combo = QComboBox()
        family_combo.addItems(FONT_FAMILIES)
        current = item.font_family
        if current in FONT_FAMILIES:
            family_combo.setCurrentText(current)
        else:
            family_combo.addItem(current)
            family_combo.setCurrentText(current)
        family_combo.currentTextChanged.connect(
            lambda v, it=item: self._push_style(it, 'font_family', it.font_family, v)
        )
        self._form.addRow("Font:", family_combo)

        # Font size
        size_spin = QDoubleSpinBox()
        size_spin.setRange(4.0, 144.0)
        size_spin.setValue(item.font_size)
        size_spin.setSuffix(" pt")
        size_spin.setSingleStep(1.0)
        size_spin.valueChanged.connect(
            lambda v, it=item: self._push_style(it, 'font_size', it.font_size, v)
        )
        self._form.addRow("Size:", size_spin)

        # Bold / Italic
        bold_cb = QCheckBox("Bold")
        bold_cb.setChecked(item.font_bold)
        bold_cb.toggled.connect(
            lambda v, it=item: self._push_style(it, 'font_bold', it.font_bold, v)
        )
        self._form.addRow("", bold_cb)

        italic_cb = QCheckBox("Italic")
        italic_cb.setChecked(item.font_italic)
        italic_cb.toggled.connect(
            lambda v, it=item: self._push_style(it, 'font_italic', it.font_italic, v)
        )
        self._form.addRow("", italic_cb)

        # Text color
        color_btn = self._make_color_button(
            item.text_color,
            lambda c, it=item: self._push_style(it, 'text_color', it.text_color, c),
        )
        self._form.addRow("Color:", color_btn)

        # Math hint
        self._form.addRow(QLabel("<small>Wrap LaTeX in $...$ for math</small>"))

        # Position (live-updating)
        pos_x = QLabel(f"{item.pos().x():.1f}")
        self._form.addRow("X:", pos_x)
        pos_y = QLabel(f"{item.pos().y():.1f}")
        self._form.addRow("Y:", pos_y)

        self._live_widgets = {"pos_x": pos_x, "pos_y": pos_y}
        self._refresh_timer.start()

    # -- Multi-selection form --

    def _build_multi_form(self, items: list) -> None:
        """Show shared editable properties for multiple selected items."""
        from diagrammer.items.connection_item import ConnectionItem
        self._form.addRow(QLabel(f"<b>{len(items)} items selected</b>"))

        # Check if all selected items are connections
        conns = [i for i in items if isinstance(i, ConnectionItem)]
        if conns:
            self._form.addRow(QLabel(f"{len(conns)} connection(s)"))

            # Line width (shared)
            width_spin = QDoubleSpinBox()
            width_spin.setRange(0.5, 20.0)
            width_spin.setValue(conns[0].line_width)
            width_spin.setSuffix(" pt")
            width_spin.setSingleStep(0.5)
            def set_all_width(v):
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.beginMacro("Set width")
                for c in conns:
                    self._push_style(c, 'line_width', c.line_width, v)
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.endMacro()
            width_spin.valueChanged.connect(set_all_width)
            self._form.addRow("Width:", width_spin)

            # Corner radius (shared)
            radius_spin = QDoubleSpinBox()
            radius_spin.setRange(0.0, 50.0)
            radius_spin.setValue(conns[0].corner_radius)
            radius_spin.setSuffix(" pt")
            radius_spin.setSingleStep(1.0)
            def set_all_radius(v):
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.beginMacro("Set corner radius")
                for c in conns:
                    self._push_style(c, 'corner_radius', c.corner_radius, v)
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.endMacro()
            radius_spin.valueChanged.connect(set_all_radius)
            self._form.addRow("Corner radius:", radius_spin)

            # Color (shared)
            color_btn = self._make_color_button(
                conns[0].line_color,
                lambda c: self._set_all_color(conns, c),
            )
            self._form.addRow("Color:", color_btn)

    def _set_all_color(self, conns, color) -> None:
        if self._scene and hasattr(self._scene, 'undo_stack'):
            self._scene.undo_stack.beginMacro("Set color")
        for c in conns:
            self._push_style(c, 'line_color', c.line_color, color)
        if self._scene and hasattr(self._scene, 'undo_stack'):
            self._scene.undo_stack.endMacro()

    def _make_color_button(self, color: QColor, callback, alpha: bool = False) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(60, 24)
        btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #888;")

        def pick():
            opts = QColorDialog.ColorDialogOption.ShowAlphaChannel if alpha else QColorDialog.ColorDialogOption(0)
            c = QColorDialog.getColor(color, self, "Choose Color", opts)
            if c.isValid():
                btn.setStyleSheet(f"background-color: {c.name()}; border: 1px solid #888;")
                callback(c)

        btn.clicked.connect(pick)
        return btn
