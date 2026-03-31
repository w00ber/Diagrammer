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
    QHBoxLayout,
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
        elif isinstance(item, ComponentItem) and getattr(item, '_isolation_mode', False):
            self._build_component_isolation_form(item)
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

        # Set as Default button
        default_btn = QPushButton("Set as Default")
        default_btn.setToolTip("Use this wire's style as the default for new connections")
        default_btn.clicked.connect(lambda checked=False, it=item: self._set_wire_defaults(it))
        self._form.addRow("", default_btn)

    def _set_wire_defaults(self, item) -> None:
        """Copy this connection's style to app-wide wiring defaults."""
        from diagrammer.panels.settings_dialog import app_settings
        app_settings.default_line_width = item.line_width
        app_settings.default_line_color = item.line_color
        app_settings.default_corner_radius = item.corner_radius
        app_settings.save()

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
        from diagrammer.items.shape_item import (
            ARROW_BACKWARD, ARROW_BOTH, ARROW_FORWARD, ARROW_NONE,
            DASH_PATTERNS, LineItem, RectangleItem, ShapeItem,
        )
        self._form.addRow(QLabel(f"<b>{type(item).__name__}</b>"))

        # Stroke width
        width_spin = QDoubleSpinBox()
        width_spin.setRange(0.5, 20.0)
        width_spin.setValue(item.stroke_width)
        width_spin.setSuffix(" pt")
        width_spin.setSingleStep(0.5)
        width_spin.valueChanged.connect(
            lambda v, it=item: self._push_style(it, 'stroke_width', it.stroke_width, v)
        )
        self._form.addRow("Stroke:", width_spin)

        # Stroke color (with alpha)
        color_btn = self._make_color_button(
            item.stroke_color,
            lambda c, it=item: self._push_style(it, 'stroke_color', it.stroke_color, c),
            alpha=True,
        )
        self._form.addRow("Stroke color:", color_btn)

        # Dash style
        dash_combo = QComboBox()
        dash_combo.addItems(list(DASH_PATTERNS.keys()))
        dash_combo.setCurrentText(item.dash_style)
        dash_combo.currentTextChanged.connect(
            lambda v, it=item: self._push_style(it, 'dash_style', it.dash_style, v)
        )
        self._form.addRow("Dash:", dash_combo)

        if isinstance(item, ShapeItem):
            # Fill color
            fill_btn = self._make_color_button(
                item.fill_color,
                lambda c, it=item: self._push_style(it, 'fill_color', it.fill_color, c),
                alpha=True,
            )
            self._form.addRow("Fill:", fill_btn)

            # Corner radius (rectangle only)
            if isinstance(item, RectangleItem):
                radius_spin = QDoubleSpinBox()
                radius_spin.setRange(0.0, 100.0)
                radius_spin.setValue(item.corner_radius)
                radius_spin.setSuffix(" pt")
                radius_spin.setSingleStep(1.0)
                radius_spin.valueChanged.connect(
                    lambda v, it=item: self._push_style(it, 'corner_radius', it.corner_radius, v)
                )
                self._form.addRow("Corner radius:", radius_spin)

            # Dimensions
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

        elif isinstance(item, LineItem):
            # Cap style
            cap_combo = QComboBox()
            cap_combo.addItems(["round", "square"])
            cap_combo.setCurrentText(item.cap_style)
            cap_combo.currentTextChanged.connect(
                lambda v, it=item: self._push_style(it, 'cap_style', it.cap_style, v)
            )
            self._form.addRow("End caps:", cap_combo)

            # Arrowhead direction
            arrow_combo = QComboBox()
            arrow_combo.addItems([ARROW_NONE, ARROW_FORWARD, ARROW_BACKWARD, ARROW_BOTH])
            arrow_combo.setCurrentText(item.arrow_style)
            arrow_combo.currentTextChanged.connect(
                lambda v, it=item: self._push_style(it, 'arrow_style', it.arrow_style, v)
            )
            self._form.addRow("Arrows:", arrow_combo)

            # Arrowhead type
            from diagrammer.items.shape_item import ARROW_TYPES
            type_combo = QComboBox()
            type_combo.addItems(ARROW_TYPES)
            type_combo.setCurrentText(item.arrow_type)
            type_combo.currentTextChanged.connect(
                lambda v, it=item: self._push_style(it, 'arrow_type', it.arrow_type, v)
            )
            self._form.addRow("Arrow type:", type_combo)

            # Arrowhead scale
            scale_spin = QDoubleSpinBox()
            scale_spin.setRange(0.1, 5.0)
            scale_spin.setValue(item.arrow_scale)
            scale_spin.setSingleStep(0.1)
            scale_spin.setDecimals(1)
            scale_spin.setSuffix("x")
            scale_spin.valueChanged.connect(
                lambda v, it=item: self._push_style(it, 'arrow_scale', it.arrow_scale, v)
            )
            self._form.addRow("Arrow size:", scale_spin)

            # Arrowhead extend
            extend_spin = QDoubleSpinBox()
            extend_spin.setRange(0.0, 50.0)
            extend_spin.setValue(item.arrow_extend)
            extend_spin.setSingleStep(1.0)
            extend_spin.setSuffix(" pt")
            extend_spin.valueChanged.connect(
                lambda v, it=item: self._push_style(it, 'arrow_extend', it.arrow_extend, v)
            )
            self._form.addRow("Arrow extend:", extend_spin)

        # Set as Default button for all shape types
        default_btn = QPushButton("Set as Default")
        default_btn.setToolTip("Use this shape's style as the default for new shapes")
        default_btn.clicked.connect(lambda checked=False, it=item: self._set_shape_defaults(it))
        self._form.addRow("", default_btn)

    def _set_shape_defaults(self, item) -> None:
        """Copy this shape/line's style to app-wide defaults."""
        from diagrammer.items.shape_item import LineItem, RectangleItem, ShapeItem
        from diagrammer.panels.settings_dialog import app_settings
        app_settings.default_shape_stroke_color = item.stroke_color
        app_settings.default_shape_stroke_width = item.stroke_width
        app_settings.default_shape_dash_style = item.dash_style
        if isinstance(item, ShapeItem):
            app_settings.default_shape_fill_color = item.fill_color
            if isinstance(item, RectangleItem):
                app_settings.default_shape_corner_radius = item.corner_radius
        if isinstance(item, LineItem):
            app_settings.default_shape_cap_style = item.cap_style
            app_settings.default_shape_arrow_type = item.arrow_type
            app_settings.default_shape_arrow_scale = item.arrow_scale
            app_settings.default_shape_arrow_extend = item.arrow_extend
        app_settings.save()

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

        # Set as Default button
        default_btn = QPushButton("Set as Default")
        default_btn.setToolTip("Use this annotation's style as the default for new annotations")
        default_btn.clicked.connect(lambda checked=False, it=item: self._set_annotation_defaults(it))
        self._form.addRow("", default_btn)

        # Position (live-updating)
        pos_x = QLabel(f"{item.pos().x():.1f}")
        self._form.addRow("X:", pos_x)
        pos_y = QLabel(f"{item.pos().y():.1f}")
        self._form.addRow("Y:", pos_y)

        self._live_widgets = {"pos_x": pos_x, "pos_y": pos_y}
        self._refresh_timer.start()

    def _set_annotation_defaults(self, item) -> None:
        """Copy this annotation's style to the app-wide defaults."""
        from diagrammer.panels.settings_dialog import app_settings
        app_settings.default_annotation_font = item.font_family
        app_settings.default_annotation_size = item.font_size
        app_settings.default_annotation_color = item.text_color
        app_settings.default_annotation_bold = item.font_bold
        app_settings.default_annotation_italic = item.font_italic
        app_settings.save()

    # -- Component isolation mode form --

    def _build_component_isolation_form(self, item) -> None:
        """Show element list with flash-to-identify and style controls."""
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        from diagrammer.items.shape_item import DASH_PATTERNS
        from diagrammer.models.svg_style_override import SvgElementStyleOverride

        self._form.addRow(QLabel(f"<b>{item.component_def.name}</b> — Edit Elements"))

        infos = item.get_element_infos()
        if not infos:
            self._form.addRow(QLabel("<small>No editable elements found</small>"))
            return

        # Element list — click to select, Shift+click for multi-select
        from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem
        self._form.addRow(QLabel("<small>Select elements to edit (Shift+click for multi):</small>"))
        elem_list = QListWidget()
        elem_list.setMaximumHeight(150)
        elem_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for info in infos:
            has_ovr = item.style_overrides.get(info.element_path) is not None
            label = f"{'● ' if has_ovr else ''}{info.label}"
            li = QListWidgetItem(label)
            li.setData(Qt.ItemDataRole.UserRole, info.element_path)
            if has_ovr:
                li.setForeground(QColor(0, 100, 200))
            elem_list.addItem(li)

        # Pre-select if item already has selected elements
        if item._selected_element_paths:
            for i in range(elem_list.count()):
                if elem_list.item(i).data(Qt.ItemDataRole.UserRole) in item._selected_element_paths:
                    elem_list.item(i).setSelected(True)

        def _on_selection_changed():
            selected_items = elem_list.selectedItems()
            if not selected_items:
                return
            paths = {si.data(Qt.ItemDataRole.UserRole) for si in selected_items}
            item._selected_element_paths = paths
            # Rebuild controls for the selected element(s)
            self._rebuild_isolation_controls(item, paths)
            # Flash the most recently clicked element
            current = elem_list.currentItem()
            if current:
                self._flash_element(item, current.data(Qt.ItemDataRole.UserRole))

        elem_list.itemSelectionChanged.connect(_on_selection_changed)
        self._form.addRow(elem_list)
        self._isolation_list = elem_list

        # Style controls placeholder — filled when element is selected
        self._isolation_controls_widget = QWidget()
        self._isolation_controls_layout = QFormLayout(self._isolation_controls_widget)
        self._isolation_controls_layout.setContentsMargins(0, 0, 0, 0)
        self._form.addRow(self._isolation_controls_widget)

        # If already selected, show controls
        if item._selected_element_paths:
            self._rebuild_isolation_controls(item, item._selected_element_paths)

    def _rebuild_isolation_controls(self, item, element_paths) -> None:
        """Rebuild the style controls for the selected element(s).

        Args:
            element_paths: A single path string or a set of path strings.
        """
        from diagrammer.models.svg_style_override import SvgElementStyleOverride

        layout = self._isolation_controls_layout
        while layout.rowCount() > 0:
            layout.removeRow(0)

        if isinstance(element_paths, str):
            paths = {element_paths}
        else:
            paths = set(element_paths)

        if not paths:
            return

        if len(paths) > 1:
            layout.addRow(QLabel(f"<b>{len(paths)} elements selected</b>"))

        # Collect effective values for all selected elements
        def _effective_val(path, prop, css_prop):
            ovr = item.style_overrides.get(path)
            val = getattr(ovr, prop, None) if ovr else None
            if val is None:
                css = self._get_element_original_css(item, path)
                val = css.get(css_prop)
            return val

        stroke_vals = {_effective_val(p, 'stroke_color', 'stroke') or '#000000' for p in paths}
        fill_vals = {_effective_val(p, 'fill_color', 'fill') or 'none' for p in paths}
        width_vals = set()
        for p in paths:
            w = _effective_val(p, 'stroke_width', 'stroke-width')
            if w:
                try:
                    width_vals.add(float(str(w).replace('px', '').replace('pt', '').strip()))
                except ValueError:
                    pass
            else:
                width_vals.add(3.0)
        cap_vals = {_effective_val(p, 'stroke_linecap', 'stroke-linecap') for p in paths}

        # For display: use common value if all same, otherwise show "mixed"
        stroke_mixed = len(stroke_vals) > 1
        fill_mixed = len(fill_vals) > 1
        width_mixed = len(width_vals) > 1
        cap_mixed = len(cap_vals) > 1

        stroke_val = next(iter(stroke_vals)) if not stroke_mixed else '#000000'
        fill_val = next(iter(fill_vals)) if not fill_mixed else 'none'
        width_val = next(iter(width_vals)) if not width_mixed else 3.0
        cap_val = next(iter(cap_vals)) if not cap_mixed else None

        first_path = next(iter(paths))
        ovr = item.style_overrides.get(first_path) or SvgElementStyleOverride()

        # Stroke color (with "none" option)
        stroke_row = QWidget()
        stroke_layout = QHBoxLayout(stroke_row)
        stroke_layout.setContentsMargins(0, 0, 0, 0)
        stroke_layout.setSpacing(4)

        current_stroke = QColor(stroke_val) if stroke_val != "none" else QColor(0, 0, 0)
        if stroke_mixed:
            stroke_btn = QPushButton("Mixed")
            stroke_btn.setFixedSize(60, 24)
            stroke_btn.setStyleSheet("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                                     "stop:0 #ff0000,stop:0.5 #00ff00,stop:1 #0000ff);"
                                     "border:1px solid #888; color:white; font-weight:bold;")
            stroke_btn.clicked.connect(lambda: self._pick_isolation_color(
                item, paths, 'stroke_color', current_stroke))
        else:
            stroke_btn = self._make_color_button(
                current_stroke,
                lambda c, it=item, p=paths: self._set_isolation_style(it, p, 'stroke_color', c.name()),
                alpha=True,
            )
        stroke_layout.addWidget(stroke_btn)

        stroke_none_btn = QPushButton("None")
        stroke_none_btn.setFixedSize(40, 24)
        stroke_none_btn.clicked.connect(
            lambda checked=False, it=item, p=paths: self._set_isolation_style(it, p, 'stroke_color', 'none')
        )
        stroke_layout.addWidget(stroke_none_btn)
        stroke_layout.addStretch()
        layout.addRow("Stroke:", stroke_row)

        # Fill color (with "none" option)
        fill_row = QWidget()
        fill_layout = QHBoxLayout(fill_row)
        fill_layout.setContentsMargins(0, 0, 0, 0)
        fill_layout.setSpacing(4)

        current_fill = QColor(fill_val) if fill_val and fill_val != "none" else QColor(255, 255, 255, 0)
        if fill_mixed:
            fill_btn = QPushButton("Mixed")
            fill_btn.setFixedSize(60, 24)
            fill_btn.setStyleSheet("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                                   "stop:0 #ff0000,stop:0.5 #00ff00,stop:1 #0000ff);"
                                   "border:1px solid #888; color:white; font-weight:bold;")
            fill_btn.clicked.connect(lambda: self._pick_isolation_color(
                item, paths, 'fill_color', current_fill))
        else:
            fill_btn = self._make_color_button(
                current_fill,
                lambda c, it=item, p=paths: self._set_isolation_style(
                    it, p, 'fill_color',
                    c.name() if c.alpha() == 255 else f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f})"),
                alpha=True,
            )
        fill_layout.addWidget(fill_btn)

        fill_none_btn = QPushButton("None")
        fill_none_btn.setFixedSize(40, 24)
        fill_none_btn.clicked.connect(
            lambda checked=False, it=item, p=paths: self._set_isolation_style(it, p, 'fill_color', 'none')
        )
        fill_layout.addWidget(fill_none_btn)
        fill_layout.addStretch()
        layout.addRow("Fill:", fill_row)

        # Stroke width
        width_spin = QDoubleSpinBox()
        width_spin.setRange(0.0, 20.0)
        width_spin.setValue(width_val)
        width_spin.setSuffix(" pt")
        width_spin.setSingleStep(0.5)
        width_spin.valueChanged.connect(
            lambda v, it=item, p=paths: self._set_isolation_style(it, p, 'stroke_width', v)
        )
        layout.addRow("Width:", width_spin)

        # Stroke linecap
        cap_combo = QComboBox()
        cap_combo.addItems(["(default)", "round", "butt", "square"])
        if cap_val:
            cap_combo.setCurrentText(cap_val)
        elif cap_mixed:
            cap_combo.setCurrentText("(default)")
        cap_combo.currentTextChanged.connect(
            lambda v, it=item, p=paths: self._set_isolation_style(
                it, p, 'stroke_linecap', None if v == "(default)" else v)
        )
        layout.addRow("End caps:", cap_combo)

        # Opacity
        opacity_spin = QDoubleSpinBox()
        opacity_spin.setRange(0.0, 1.0)
        opacity_spin.setValue(ovr.opacity if ovr.opacity is not None else 1.0)
        opacity_spin.setSingleStep(0.1)
        opacity_spin.setDecimals(2)
        opacity_spin.valueChanged.connect(
            lambda v, it=item, p=paths: self._set_isolation_style(
                it, p, 'opacity', v if v < 1.0 else None)
        )
        layout.addRow("Opacity:", opacity_spin)

        # Reset
        reset_btn = QPushButton("Reset to Default")
        reset_btn.clicked.connect(
            lambda checked=False, it=item, p=paths: self._reset_isolation_styles(it, p)
        )
        layout.addRow("", reset_btn)

    def _flash_element(self, item, element_path: str) -> None:
        """Flash an SVG element by toggling a bright highlight style.

        Temporarily overrides the element's stroke/fill to a bright color,
        then restores the original. Repeats 3 times for visibility.
        Does NOT use set_element_style (which permanently modifies overrides).
        Instead, directly manipulates the override dict and invalidates.
        """
        from PySide6.QtCore import QTimer
        import copy
        from diagrammer.models.svg_style_override import SvgElementStyleOverride

        # Deep-copy the entire override for this element to restore exactly
        orig_ovr = item.style_overrides.get(element_path)
        saved_ovr = copy.deepcopy(orig_ovr) if orig_ovr else None

        flash_color = "#ff2222"
        count = [0]  # mutable counter for closure

        def _do_flash():
            if count[0] >= 6:
                # Final restore — put back exactly what was there before
                if saved_ovr:
                    item._style_overrides.overrides[element_path] = copy.deepcopy(saved_ovr)
                else:
                    item._style_overrides.overrides.pop(element_path, None)
                item._invalidate_renderers()
                return

            if count[0] % 2 == 0:
                # Flash ON
                ovr = item._style_overrides.get(element_path) or SvgElementStyleOverride()
                ovr.stroke_color = flash_color
                ovr.fill_color = flash_color
                item._style_overrides.overrides[element_path] = ovr
            else:
                # Flash OFF — restore saved state
                if saved_ovr:
                    item._style_overrides.overrides[element_path] = copy.deepcopy(saved_ovr)
                else:
                    item._style_overrides.overrides.pop(element_path, None)

            item._invalidate_renderers()
            count[0] += 1
            QTimer.singleShot(200, _do_flash)

        _do_flash()

    def _get_element_original_css(self, item, element_path: str) -> dict[str, str]:
        """Read the original CSS properties for an SVG element from the component's SVG file."""
        import re
        import xml.etree.ElementTree as ET2

        def _strip_ns(tag):
            return tag.split("}", 1)[1] if "}" in tag else tag

        try:
            tree = ET2.parse(str(item.component_def.svg_path))
            root = tree.getroot()
        except Exception:
            return {}

        # Parse CSS classes
        css_classes: dict[str, dict[str, str]] = {}
        for elem in root.iter():
            if _strip_ns(elem.tag) == "style" and elem.text:
                for m in re.finditer(r'([^{}]+)\{([^}]+)\}', elem.text):
                    props = {}
                    for pm in re.finditer(r'([\w-]+)\s*:\s*([^;]+)', m.group(2)):
                        props[pm.group(1).strip()] = pm.group(2).strip()
                    for cm in re.finditer(r'\.(\w+)', m.group(1)):
                        css_classes.setdefault(cm.group(1), {}).update(props)

        # Walk to find the element by path
        for layer_id in ("artwork", "leads"):
            layer = None
            for elem in root.iter():
                if _strip_ns(elem.tag) == "g" and elem.get("id") == layer_id:
                    layer = elem
                    break
            if layer is None:
                continue

            from diagrammer.models.svg_element_info import _LEAF_TAGS
            result = self._walk_for_css(layer, layer_id, element_path, css_classes, _LEAF_TAGS)
            if result is not None:
                return result
        return {}

    def _walk_for_css(self, elem, path_prefix, target_path, css_classes, leaf_tags) -> dict[str, str] | None:
        """Walk SVG tree to find element at target_path and return its CSS properties."""
        import re

        def _strip_ns(tag):
            return tag.split("}", 1)[1] if "}" in tag else tag

        child_idx = 0
        for child in elem:
            tag = _strip_ns(child.tag)
            child_path = f"{path_prefix}/{child_idx}"
            if tag == "g":
                result = self._walk_for_css(child, child_path, target_path, css_classes, leaf_tags)
                if result is not None:
                    return result
            elif tag in leaf_tags and child_path == target_path:
                props: dict[str, str] = {}
                for cls in child.get("class", "").split():
                    if cls in css_classes:
                        props.update(css_classes[cls])
                for attr in ("stroke", "fill", "stroke-width", "stroke-linecap",
                             "stroke-linejoin", "opacity"):
                    val = child.get(attr)
                    if val:
                        props[attr] = val
                existing = child.get("style", "")
                if existing:
                    for pm in re.finditer(r'([\w-]+)\s*:\s*([^;]+)', existing):
                        props[pm.group(1).strip()] = pm.group(2).strip()
                return props
            child_idx += 1
        return None

    def _pick_isolation_color(self, item, paths, prop, initial_color) -> None:
        """Open color picker for mixed-color multi-selection."""
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(initial_color, self, "Choose Color",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            if prop == 'fill_color':
                val = c.name() if c.alpha() == 255 else f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f})"
            else:
                val = c.name()
            self._set_isolation_style(item, paths, prop, val)
            # Rebuild controls to show the new uniform color
            self._rebuild_isolation_controls(item, paths)

    def _set_isolation_style(self, item, paths: set, prop: str, value) -> None:
        """Apply a style property to all selected sub-elements (undoable)."""
        from diagrammer.commands.svg_style_command import ChangeSvgStyleCommand
        if self._scene and hasattr(self._scene, 'undo_stack'):
            self._scene.undo_stack.beginMacro(f"Set {prop}")
        for path in paths:
            old_val = item.get_element_style(path, prop)
            if old_val != value:
                cmd = ChangeSvgStyleCommand(item, path, prop, old_val, value)
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.push(cmd)
                else:
                    item.set_element_style(path, prop, value)
        if self._scene and hasattr(self._scene, 'undo_stack'):
            self._scene.undo_stack.endMacro()

    def _reset_isolation_styles(self, item, paths: set) -> None:
        """Clear all style overrides on selected sub-elements (undoable)."""
        from diagrammer.commands.svg_style_command import ChangeSvgStyleCommand
        from diagrammer.models.svg_style_override import SvgElementStyleOverride
        if self._scene and hasattr(self._scene, 'undo_stack'):
            self._scene.undo_stack.beginMacro("Reset styles")
        for path in paths:
            ovr = item.style_overrides.get(path)
            if ovr and not ovr.is_empty():
                for prop in ('stroke_color', 'fill_color', 'stroke_width',
                             'stroke_dasharray', 'stroke_linecap', 'opacity'):
                    old_val = getattr(ovr, prop, None)
                    if old_val is not None:
                        cmd = ChangeSvgStyleCommand(item, path, prop, old_val, None)
                        if self._scene and hasattr(self._scene, 'undo_stack'):
                            self._scene.undo_stack.push(cmd)
        if self._scene and hasattr(self._scene, 'undo_stack'):
            self._scene.undo_stack.endMacro()
        self.update_for_selection(self._scene)

    # -- Multi-selection form --

    def _build_multi_form(self, items: list) -> None:
        """Show shared editable properties for multiple selected items."""
        from diagrammer.items.annotation_item import AnnotationItem, FONT_FAMILIES
        from diagrammer.items.connection_item import ConnectionItem
        self._form.addRow(QLabel(f"<b>{len(items)} items selected</b>"))

        # Connections
        conns = [i for i in items if isinstance(i, ConnectionItem)]
        if conns:
            self._form.addRow(QLabel(f"{len(conns)} connection(s)"))

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

            color_btn = self._make_color_button(
                conns[0].line_color,
                lambda c: self._set_all_color(conns, c),
            )
            self._form.addRow("Color:", color_btn)

        # Annotations
        annots = [i for i in items if isinstance(i, AnnotationItem)]
        if annots:
            self._form.addRow(QLabel(f"{len(annots)} annotation(s)"))

            family_combo = QComboBox()
            family_combo.addItems(FONT_FAMILIES)
            current = annots[0].font_family
            if current in FONT_FAMILIES:
                family_combo.setCurrentText(current)
            else:
                family_combo.addItem(current)
                family_combo.setCurrentText(current)
            def set_all_font(v):
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.beginMacro("Set font")
                for a in annots:
                    self._push_style(a, 'font_family', a.font_family, v)
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.endMacro()
            family_combo.currentTextChanged.connect(set_all_font)
            self._form.addRow("Font:", family_combo)

            size_spin = QDoubleSpinBox()
            size_spin.setRange(4.0, 144.0)
            size_spin.setValue(annots[0].font_size)
            size_spin.setSuffix(" pt")
            size_spin.setSingleStep(1.0)
            def set_all_size(v):
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.beginMacro("Set font size")
                for a in annots:
                    self._push_style(a, 'font_size', a.font_size, v)
                if self._scene and hasattr(self._scene, 'undo_stack'):
                    self._scene.undo_stack.endMacro()
            size_spin.valueChanged.connect(set_all_size)
            self._form.addRow("Size:", size_spin)

            color_btn = self._make_color_button(
                annots[0].text_color,
                lambda c: self._set_all_annot_color(annots, c),
            )
            self._form.addRow("Color:", color_btn)

    def _set_all_annot_color(self, annots, color) -> None:
        if self._scene and hasattr(self._scene, 'undo_stack'):
            self._scene.undo_stack.beginMacro("Set annotation color")
        for a in annots:
            self._push_style(a, 'text_color', a.text_color, color)
        if self._scene and hasattr(self._scene, 'undo_stack'):
            self._scene.undo_stack.endMacro()

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
