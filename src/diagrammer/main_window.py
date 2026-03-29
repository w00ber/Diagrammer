"""MainWindow — the top-level application window for Diagrammer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QToolBar,
    QWidget,
)

from diagrammer.canvas.grid import DEFAULT_GRID_SPACING
from diagrammer.canvas.scene import DiagramScene, InteractionMode
from diagrammer.canvas.view import DiagramView
from diagrammer.models.library import ComponentLibrary
from diagrammer.panels.library_panel import LibraryPanel
from diagrammer.panels.settings_dialog import AppSettings, SettingsDialog, app_settings


def _find_builtin_components() -> Path:
    """Locate the built-in components directory shipped with the package."""
    here = Path(__file__).resolve().parent
    for ancestor in [here.parent.parent, here.parent.parent.parent]:
        candidate = ancestor / "components"
        if candidate.is_dir():
            return candidate
    return here / "components"


class MainWindow(QMainWindow):
    """Top-level window containing the diagram canvas, menus, and panels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Diagrammer")
        self.resize(1200, 800)

        # -- Component library --
        self._library = ComponentLibrary()
        builtin_path = _find_builtin_components()
        if builtin_path.is_dir():
            self._library.scan(builtin_path)

        # -- Scene and View --
        self._scene = DiagramScene(library=self._library, parent=self)
        self._view = DiagramView(self._scene, self)
        self.setCentralWidget(self._view)

        # -- Clipboard for copy/paste --
        self._clipboard: list = []

        # -- Current file path --
        self._current_file: str | None = None

        # -- Status bar --
        self._pos_label = QLabel("X: 0.0  Y: 0.0")
        self._mode_label = QLabel("Mode: Select")
        self.statusBar().addWidget(self._pos_label)
        self.statusBar().addPermanentWidget(self._mode_label)

        self._scene.cursor_scene_pos_changed.connect(self._update_pos_label)
        self._scene.mode_changed.connect(self._update_mode_label)

        # -- Menus --
        self._create_menus()

        # -- Toolbar --
        self._create_toolbar()

        # -- Panels --
        self._create_panels()

        # -- Restore state from settings --
        self._restore_from_settings()

    # ------------------------------------------------------------------ Menus

    def _create_menus(self) -> None:
        menu_bar = self.menuBar()

        # ---- File ----
        file_menu = menu_bar.addMenu("&File")

        new_act = QAction("&New", self)
        new_act.setShortcut(QKeySequence.StandardKey.New)
        new_act.triggered.connect(self._file_new)
        file_menu.addAction(new_act)

        open_act = QAction("&Open...", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self._file_open)
        file_menu.addAction(open_act)

        save_act = QAction("&Save", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        save_act.triggered.connect(self._file_save)
        file_menu.addAction(save_act)

        save_as_act = QAction("Save &As...", self)
        save_as_act.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_act.triggered.connect(self._file_save_as)
        file_menu.addAction(save_as_act)

        file_menu.addSeparator()

        export_svg_act = QAction("Export as SV&G...", self)
        export_svg_act.triggered.connect(self._export_svg)
        file_menu.addAction(export_svg_act)

        export_png_act = QAction("Export as &PNG...", self)
        export_png_act.triggered.connect(self._export_png)
        file_menu.addAction(export_png_act)

        export_pdf_act = QAction("Export as P&DF...", self)
        export_pdf_act.triggered.connect(self._export_pdf)
        file_menu.addAction(export_pdf_act)

        file_menu.addSeparator()

        restore_act = QAction("&Restore Previous Session", self)
        restore_act.triggered.connect(self._restore_session)
        file_menu.addAction(restore_act)

        file_menu.addSeparator()

        quit_act = QAction("&Quit", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # ---- Edit ----
        edit_menu = menu_bar.addMenu("&Edit")

        undo_act = self._scene.undo_stack.createUndoAction(self, "&Undo")
        undo_act.setShortcut(QKeySequence.StandardKey.Undo)
        edit_menu.addAction(undo_act)

        redo_act = self._scene.undo_stack.createRedoAction(self, "&Redo")
        redo_act.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(redo_act)

        edit_menu.addSeparator()

        cut_act = QAction("Cu&t", self)
        cut_act.setShortcut(QKeySequence.StandardKey.Cut)
        cut_act.triggered.connect(self._cut)
        edit_menu.addAction(cut_act)

        copy_act = QAction("&Copy", self)
        copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        copy_act.triggered.connect(self._copy)
        edit_menu.addAction(copy_act)

        paste_act = QAction("&Paste", self)
        paste_act.setShortcut(QKeySequence.StandardKey.Paste)
        paste_act.triggered.connect(self._paste)
        edit_menu.addAction(paste_act)

        edit_menu.addSeparator()

        select_all_act = QAction("Select &All", self)
        select_all_act.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all_act.triggered.connect(self._select_all)
        edit_menu.addAction(select_all_act)

        delete_act = QAction("&Delete", self)
        delete_act.setShortcuts([
            QKeySequence.StandardKey.Delete,
            QKeySequence(Qt.Key.Key_Backspace),
        ])
        delete_act.triggered.connect(self._delete_selected)
        edit_menu.addAction(delete_act)

        edit_menu.addSeparator()

        # -- Rotation (90°, center) --
        rotate_ccw_act = QAction("Rotate CCW (&90\u00b0)", self)
        rotate_ccw_act.setShortcut(QKeySequence(Qt.Key.Key_Space))
        rotate_ccw_act.triggered.connect(lambda: self._rotate_selected(90))
        edit_menu.addAction(rotate_ccw_act)

        rotate_cw_act = QAction("Rotate CW (9&0\u00b0)", self)
        rotate_cw_act.setShortcut(QKeySequence(Qt.Modifier.SHIFT | Qt.Key.Key_Space))
        rotate_cw_act.triggered.connect(lambda: self._rotate_selected(-90))
        edit_menu.addAction(rotate_cw_act)

        # -- Fine rotation (15°, around first port) --
        fine_cw_act = QAction("Fine Rotate C&W (15\u00b0)", self)
        fine_cw_act.setShortcut(QKeySequence(Qt.Key.Key_R))
        fine_cw_act.triggered.connect(lambda: self._fine_rotate_selected(-15))
        edit_menu.addAction(fine_cw_act)

        fine_ccw_act = QAction("Fine Rotate CC&W (15\u00b0)", self)
        fine_ccw_act.setShortcut(QKeySequence(Qt.Modifier.SHIFT | Qt.Key.Key_R))
        fine_ccw_act.triggered.connect(lambda: self._fine_rotate_selected(15))
        edit_menu.addAction(fine_ccw_act)

        # -- Flip --
        flip_h_act = QAction("Flip &Horizontal", self)
        flip_h_act.setShortcut(QKeySequence(Qt.Key.Key_F))
        flip_h_act.triggered.connect(lambda: self._flip_selected(horizontal=True))
        edit_menu.addAction(flip_h_act)

        flip_v_act = QAction("Flip &Vertical", self)
        flip_v_act.setShortcut(QKeySequence(Qt.Modifier.SHIFT | Qt.Key.Key_F))
        flip_v_act.triggered.connect(lambda: self._flip_selected(horizontal=False))
        edit_menu.addAction(flip_v_act)

        edit_menu.addSeparator()

        edit_menu.addSeparator()

        # -- Alignment --
        align_h_act = QAction("Align Hori&zontally", self)
        align_h_act.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Modifier.SHIFT | Qt.Key.Key_H))
        align_h_act.triggered.connect(lambda: self._align_selected("horizontal"))
        edit_menu.addAction(align_h_act)

        align_v_act = QAction("Align Vert&ically", self)
        align_v_act.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Modifier.SHIFT | Qt.Key.Key_V))
        align_v_act.triggered.connect(lambda: self._align_selected("vertical"))
        edit_menu.addAction(align_v_act)

        edit_menu.addSeparator()

        hide_layer_act = QAction("&Hide Active Layer", self)
        hide_layer_act.setShortcut(QKeySequence(Qt.Key.Key_H))
        hide_layer_act.triggered.connect(self._hide_active_layer)
        edit_menu.addAction(hide_layer_act)

        show_layer_act = QAction("S&how Active Layer", self)
        show_layer_act.setShortcut(QKeySequence(Qt.Modifier.SHIFT | Qt.Key.Key_H))
        show_layer_act.triggered.connect(self._show_active_layer)
        edit_menu.addAction(show_layer_act)

        lock_layer_act = QAction("&Lock Active Layer", self)
        lock_layer_act.setShortcut(QKeySequence(Qt.Key.Key_L))
        lock_layer_act.triggered.connect(self._lock_active_layer)
        edit_menu.addAction(lock_layer_act)

        unlock_layer_act = QAction("&Unlock Active Layer", self)
        unlock_layer_act.setShortcut(QKeySequence(Qt.Modifier.SHIFT | Qt.Key.Key_L))
        unlock_layer_act.triggered.connect(self._unlock_active_layer)
        edit_menu.addAction(unlock_layer_act)

        edit_menu.addSeparator()

        edit_menu.addSeparator()

        group_act = QAction("&Group", self)
        group_act.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_G))
        group_act.triggered.connect(self._group_selected)
        edit_menu.addAction(group_act)

        ungroup_act = QAction("U&ngroup", self)
        ungroup_act.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Modifier.SHIFT | Qt.Key.Key_G))
        ungroup_act.triggered.connect(self._ungroup_selected)
        edit_menu.addAction(ungroup_act)

        edit_menu.addSeparator()

        settings_act = QAction("Se&ttings\u2026", self)
        settings_act.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_Comma))
        settings_act.triggered.connect(self._open_settings)
        edit_menu.addAction(settings_act)

        # ---- View ----
        view_menu = menu_bar.addMenu("&View")

        zoom_in_act = QAction("Zoom &In", self)
        zoom_in_act.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in_act.triggered.connect(lambda: self._view.zoom_centered(1.25))
        view_menu.addAction(zoom_in_act)

        zoom_out_act = QAction("Zoom &Out", self)
        zoom_out_act.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out_act.triggered.connect(lambda: self._view.zoom_centered(0.8))
        view_menu.addAction(zoom_out_act)

        fit_act = QAction("Zoom &All / Fit", self)
        fit_act.setShortcuts([
            QKeySequence(Qt.Key.Key_A),
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_0),
        ])
        fit_act.triggered.connect(self._view.fit_all)
        view_menu.addAction(fit_act)

        view_menu.addSeparator()

        toggle_grid_act = QAction("Show &Grid", self)
        toggle_grid_act.setCheckable(True)
        toggle_grid_act.setChecked(True)
        toggle_grid_act.toggled.connect(self._toggle_grid)
        view_menu.addAction(toggle_grid_act)

        view_menu.addSeparator()

        self._zoom_window_act = QAction("&Zoom Window", self)
        self._zoom_window_act.setCheckable(True)
        self._zoom_window_act.setShortcut(QKeySequence(Qt.Key.Key_Z))
        self._zoom_window_act.toggled.connect(self._toggle_zoom_window)
        view_menu.addAction(self._zoom_window_act)

        # ---- Routing ----
        routing_menu = menu_bar.addMenu("&Routing")

        self._trace_mode_act = QAction("&Trace Routing Mode", self)
        self._trace_mode_act.setCheckable(True)
        self._trace_mode_act.setShortcut(QKeySequence(Qt.Key.Key_T))
        self._trace_mode_act.toggled.connect(self._toggle_trace_mode)
        routing_menu.addAction(self._trace_mode_act)

        routing_menu.addSeparator()

        self._snap_grid_act = QAction("Snap to &Grid", self)
        self._snap_grid_act.setCheckable(True)
        self._snap_grid_act.setChecked(True)
        self._snap_grid_act.toggled.connect(self._toggle_snap)
        routing_menu.addAction(self._snap_grid_act)

        self._snap_port_act = QAction("Snap to &Port", self)
        self._snap_port_act.setCheckable(True)
        self._snap_port_act.setChecked(app_settings.snap_to_port)
        self._snap_port_act.toggled.connect(self._toggle_snap_to_port)
        routing_menu.addAction(self._snap_port_act)

        self._snap_angle_act = QAction("Snap to &Angle", self)
        self._snap_angle_act.setCheckable(True)
        self._snap_angle_act.setChecked(app_settings.snap_to_angle)
        self._snap_angle_act.toggled.connect(self._toggle_snap_to_angle)
        routing_menu.addAction(self._snap_angle_act)

        routing_menu.addSeparator()

        self._discrete_angle_act = QAction("&Discrete Angle Routing", self)
        self._discrete_angle_act.setCheckable(True)
        self._discrete_angle_act.setChecked(False)
        self._discrete_angle_act.toggled.connect(self._toggle_discrete_angle_routing)
        routing_menu.addAction(self._discrete_angle_act)

        routing_menu.addSeparator()

        self._show_junctions_act = QAction("Show &Junctions", self)
        self._show_junctions_act.setCheckable(True)
        self._show_junctions_act.setChecked(app_settings.show_junctions)
        self._show_junctions_act.toggled.connect(self._toggle_show_junctions)
        routing_menu.addAction(self._show_junctions_act)

        routing_menu.addSeparator()

        hv_info_act = QAction("Hold Shift for &H/V Constraint", self)
        hv_info_act.setEnabled(False)
        routing_menu.addAction(hv_info_act)

        # ---- Draw ----
        draw_menu = menu_bar.addMenu("&Draw")

        draw_rect_act = QAction("&Rectangle", self)
        draw_rect_act.triggered.connect(lambda: self._add_shape("rectangle"))
        draw_menu.addAction(draw_rect_act)

        draw_ellipse_act = QAction("&Ellipse", self)
        draw_ellipse_act.triggered.connect(lambda: self._add_shape("ellipse"))
        draw_menu.addAction(draw_ellipse_act)

        draw_line_act = QAction("&Line", self)
        draw_line_act.triggered.connect(lambda: self._add_shape("line"))
        draw_menu.addAction(draw_line_act)

        # ---- Help ----
        help_menu = menu_bar.addMenu("&Help")

        help_act = QAction("&Help", self)
        help_act.setShortcut(QKeySequence.StandardKey.HelpContents)
        help_act.triggered.connect(self._show_help)
        help_menu.addAction(help_act)

    # ---------------------------------------------------------------- Toolbar

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Grid", self)
        toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        toolbar.addWidget(QLabel("  Grid: "))

        self._grid_spin = QDoubleSpinBox()
        self._grid_spin.setRange(2.0, 200.0)
        self._grid_spin.setValue(DEFAULT_GRID_SPACING)
        self._grid_spin.setSuffix(" pt")
        self._grid_spin.setDecimals(1)
        self._grid_spin.setSingleStep(1.0)
        self._grid_spin.setFixedWidth(90)
        self._grid_spin.valueChanged.connect(self._on_grid_spacing_changed)
        toolbar.addWidget(self._grid_spin)

        reset_btn = QPushButton("Reset")
        reset_btn.setFixedWidth(50)
        reset_btn.clicked.connect(self._reset_grid_spacing)
        toolbar.addWidget(reset_btn)

    # ----------------------------------------------------------------- Panels

    def _create_panels(self) -> None:
        self._library_panel = LibraryPanel(self._library, self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._library_panel)

        from diagrammer.panels.properties_panel import PropertiesPanel
        self._props_panel = PropertiesPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._props_panel)
        self._scene.selectionChanged.connect(self._on_selection_changed)

        from diagrammer.panels.layers_panel import LayersPanel
        self._layers_panel = LayersPanel(self._scene.layer_manager, self)
        self._layers_panel.layers_changed.connect(self._on_layers_changed)
        self._layers_panel.active_layer_switched.connect(self._on_layer_switched)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._layers_panel)
        self.tabifyDockWidget(self._props_panel, self._layers_panel)

    # ------------------------------------------------------------------ Slots

    def _update_pos_label(self, x: float, y: float) -> None:
        snapped = self._view.snap(QPointF(x, y))
        self._pos_label.setText(
            f"X: {x:.1f}  Y: {y:.1f}  (grid: {snapped.x():.0f}, {snapped.y():.0f})"
        )

    def _update_mode_label(self, mode: InteractionMode) -> None:
        if self._view.zoom_window_mode:
            self._mode_label.setText("Mode: Zoom Window")
        else:
            self._mode_label.setText(f"Mode: {mode.name.capitalize()}")
        # Sync zoom window checkbox
        if not self._view.zoom_window_mode and self._zoom_window_act.isChecked():
            self._zoom_window_act.blockSignals(True)
            self._zoom_window_act.setChecked(False)
            self._zoom_window_act.blockSignals(False)

    def _toggle_grid(self, visible: bool) -> None:
        self._view.grid_visible = visible

    def _toggle_snap(self, enabled: bool) -> None:
        self._view.snap_enabled = enabled
        app_settings.snap_to_grid = enabled
        app_settings.save()
        if not enabled and app_settings.snap_to_angle:
            self._view._angle_snap = True
            self._view._angle_snap_increment = app_settings.angle_snap_increment
        else:
            self._view._angle_snap = False

    def _on_grid_spacing_changed(self, value: float) -> None:
        self._view.grid_spacing = value

    def _reset_grid_spacing(self) -> None:
        self._grid_spin.setValue(DEFAULT_GRID_SPACING)
        self._view.grid_spacing = DEFAULT_GRID_SPACING

    def _open_settings(self) -> None:
        dlg = SettingsDialog(app_settings, self, library=self._library)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            dlg.apply()
            self._apply_settings()
            self._apply_library_visibility()

    def _apply_settings(self) -> None:
        """Push current app_settings into the scene/view."""
        # Update snap-to-angle based on grid snap state
        if not self._view.snap_enabled and app_settings.snap_to_angle:
            self._view._angle_snap = True
            self._view._angle_snap_increment = app_settings.angle_snap_increment
        else:
            self._view._angle_snap = False

    def _apply_library_visibility(self) -> None:
        """Rebuild library panel with only visible categories."""
        from diagrammer.models.library import ComponentLibrary
        visible = ComponentLibrary()
        for cat, defs in self._library.categories.items():
            if cat not in app_settings.hidden_libraries:
                visible._categories[cat] = defs
                for d in defs:
                    visible._by_key[f"{cat}/{d.name}"] = d
        self._library_panel._tree.populate(visible, self._library_panel._favorites, self._library_panel._recents)
        self._library_panel._grid.populate(visible)

    def _group_selected(self) -> None:
        """Select all connected items as a group for moving together.

        Note: We don't use QGraphicsItemGroup because it changes the
        coordinate hierarchy and breaks port-based connections.
        Instead, multi-select + drag already moves items together
        with proper waypoint shifting.
        """
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem

        selected_comps = [i for i in self._scene.selectedItems() if isinstance(i, ComponentItem)]
        if not selected_comps:
            return

        # Also select all connections between the selected components
        comp_ids = set(id(c) for c in selected_comps)
        for item in self._scene.items():
            if isinstance(item, ConnectionItem):
                if (id(item.source_port.component) in comp_ids and
                        id(item.target_port.component) in comp_ids):
                    item.setSelected(True)

    def _ungroup_selected(self) -> None:
        """Deselect all — effectively 'ungroups' the visual selection."""
        self._scene.clearSelection()

    def _on_selection_changed(self) -> None:
        self._props_panel.update_for_selection(self._scene)

    def _on_layers_changed(self) -> None:
        """Apply layer visibility/lock state to all scene items."""
        self._scene.apply_layer_state()

    def _on_layer_switched(self, layer_index: int) -> None:
        """Flash items on the newly selected layer."""
        self._scene.flash_layer(layer_index)

    def _hide_active_layer(self) -> None:
        lm = self._scene.layer_manager
        lm.active_layer.visible = False
        self._layers_panel.refresh()
        self._scene.apply_layer_state()

    def _show_active_layer(self) -> None:
        lm = self._scene.layer_manager
        lm.active_layer.visible = True
        self._layers_panel.refresh()
        self._scene.apply_layer_state()

    def _lock_active_layer(self) -> None:
        lm = self._scene.layer_manager
        lm.active_layer.locked = True
        self._layers_panel.refresh()
        self._scene.apply_layer_state()

    def _unlock_active_layer(self) -> None:
        lm = self._scene.layer_manager
        lm.active_layer.locked = False
        self._layers_panel.refresh()
        self._scene.apply_layer_state()

    def _toggle_zoom_window(self, enabled: bool) -> None:
        self._view.zoom_window_mode = enabled
        if enabled:
            self._mode_label.setText("Mode: Zoom Window")
        else:
            self._mode_label.setText(f"Mode: {self._scene.mode.name.capitalize()}")

    def _toggle_trace_mode(self, enabled: bool) -> None:
        if enabled:
            self._scene.mode = InteractionMode.CONNECT
            self._view.trace_routing = True
            self._view.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._scene.mode = InteractionMode.SELECT
            self._view.trace_routing = False
            self._view.setCursor(Qt.CursorShape.ArrowCursor)

    def _toggle_snap_to_port(self, enabled: bool) -> None:
        app_settings.snap_to_port = enabled
        app_settings.save()

    def _toggle_snap_to_angle(self, enabled: bool) -> None:
        app_settings.snap_to_angle = enabled
        app_settings.save()
        if not self._view.snap_enabled and enabled:
            self._view._angle_snap = True
            self._view._angle_snap_increment = app_settings.angle_snap_increment
        elif not enabled:
            self._view._angle_snap = False

    def _toggle_discrete_angle_routing(self, enabled: bool) -> None:
        from diagrammer.items.connection_item import ROUTE_ORTHO, ROUTE_ORTHO_45
        self._scene.default_routing_mode = ROUTE_ORTHO_45 if enabled else ROUTE_ORTHO
        app_settings.discrete_angle_routing = enabled
        app_settings.save()

    def _toggle_show_junctions(self, enabled: bool) -> None:
        app_settings.show_junctions = enabled
        app_settings.save()
        # Update visibility of all existing junctions
        from diagrammer.items.junction_item import JunctionItem
        for item in self._scene.items():
            if isinstance(item, JunctionItem):
                item.setVisible(enabled)

    def _restore_from_settings(self) -> None:
        """Restore UI state from persisted settings on startup."""
        # Snap to grid
        self._view.snap_enabled = app_settings.snap_to_grid
        self._snap_grid_act.setChecked(app_settings.snap_to_grid)

        # Snap to port
        self._snap_port_act.setChecked(app_settings.snap_to_port)

        # Snap to angle
        self._snap_angle_act.setChecked(app_settings.snap_to_angle)

        # Discrete angle routing
        self._discrete_angle_act.setChecked(app_settings.discrete_angle_routing)

        # Show junctions
        self._show_junctions_act.setChecked(app_settings.show_junctions)

        # Library view mode
        self._library_panel._set_view_mode(app_settings.library_view_mode)
        if app_settings.library_view_mode == "grid":
            self._library_panel._grid_btn.setChecked(True)
            self._library_panel._tree_btn.setChecked(False)

        # Library visibility
        self._apply_library_visibility()

    # ---------------------------------------------------- Delete / Transform

    def _select_all(self) -> None:
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import LineItem, ShapeItem
        for item in self._scene.items():
            if isinstance(item, (ComponentItem, ConnectionItem, JunctionItem, ShapeItem, LineItem)):
                if item.flags() & item.GraphicsItemFlag.ItemIsSelectable:
                    item.setSelected(True)

    def _delete_selected(self) -> None:
        from diagrammer.commands.delete_command import DeleteCommand
        selected = self._scene.selectedItems()
        if selected:
            cmd = DeleteCommand(self._scene, selected)
            self._scene.undo_stack.push(cmd)

    def _gather_connected_junctions(self, selected_items) -> list:
        """Find JunctionItems connected to selected items but not explicitly selected.

        When a connection terminates on a JunctionItem (e.g. a T-junction on a wire),
        that junction must move with the group even if it wasn't explicitly selected
        (it may be invisible or too small to rubber-band).
        """
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        selected_ids = set(id(i) for i in selected_items)
        extra_juncs = []
        for item in self._scene.items():
            if not isinstance(item, ConnectionItem):
                continue
            src_comp = item.source_port.component
            tgt_comp = item.target_port.component
            # If one end is in the selection and the other is a junction NOT in
            # the selection, pull that junction into the group.
            if id(src_comp) in selected_ids and isinstance(tgt_comp, JunctionItem) and id(tgt_comp) not in selected_ids:
                extra_juncs.append(tgt_comp)
                selected_ids.add(id(tgt_comp))
            elif id(tgt_comp) in selected_ids and isinstance(src_comp, JunctionItem) and id(src_comp) not in selected_ids:
                extra_juncs.append(src_comp)
                selected_ids.add(id(src_comp))
        return extra_juncs

    def _rotate_selected(self, degrees: float) -> None:
        import math
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
        from diagrammer.commands.transform_command import RotateComponentCommand
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        comp_targets = [i for i in self._scene.selectedItems() if isinstance(i, ComponentItem)]
        junc_targets = [i for i in self._scene.selectedItems() if isinstance(i, JunctionItem)]
        if not comp_targets and not junc_targets:
            return

        # Auto-include connected junctions that aren't in the selection
        all_selected = comp_targets + junc_targets
        extra_juncs = self._gather_connected_junctions(all_selected)
        junc_targets.extend(extra_juncs)
        all_targets = comp_targets + junc_targets
        self._scene.undo_stack.beginMacro(f"Rotate {len(all_targets)} items")

        if len(all_targets) == 1 and len(comp_targets) == 1:
            # Single component: rotate around its own center
            cmd = RotateComponentCommand(comp_targets[0], degrees)
            self._scene.undo_stack.push(cmd)
        else:
            # Group rotation: rigid-body — rotate each component internally
            # AND orbit all item positions around the group center.
            # Compute scene centers for all items
            scene_centers = []
            for item in comp_targets:
                scene_centers.append(item.mapToScene(QPointF(item._def.width / 2, item._def.height / 2)))
            for item in junc_targets:
                scene_centers.append(item.mapToScene(QPointF(0, 0)))

            gcx = sum(p.x() for p in scene_centers) / len(scene_centers)
            gcy = sum(p.y() for p in scene_centers) / len(scene_centers)
            rad = math.radians(degrees)
            cos_a, sin_a = math.cos(rad), math.sin(rad)

            # Pre-compute orbited target positions
            target_centers = []
            for sc in scene_centers:
                dx, dy = sc.x() - gcx, sc.y() - gcy
                target_centers.append(QPointF(
                    gcx + dx * cos_a - dy * sin_a,
                    gcy + dx * sin_a + dy * cos_a,
                ))

            # Rotate and orbit components
            idx = 0
            for item in comp_targets:
                cmd = RotateComponentCommand(item, degrees)
                self._scene.undo_stack.push(cmd)
                cur_sc = item.mapToScene(QPointF(item._def.width / 2, item._def.height / 2))
                offset = target_centers[idx] - cur_sc
                new_pos = item.pos() + offset
                item._skip_snap = True
                move_cmd = MoveComponentCommand(item, item.pos(), new_pos)
                self._scene.undo_stack.push(move_cmd)
                item._skip_snap = False
                idx += 1

            # Orbit junctions (no internal rotation needed — they're dots)
            for item in junc_targets:
                cur_sc = item.mapToScene(QPointF(0, 0))
                offset = target_centers[idx] - cur_sc
                new_pos = item.pos() + offset
                item._skip_snap = True
                move_cmd = MoveComponentCommand(item, item.pos(), new_pos)
                self._scene.undo_stack.push(move_cmd)
                item._skip_snap = False
                idx += 1

            # Rotate internal connection waypoints around group center.
            # Only rotate USER waypoints — don't freeze expanded routes.
            item_ids = set(id(c) for c in all_targets)
            for item in self._scene.items():
                if not isinstance(item, ConnectionItem):
                    continue
                if (id(item.source_port.component) in item_ids and
                        id(item.target_port.component) in item_ids):
                    old_wps = [QPointF(w) for w in item.vertices]
                    if old_wps:
                        new_wps = []
                        for wp in old_wps:
                            dx, dy = wp.x() - gcx, wp.y() - gcy
                            new_wps.append(QPointF(
                                gcx + dx * cos_a - dy * sin_a,
                                gcy + dx * sin_a + dy * cos_a,
                            ))
                        cmd = EditWaypointsCommand(item, old_wps, new_wps)
                        self._scene.undo_stack.push(cmd)

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    def _fine_rotate_selected(self, degrees: float) -> None:
        """Fine-rotate selected components around the pivot port (or first port)."""
        import math
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
        from diagrammer.commands.transform_command import RotateAroundPortCommand
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        pivot = self._scene.rotation_pivot_port
        comp_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, ComponentItem) and i.ports]
        junc_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, JunctionItem)]
        if not comp_targets and not junc_targets:
            return
        # Auto-include connected junctions
        all_selected = comp_targets + junc_targets
        extra_juncs = self._gather_connected_junctions(all_selected)
        junc_targets.extend(extra_juncs)
        all_targets = comp_targets + junc_targets
        self._scene.undo_stack.beginMacro(f"Fine rotate {len(all_targets)} items")

        # Determine the pivot point in scene coords
        if pivot is not None:
            pivot_scene = pivot.scene_center()
        elif len(comp_targets) == 1:
            # Single component: pivot around its first port (junctions orbit around it)
            pivot_scene = comp_targets[0].ports[0].scene_center()
        else:
            # Group: use the center of all items
            centers = []
            for c in comp_targets:
                centers.append(QPointF(c.pos().x() + c._def.width / 2,
                                       c.pos().y() + c._def.height / 2))
            for j in junc_targets:
                centers.append(j.mapToScene(QPointF(0, 0)))
            pivot_scene = QPointF(
                sum(p.x() for p in centers) / len(centers),
                sum(p.y() for p in centers) / len(centers),
            )

        for item in comp_targets:
            if pivot is not None and pivot.component is item:
                port = pivot
            else:
                port = item.ports[0]
            cmd = RotateAroundPortCommand(item, port, degrees)
            self._scene.undo_stack.push(cmd)

        # Orbit junctions around pivot
        rad = math.radians(degrees)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        px, py = pivot_scene.x(), pivot_scene.y()
        for junc in junc_targets:
            sc = junc.mapToScene(QPointF(0, 0))
            dx, dy = sc.x() - px, sc.y() - py
            new_sc = QPointF(px + dx * cos_a - dy * sin_a,
                             py + dx * sin_a + dy * cos_a)
            offset = new_sc - sc
            new_pos = junc.pos() + offset
            junc._skip_snap = True
            move_cmd = MoveComponentCommand(junc, junc.pos(), new_pos)
            self._scene.undo_stack.push(move_cmd)
            junc._skip_snap = False

        # Rotate internal connection waypoints around the pivot
        if len(all_targets) > 1:
            item_ids = set(id(c) for c in all_targets)
            for item in self._scene.items():
                if not isinstance(item, ConnectionItem):
                    continue
                if (id(item.source_port.component) in item_ids and
                        id(item.target_port.component) in item_ids):
                    old_wps = [QPointF(w) for w in item.vertices]
                    if old_wps:
                        new_wps = [QPointF(px + (w.x()-px)*cos_a - (w.y()-py)*sin_a,
                                           py + (w.x()-px)*sin_a + (w.y()-py)*cos_a)
                                   for w in old_wps]
                        cmd = EditWaypointsCommand(item, old_wps, new_wps)
                        self._scene.undo_stack.push(cmd)

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    def _flip_selected(self, horizontal: bool) -> None:
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.transform_command import FlipComponentCommand
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        selected_comps = [
            i for i in self._scene.selectedItems()
            if isinstance(i, ComponentItem)
        ]
        selected_juncs = [
            i for i in self._scene.selectedItems()
            if isinstance(i, JunctionItem)
        ]
        # Auto-include connected junctions that aren't in the selection
        all_selected = selected_comps + selected_juncs
        extra_juncs = self._gather_connected_junctions(all_selected)
        selected_juncs.extend(extra_juncs)
        all_selected = selected_comps + selected_juncs

        axis = "H" if horizontal else "V"
        self._scene.undo_stack.beginMacro(f"Flip {axis} {len(all_selected)} items")

        if len(all_selected) <= 1:
            for item in selected_comps:
                cmd = FlipComponentCommand(item, horizontal)
                self._scene.undo_stack.push(cmd)
            self._scene.undo_stack.endMacro()
            self._scene.update_connections()
            return

        # Multi-item: flip each component AND mirror all positions around group center
        centers = []
        for c in selected_comps:
            centers.append(QPointF(c.pos().x() + c._width / 2, c.pos().y() + c._height / 2))
        for j in selected_juncs:
            centers.append(j.mapToScene(QPointF(0, 0)))
        group_cx = sum(p.x() for p in centers) / len(centers)
        group_cy = sum(p.y() for p in centers) / len(centers)

        item_set = set(id(c) for c in all_selected)

        for comp in selected_comps:
            cmd = FlipComponentCommand(comp, horizontal)
            self._scene.undo_stack.push(cmd)

            old_pos = comp.pos()
            if horizontal:
                new_x = 2 * group_cx - old_pos.x() - comp._width
                new_pos = QPointF(new_x, old_pos.y())
            else:
                new_y = 2 * group_cy - old_pos.y() - comp._height
                new_pos = QPointF(old_pos.x(), new_y)

            comp._skip_snap = True
            move_cmd = MoveComponentCommand(comp, old_pos, new_pos)
            self._scene.undo_stack.push(move_cmd)
            comp._skip_snap = False

        # Mirror junction positions (no internal flip needed)
        for junc in selected_juncs:
            old_pos = junc.pos()
            sc = junc.mapToScene(QPointF(0, 0))
            if horizontal:
                new_sc_x = 2 * group_cx - sc.x()
                new_pos = QPointF(old_pos.x() + (new_sc_x - sc.x()), old_pos.y())
            else:
                new_sc_y = 2 * group_cy - sc.y()
                new_pos = QPointF(old_pos.x(), old_pos.y() + (new_sc_y - sc.y()))
            junc._skip_snap = True
            move_cmd = MoveComponentCommand(junc, old_pos, new_pos)
            self._scene.undo_stack.push(move_cmd)
            junc._skip_snap = False

        # Mirror connection waypoints for connections between selected items
        from diagrammer.commands.connect_command import EditWaypointsCommand
        for item in self._scene.items():
            if not isinstance(item, ConnectionItem):
                continue
            if (id(item.source_port.component) in item_set and
                    id(item.target_port.component) in item_set):
                old_wps = [QPointF(w) for w in item.vertices]
                if old_wps:
                    new_wps = []
                    for wp in old_wps:
                        if horizontal:
                            new_wps.append(QPointF(2 * group_cx - wp.x(), wp.y()))
                        else:
                            new_wps.append(QPointF(wp.x(), 2 * group_cy - wp.y()))
                    cmd = EditWaypointsCommand(item, old_wps, new_wps)
                    self._scene.undo_stack.push(cmd)

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    def _align_selected(self, direction: str) -> None:
        """Align components by ports or centers.

        Priority:
        1. If ports have been Ctrl+click selected (>=2), align by those ports.
        2. Otherwise, align selected components by their centers.
        """
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.items.component_item import ComponentItem

        alignment_ports = self._scene.alignment_ports

        if len(alignment_ports) >= 2:
            # Align by explicitly selected ports
            anchors: list[tuple[ComponentItem, QPointF]] = [
                (port.component, port.scene_center())
                for port in alignment_ports
            ]
        else:
            # Align selected components by their centers
            selected = [
                item for item in self._scene.selectedItems()
                if isinstance(item, ComponentItem)
            ]
            if len(selected) < 2:
                return
            anchors = []
            for comp in selected:
                center = comp.mapToScene(
                    QPointF(comp._width / 2, comp._height / 2)
                )
                anchors.append((comp, center))

        if len(anchors) < 2:
            return

        self._scene.undo_stack.beginMacro(f"Align {direction} {len(anchors)} items")

        if direction == "horizontal":
            avg_y = sum(pos.y() for _, pos in anchors) / len(anchors)
            for comp, anchor_pos in anchors:
                dy = avg_y - anchor_pos.y()
                if abs(dy) > 0.5:
                    old_pos = comp.pos()
                    new_pos = QPointF(old_pos.x(), old_pos.y() + dy)
                    # Bypass snap so the port lands at the exact target
                    comp._skip_snap = True
                    cmd = MoveComponentCommand(comp, old_pos, new_pos)
                    self._scene.undo_stack.push(cmd)
                    comp._skip_snap = False
        elif direction == "vertical":
            avg_x = sum(pos.x() for _, pos in anchors) / len(anchors)
            for comp, anchor_pos in anchors:
                dx = avg_x - anchor_pos.x()
                if abs(dx) > 0.5:
                    old_pos = comp.pos()
                    new_pos = QPointF(old_pos.x() + dx, old_pos.y())
                    comp._skip_snap = True
                    cmd = MoveComponentCommand(comp, old_pos, new_pos)
                    self._scene.undo_stack.push(cmd)
                    comp._skip_snap = False

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()
        self._scene.clear_alignment_ports()

    # ------------------------------------------------------- Cut / Copy / Paste

    def _copy(self) -> None:
        """Copy selected components, junctions, and connections between them to clipboard."""
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        selected_comps = [
            item for item in self._scene.selectedItems()
            if isinstance(item, ComponentItem)
        ]
        selected_juncs = [
            item for item in self._scene.selectedItems()
            if isinstance(item, JunctionItem)
        ]
        if not selected_comps and not selected_juncs:
            return

        # Use a combined set for internal connection detection
        all_selected = selected_comps + selected_juncs
        item_set = set(id(c) for c in all_selected)

        # Collect connections where both endpoints are in the selection
        selected_conns = []
        for item in self._scene.items():
            if isinstance(item, ConnectionItem):
                if (id(item.source_port.component) in item_set and
                        id(item.target_port.component) in item_set):
                    selected_conns.append(item)

        # Serialize to clipboard: store component defs, positions, transforms, and connection topology
        # Components and junctions share a single index space for connection references
        self._clipboard = []
        id_map: dict[str, int] = {}  # instance_id -> clipboard index
        idx = 0
        for comp in selected_comps:
            id_map[comp.instance_id] = idx
            self._clipboard.append({
                "type": "component",
                "def_key": comp.library_key,
                "pos": (comp.pos().x(), comp.pos().y()),
                "rotation": comp.rotation_angle,
                "flip_h": comp.flip_h,
                "flip_v": comp.flip_v,
                "stretch_dx": comp.stretch_dx,
                "stretch_dy": comp.stretch_dy,
            })
            idx += 1
        for junc in selected_juncs:
            id_map[junc.instance_id] = idx
            self._clipboard.append({
                "type": "junction",
                "pos": (junc.pos().x(), junc.pos().y()),
            })
            idx += 1
        for conn in selected_conns:
            src_idx = id_map.get(conn.source_port.component.instance_id)
            tgt_idx = id_map.get(conn.target_port.component.instance_id)
            if src_idx is not None and tgt_idx is not None:
                self._clipboard.append({
                    "type": "connection",
                    "src_comp": src_idx,
                    "src_port": conn.source_port.port_name,
                    "tgt_comp": tgt_idx,
                    "tgt_port": conn.target_port.port_name,
                    "vertices": [(v.x(), v.y()) for v in conn.vertices],
                    "line_width": conn.line_width,
                    "line_color": conn.line_color.name(),
                    "corner_radius": conn.corner_radius,
                    "routing_mode": conn.routing_mode,
                })

    def _cut(self) -> None:
        """Copy selected items, then delete them."""
        self._copy()
        self._delete_selected()

    def _paste(self) -> None:
        """Paste clipboard contents, offset from original positions."""
        if not self._clipboard:
            return

        self._scene.undo_stack.beginMacro("Paste")
        from diagrammer.commands.add_command import AddComponentCommand
        from diagrammer.commands.connect_command import CreateConnectionCommand
        from diagrammer.items.junction_item import JunctionItem

        PASTE_OFFSET = 40.0  # offset pasted items from originals

        # First pass: create components and junctions (they share an index space)
        new_items: list = []  # ComponentItem or JunctionItem or None
        for entry in self._clipboard:
            if entry["type"] == "component":
                comp_def = self._library.get(entry["def_key"])
                if comp_def is None:
                    new_items.append(None)
                    continue
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                cmd = AddComponentCommand(self._scene, comp_def, pos)
                self._scene.undo_stack.push(cmd)
                item = cmd.item
                if item is None:
                    new_items.append(None)
                    continue
                # Apply transforms — bypass snap during setup
                item._skip_snap = True
                if entry.get("rotation", 0):
                    item.rotate_by(entry["rotation"])
                if entry.get("flip_h"):
                    item.set_flip_h(True)
                if entry.get("flip_v"):
                    item.set_flip_v(True)
                if entry.get("stretch_dx", 0) or entry.get("stretch_dy", 0):
                    item.set_stretch(entry.get("stretch_dx", 0), entry.get("stretch_dy", 0))
                # Set exact position after all transforms
                item.setPos(pos)
                item._skip_snap = False
                new_items.append(item)
            elif entry["type"] == "junction":
                junc = JunctionItem()
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                junc._skip_snap = True
                junc.setPos(pos)
                junc._skip_snap = False
                self._scene.addItem(junc)
                new_items.append(junc)

        # Second pass: create connections
        for entry in self._clipboard:
            if entry["type"] != "connection":
                continue
            src_item = new_items[entry["src_comp"]] if entry["src_comp"] < len(new_items) else None
            tgt_item = new_items[entry["tgt_comp"]] if entry["tgt_comp"] < len(new_items) else None
            if src_item is None or tgt_item is None:
                continue
            src_port = src_item.port_by_name(entry["src_port"]) if hasattr(src_item, 'port_by_name') else None
            tgt_port = tgt_item.port_by_name(entry["tgt_port"]) if hasattr(tgt_item, 'port_by_name') else None
            # JunctionItem: single port named "center"
            if src_port is None and isinstance(src_item, JunctionItem):
                src_port = src_item.port if entry["src_port"] == "center" else None
            if tgt_port is None and isinstance(tgt_item, JunctionItem):
                tgt_port = tgt_item.port if entry["tgt_port"] == "center" else None
            if src_port and tgt_port:
                cmd = CreateConnectionCommand(self._scene, src_port, tgt_port)
                self._scene.undo_stack.push(cmd)
                conn = cmd.connection
                if conn:
                    # Restore style
                    if "line_width" in entry:
                        conn.line_width = entry["line_width"]
                    if "line_color" in entry:
                        conn.line_color = QColor(entry["line_color"])
                    if "corner_radius" in entry:
                        conn.corner_radius = entry["corner_radius"]
                    if "routing_mode" in entry:
                        conn.routing_mode = entry["routing_mode"]
                    # Restore vertices (offset)
                    if entry.get("vertices"):
                        conn.vertices = [
                            QPointF(v[0] + PASTE_OFFSET, v[1] + PASTE_OFFSET)
                            for v in entry["vertices"]
                        ]

        self._scene.undo_stack.endMacro()

        # Update all routes so approach segments and lead shortening are computed
        self._scene.update_connections()

        # Select the pasted items
        self._scene.clearSelection()
        for item in new_items:
            if item is not None:
                item.setSelected(True)

    # ----------------------------------------------------------- Shape drawing

    def _add_shape(self, shape_type: str) -> None:
        """Add a simple shape at the center of the current view."""
        from diagrammer.commands.shape_command import AddShapeCommand
        from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem

        # Place shape at the center of the visible area
        center = self._view.mapToScene(
            self._view.viewport().rect().center()
        )
        snapped = self._view.snap(center)

        if shape_type == "rectangle":
            item = RectangleItem(width=100, height=60)
        elif shape_type == "ellipse":
            item = EllipseItem(width=80, height=80)
        elif shape_type == "line":
            item = LineItem(start=QPointF(0, 0), end=QPointF(100, 0))
        else:
            return

        cmd = AddShapeCommand(self._scene, item, snapped)
        self._scene.undo_stack.push(cmd)

    # --------------------------------------------------------- File operations

    def _restore_session(self) -> None:
        """Restore the last session — tries autosave first, then last opened file."""
        from diagrammer.io.serializer import DiagramSerializer
        auto_save = Path.home() / ".diagrammer" / "_autosave.dgm"

        restore_path = None
        if auto_save.exists():
            restore_path = str(auto_save)
        elif app_settings.last_opened_file and Path(app_settings.last_opened_file).exists():
            restore_path = app_settings.last_opened_file

        if restore_path:
            self._scene.clear()
            self._scene.undo_stack.clear()
            DiagramSerializer.load(self._scene, restore_path, library=self._library)
            self._layers_panel._manager = self._scene._layer_manager
            self._layers_panel.refresh()
            self._scene.apply_layer_state()
            # If restoring from autosave, don't set _current_file (it's unsaved)
            if restore_path != str(auto_save):
                self._current_file = restore_path
            self._update_title()
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "No Previous Session",
                                   "No previous session file found.")

    def closeEvent(self, event) -> None:
        """Auto-save session state on close for Restore Previous Session."""
        try:
            from diagrammer.io.serializer import DiagramSerializer
            auto_save_path = Path.home() / ".diagrammer" / "_autosave.dgm"
            auto_save_path.parent.mkdir(parents=True, exist_ok=True)
            DiagramSerializer.save(self._scene, str(auto_save_path))
            app_settings.last_opened_file = self._current_file or ""
            app_settings.save()
        except Exception:
            pass
        super().closeEvent(event)

    def _show_help(self) -> None:
        from diagrammer.panels.help_window import HelpWindow
        HelpWindow.show_help(self)

    def _update_title(self) -> None:
        name = Path(self._current_file).name if self._current_file else "Untitled"
        self.setWindowTitle(f"{name} \u2014 Diagrammer")

    def _file_new(self) -> None:
        self._scene.clear()
        self._scene.undo_stack.clear()
        from diagrammer.panels.layers_panel import LayerManager
        self._scene._layer_manager = LayerManager()
        self._layers_panel._manager = self._scene._layer_manager
        self._layers_panel.refresh()
        self._current_file = None
        self._update_title()

    def _file_open(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Diagram", "", "Diagrammer Files (*.dgm);;All Files (*)"
        )
        if path:
            from diagrammer.io.serializer import DiagramSerializer
            self._scene.clear()
            self._scene.undo_stack.clear()
            DiagramSerializer.load(self._scene, path, library=self._library)
            self._layers_panel._manager = self._scene._layer_manager
            self._layers_panel.refresh()
            self._scene.apply_layer_state()
            self._current_file = path
            self._update_title()

    def _file_save(self) -> None:
        if self._current_file:
            from diagrammer.io.serializer import DiagramSerializer
            DiagramSerializer.save(self._scene, self._current_file)
            app_settings.last_opened_file = self._current_file
            app_settings.save()
        else:
            self._file_save_as()

    def _file_save_as(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Diagram", "", "Diagrammer Files (*.dgm);;All Files (*)"
        )
        if path:
            if not path.endswith(".dgm"):
                path += ".dgm"
            from diagrammer.io.serializer import DiagramSerializer
            DiagramSerializer.save(self._scene, path)
            self._current_file = path
            self._update_title()

    def _export_svg(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SVG", "", "SVG Files (*.svg);;All Files (*)"
        )
        if path:
            from diagrammer.io.exporter import DiagramExporter
            DiagramExporter.export_svg(self._scene, path)

    def _export_png(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", "", "PNG Files (*.png);;All Files (*)"
        )
        if path:
            from diagrammer.io.exporter import DiagramExporter
            DiagramExporter.export_png(self._scene, path)

    def _export_pdf(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", "", "PDF Files (*.pdf);;All Files (*)"
        )
        if path:
            from diagrammer.io.exporter import DiagramExporter
            DiagramExporter.export_pdf(self._scene, path)
