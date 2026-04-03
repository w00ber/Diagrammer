"""MainWindow — the top-level application window for Diagrammer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QDoubleSpinBox,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabBar,
    QTabWidget,
    QToolBar,
    QWidget,
)

from diagrammer.canvas.grid import DEFAULT_GRID_SPACING
from diagrammer.canvas.scene import DiagramScene, InteractionMode
from diagrammer.canvas.view import DiagramView
from diagrammer.models.library import ComponentLibrary
from diagrammer.panels.library_panel import LibraryPanel
from diagrammer.panels.settings_dialog import AppSettings, SettingsDialog, app_settings
from diagrammer.shortcuts import SHORTCUTS, get as get_shortcut


def _find_builtin_components() -> Path:
    """Locate the built-in components directory shipped with the package.

    Components are stored inside the package at diagrammer/components/,
    so the same path works in both development and installed modes.
    """
    here = Path(__file__).resolve().parent
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
        # Scan custom library paths from settings
        for custom_path in app_settings.custom_library_paths:
            p = Path(custom_path)
            if p.is_dir():
                self._library.scan(p)

        # -- Scene and View --
        self._scene = DiagramScene(library=self._library, parent=self)
        self._view = DiagramView(self._scene, self)

        # -- Tab widget (diagram tab + closable library table tabs) --
        self._tab_widget = QTabWidget(self)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.setMovable(False)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tab_widget.addTab(self._view, "Diagram")
        # Diagram tab is never closable
        self._tab_widget.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.RightSide, None
        )
        # Hide tab bar when only the diagram tab is shown
        self._tab_widget.tabBar().setVisible(False)
        self.setCentralWidget(self._tab_widget)

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
        new_act.setShortcut(get_shortcut("file.new"))
        new_act.triggered.connect(self._file_new)
        file_menu.addAction(new_act)

        open_act = QAction("&Open...", self)
        open_act.setShortcut(get_shortcut("file.open"))
        open_act.triggered.connect(self._file_open)
        file_menu.addAction(open_act)

        save_act = QAction("&Save", self)
        save_act.setShortcut(get_shortcut("file.save"))
        save_act.triggered.connect(self._file_save)
        file_menu.addAction(save_act)

        save_as_act = QAction("Save &As...", self)
        save_as_act.setShortcut(get_shortcut("file.save_as"))
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

        create_comp_act = QAction("Create &Component from Selection...", self)
        create_comp_act.triggered.connect(self._create_component_from_selection)
        file_menu.addAction(create_comp_act)

        file_menu.addSeparator()

        restore_act = QAction("&Restore Previous Session", self)
        restore_act.triggered.connect(self._restore_session)
        file_menu.addAction(restore_act)

        file_menu.addSeparator()

        self._recent_menu = file_menu.addMenu("Recent &Files")
        self._rebuild_recent_menu()

        file_menu.addSeparator()

        quit_act = QAction("&Quit", self)
        quit_act.setShortcut(get_shortcut("file.quit"))
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # ---- Edit ----
        edit_menu = menu_bar.addMenu("&Edit")

        undo_act = self._scene.undo_stack.createUndoAction(self, "&Undo")
        undo_act.setShortcut(get_shortcut("edit.undo"))
        edit_menu.addAction(undo_act)

        redo_act = self._scene.undo_stack.createRedoAction(self, "&Redo")
        redo_act.setShortcut(get_shortcut("edit.redo"))
        edit_menu.addAction(redo_act)

        edit_menu.addSeparator()

        cut_act = QAction("Cu&t", self)
        cut_act.setShortcut(get_shortcut("edit.cut"))
        cut_act.triggered.connect(self._cut)
        edit_menu.addAction(cut_act)

        copy_act = QAction("&Copy", self)
        copy_act.setShortcut(get_shortcut("edit.copy"))
        copy_act.triggered.connect(self._copy)
        edit_menu.addAction(copy_act)

        paste_act = QAction("&Paste", self)
        paste_act.setShortcut(get_shortcut("edit.paste"))
        paste_act.triggered.connect(self._paste)
        edit_menu.addAction(paste_act)

        copy_image_act = QAction("Copy Selection as &Image", self)
        copy_image_act.setShortcut(get_shortcut("edit.copy_as_image"))
        copy_image_act.triggered.connect(self._copy_selection_as_image)
        edit_menu.addAction(copy_image_act)

        edit_menu.addSeparator()

        select_all_act = QAction("Select &All", self)
        select_all_act.setShortcut(get_shortcut("edit.select_all"))
        select_all_act.triggered.connect(self._select_all)
        edit_menu.addAction(select_all_act)

        delete_act = QAction("&Delete", self)
        delete_act.setShortcuts([
            get_shortcut("edit.delete"),
            QKeySequence(Qt.Key.Key_Backspace),
        ])
        delete_act.triggered.connect(self._delete_selected)
        edit_menu.addAction(delete_act)

        edit_menu.addSeparator()

        # -- Rotation (90°, center) --
        rotate_ccw_act = QAction("Rotate CCW (&90\u00b0)", self)
        rotate_ccw_act.setShortcut(get_shortcut("edit.rotate_ccw"))
        rotate_ccw_act.triggered.connect(lambda: self._rotate_selected(90))
        edit_menu.addAction(rotate_ccw_act)

        rotate_cw_act = QAction("Rotate CW (9&0\u00b0)", self)
        rotate_cw_act.setShortcut(get_shortcut("edit.rotate_cw"))
        rotate_cw_act.triggered.connect(lambda: self._rotate_selected(-90))
        edit_menu.addAction(rotate_cw_act)

        # -- Fine rotation (15°, around first port) --
        fine_cw_act = QAction("Fine Rotate C&W (15\u00b0)", self)
        fine_cw_act.setShortcut(get_shortcut("edit.fine_cw"))
        fine_cw_act.triggered.connect(lambda: self._fine_rotate_selected(-15))
        edit_menu.addAction(fine_cw_act)

        fine_ccw_act = QAction("Fine Rotate CC&W (15\u00b0)", self)
        fine_ccw_act.setShortcut(get_shortcut("edit.fine_ccw"))
        fine_ccw_act.triggered.connect(lambda: self._fine_rotate_selected(15))
        edit_menu.addAction(fine_ccw_act)

        # -- Flip --
        flip_h_act = QAction("Flip &Horizontal", self)
        flip_h_act.setShortcut(get_shortcut("edit.flip_h"))
        flip_h_act.triggered.connect(lambda: self._flip_selected(horizontal=True))
        edit_menu.addAction(flip_h_act)

        flip_v_act = QAction("Flip &Vertical", self)
        flip_v_act.setShortcut(get_shortcut("edit.flip_v"))
        flip_v_act.triggered.connect(lambda: self._flip_selected(horizontal=False))
        edit_menu.addAction(flip_v_act)

        edit_menu.addSeparator()

        edit_menu.addSeparator()

        # -- Alignment --
        align_h_act = QAction("Align Hori&zontally", self)
        align_h_act.setShortcut(get_shortcut("edit.align_h"))
        align_h_act.triggered.connect(lambda: self._align_selected("horizontal"))
        edit_menu.addAction(align_h_act)

        align_v_act = QAction("Align Vert&ically", self)
        align_v_act.setShortcut(get_shortcut("edit.align_v"))
        align_v_act.triggered.connect(lambda: self._align_selected("vertical"))
        edit_menu.addAction(align_v_act)

        edit_menu.addSeparator()

        hide_layer_act = QAction("&Hide Active Layer", self)
        hide_layer_act.setShortcut(get_shortcut("edit.hide_layer"))
        hide_layer_act.triggered.connect(self._hide_active_layer)
        edit_menu.addAction(hide_layer_act)

        show_layer_act = QAction("S&how Active Layer", self)
        show_layer_act.setShortcut(get_shortcut("edit.show_layer"))
        show_layer_act.triggered.connect(self._show_active_layer)
        edit_menu.addAction(show_layer_act)

        lock_layer_act = QAction("&Lock Active Layer", self)
        lock_layer_act.setShortcut(get_shortcut("edit.lock_layer"))
        lock_layer_act.triggered.connect(self._lock_active_layer)
        edit_menu.addAction(lock_layer_act)

        unlock_layer_act = QAction("&Unlock Active Layer", self)
        unlock_layer_act.setShortcut(get_shortcut("edit.unlock_layer"))
        unlock_layer_act.triggered.connect(self._unlock_active_layer)
        edit_menu.addAction(unlock_layer_act)

        edit_menu.addSeparator()

        bring_fwd_act = QAction("Bring For&ward", self)
        bring_fwd_act.setShortcut(get_shortcut("edit.bring_fwd"))
        bring_fwd_act.triggered.connect(self._bring_forward)
        edit_menu.addAction(bring_fwd_act)

        send_bwd_act = QAction("Send Back&ward", self)
        send_bwd_act.setShortcut(get_shortcut("edit.send_bwd"))
        send_bwd_act.triggered.connect(self._send_backward)
        edit_menu.addAction(send_bwd_act)

        bring_front_act = QAction("Bring to &Front", self)
        bring_front_act.setShortcut(get_shortcut("edit.bring_front"))
        bring_front_act.triggered.connect(self._bring_to_front)
        edit_menu.addAction(bring_front_act)

        send_back_act = QAction("Send to &Back", self)
        send_back_act.setShortcut(get_shortcut("edit.send_back"))
        send_back_act.triggered.connect(self._send_to_back)
        edit_menu.addAction(send_back_act)

        edit_menu.addSeparator()

        group_act = QAction("&Group", self)
        group_act.setShortcut(get_shortcut("edit.group"))
        group_act.triggered.connect(self._group_selected)
        edit_menu.addAction(group_act)

        ungroup_act = QAction("U&ngroup", self)
        ungroup_act.setShortcut(get_shortcut("edit.ungroup"))
        ungroup_act.triggered.connect(self._ungroup_selected)
        edit_menu.addAction(ungroup_act)

        join_wires_act = QAction("&Join Wires", self)
        join_wires_act.setShortcut(get_shortcut("edit.join_wires"))
        join_wires_act.triggered.connect(self._join_wires)
        edit_menu.addAction(join_wires_act)

        edit_menu.addSeparator()

        settings_act = QAction("Se&ttings\u2026", self)
        settings_act.setShortcut(get_shortcut("edit.settings"))
        settings_act.triggered.connect(self._open_settings)
        edit_menu.addAction(settings_act)

        # ---- View ----
        view_menu = menu_bar.addMenu("&View")

        zoom_in_act = QAction("Zoom &In", self)
        zoom_in_act.setShortcut(get_shortcut("view.zoom_in"))
        zoom_in_act.triggered.connect(lambda: self._zoom_active_view(1.25))
        view_menu.addAction(zoom_in_act)

        zoom_out_act = QAction("Zoom &Out", self)
        zoom_out_act.setShortcut(get_shortcut("view.zoom_out"))
        zoom_out_act.triggered.connect(lambda: self._zoom_active_view(0.8))
        view_menu.addAction(zoom_out_act)

        fit_act = QAction("Zoom &All / Fit", self)
        fit_act.setShortcuts([
            get_shortcut("view.fit_all"),
            get_shortcut("view.fit_all2"),
        ])
        fit_act.triggered.connect(self._fit_active_view)
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
        self._zoom_window_act.setShortcut(get_shortcut("view.zoom_window"))
        self._zoom_window_act.toggled.connect(self._toggle_zoom_window)
        view_menu.addAction(self._zoom_window_act)

        view_menu.addSeparator()

        close_tab_act = QAction("Close Tab", self)
        close_tab_act.setShortcut(get_shortcut("view.close_tab"))
        close_tab_act.triggered.connect(self.close_active_tab)
        view_menu.addAction(close_tab_act)

        # ---- Routing ----
        routing_menu = menu_bar.addMenu("&Routing")

        self._trace_mode_act = QAction("&Trace Routing Mode", self)
        self._trace_mode_act.setCheckable(True)
        self._trace_mode_act.setShortcut(get_shortcut("routing.trace"))
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

        draw_menu.addSeparator()

        draw_text_act = QAction("&Text", self)
        draw_text_act.setShortcut(get_shortcut("draw.text"))
        draw_text_act.triggered.connect(self._add_annotation)
        draw_menu.addAction(draw_text_act)

        # ---- Help ----
        help_menu = menu_bar.addMenu("&Help")

        help_act = QAction("&Help", self)
        help_act.setShortcut(get_shortcut("help.help"))
        help_act.triggered.connect(self._show_help)
        help_menu.addAction(help_act)

    # ---------------------------------------------------------------- Toolbar

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Grid", self)
        toolbar.setObjectName("GridToolBar")
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
        self._library_panel.setObjectName("LibraryPanel")
        self._library_panel.table_view_requested.connect(self._open_library_table)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._library_panel)

        # Annotations tool palette below library
        from diagrammer.panels.annotations_panel import AnnotationsPanel
        self._annotations_panel = AnnotationsPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._annotations_panel)
        self.splitDockWidget(self._library_panel, self._annotations_panel, Qt.Orientation.Vertical)
        self._annotations_panel.tool_activated.connect(self._on_annotation_tool)

        from diagrammer.panels.properties_panel import PropertiesPanel
        self._props_panel = PropertiesPanel(self)
        self._props_panel.setObjectName("PropertiesPanel")
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._props_panel)
        self._scene.selectionChanged.connect(self._on_selection_changed)

        from diagrammer.panels.layers_panel import LayersPanel
        self._layers_panel = LayersPanel(self._scene.layer_manager, self)
        self._layers_panel.setObjectName("LayersPanel")
        self._layers_panel.layers_changed.connect(self._on_layers_changed)
        self._layers_panel.active_layer_switched.connect(self._on_layer_switched)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._layers_panel)

        # Stack Properties (top, ~65%) and Layers (bottom, ~35%) vertically
        self.splitDockWidget(self._props_panel, self._layers_panel, Qt.Orientation.Vertical)
        self.resizeDocks(
            [self._props_panel, self._layers_panel],
            [500, 270],  # approximate 65/35 ratio
            Qt.Orientation.Vertical,
        )

        # Restore saved dock layout (overrides defaults if previously saved)
        self._restore_dock_state()

    # --------------------------------------------------------- Tab management

    def _active_scene(self):
        """Return the QGraphicsScene for the currently active tab."""
        widget = self._tab_widget.currentWidget()
        if widget is self._view:
            return self._scene
        if hasattr(widget, 'scene') and callable(widget.scene):
            return widget.scene()
        return self._scene

    def _active_view(self) -> QGraphicsView:
        """Return the QGraphicsView for the currently active tab."""
        widget = self._tab_widget.currentWidget()
        if isinstance(widget, QGraphicsView):
            return widget
        return self._view

    def _update_tab_bar_visibility(self) -> None:
        """Show the tab bar only when there are library table tabs open."""
        self._tab_widget.tabBar().setVisible(self._tab_widget.count() > 1)

    def _on_tab_close_requested(self, index: int) -> None:
        """Close a library table tab (diagram tab at index 0 is never closable)."""
        if index == 0:
            return
        widget = self._tab_widget.widget(index)
        self._tab_widget.removeTab(index)
        self._update_tab_bar_visibility()
        # Clean up the scene and view
        if hasattr(widget, 'scene') and callable(widget.scene):
            scene = widget.scene()
            if scene:
                scene.clear()
        widget.deleteLater()

    def _open_library_table(self, category: str) -> None:
        """Open (or focus) a library table tab for the given category."""
        # Check if a tab for this category already exists
        for i in range(1, self._tab_widget.count()):
            widget = self._tab_widget.widget(i)
            if getattr(widget, '_library_category', None) == category:
                self._tab_widget.setCurrentIndex(i)
                return

        # Collect component defs for this category and subcategories
        defs_by_subcat: dict[str, list] = {}
        if category == "__all__":
            # All categories
            defs_by_subcat = dict(self._library.categories)
        else:
            for cat, cat_defs in self._library.categories.items():
                if cat == category or cat.startswith(category + "/"):
                    defs_by_subcat[cat] = cat_defs

        if not defs_by_subcat:
            return

        from diagrammer.panels.library_table_view import build_library_table
        title = "All Libraries" if category == "__all__" else category
        scene, view = build_library_table(title, defs_by_subcat)
        view._library_category = category  # tag for deduplication

        label = "All Libraries" if category == "__all__" else category.replace("/", " / ").replace("_", " ")
        index = self._tab_widget.addTab(view, f"Table: {label}")
        self._tab_widget.setCurrentIndex(index)
        self._update_tab_bar_visibility()
        # Defer fit_all so the view has its final geometry
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, view.fit_all)

    def close_active_tab(self) -> None:
        """Close the active tab if it's a library table tab (not the diagram)."""
        index = self._tab_widget.currentIndex()
        if index > 0:
            self._on_tab_close_requested(index)

    def _zoom_active_view(self, factor: float) -> None:
        """Zoom the active tab's view."""
        v = self._active_view()
        if hasattr(v, 'zoom_centered'):
            v.zoom_centered(factor)
        else:
            v.scale(factor, factor)

    def _fit_active_view(self) -> None:
        """Fit-all on the active tab's view."""
        v = self._active_view()
        if hasattr(v, 'fit_all'):
            v.fit_all()

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
            # Rescan custom library paths (may have changed)
            for custom_path in app_settings.custom_library_paths:
                p = Path(custom_path)
                if p.is_dir():
                    self._library.scan(p)
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
        self._library_panel._refresh_views()

    def _group_selected(self) -> None:
        """Group all selected items so they act as a single unit.

        Supports nesting: grouping two existing groups pushes a new
        parent group_id on top of each item's group stack.
        """
        from diagrammer.commands.group_command import GroupCommand

        selected = [i for i in self._scene.selectedItems()
                    if hasattr(i, '_group_ids')]
        if len(selected) < 2:
            return

        # Also auto-include internal connections between selected items
        from diagrammer.items.connection_item import ConnectionItem
        item_ids = set(id(i) for i in selected)
        for item in self._scene.items():
            if isinstance(item, ConnectionItem) and id(item) not in item_ids:
                if (id(item.source_port.component) in item_ids and
                        id(item.target_port.component) in item_ids):
                    selected.append(item)
                    item_ids.add(id(item))

        # Also pull in connected junctions
        extra_juncs = self._gather_connected_junctions(selected)
        selected.extend(extra_juncs)

        cmd = GroupCommand(selected)
        self._scene.undo_stack.push(cmd)

    def _ungroup_selected(self) -> None:
        """Pop the top group level from selected items.

        For nested groups, this removes only the outermost group —
        child groups remain intact.
        """
        from diagrammer.commands.group_command import UngroupCommand, get_top_group

        # Find the top-level group_id(s) among selected items
        top_gids = set()
        for item in self._scene.selectedItems():
            gid = get_top_group(item)
            if gid:
                top_gids.add(gid)
        if not top_gids:
            return

        # Collect all members of these top-level groups and pop one level
        all_members = []
        seen = set()
        for gid in top_gids:
            for item in self._scene.get_group_members(gid):
                if id(item) not in seen:
                    all_members.append(item)
                    seen.add(id(item))

        if not all_members:
            return

        # Use the first top gid (there should typically be only one)
        cmd = UngroupCommand(all_members, top_gids.pop())
        self._scene.undo_stack.push(cmd)

    def _join_wires(self) -> None:
        """Join two or more selected wires that share overlapping endpoints (Ctrl+J)."""
        from diagrammer.items.connection_item import ConnectionItem, ROUTE_DIRECT
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.utils.geometry import point_distance

        SNAP = 15.0  # endpoint overlap tolerance

        # Gather selected connections
        conns = [i for i in self._scene.selectedItems() if isinstance(i, ConnectionItem)]
        if len(conns) < 2:
            return

        # Build a list of (connection, end, port, position) for all endpoints
        endpoints = []
        for conn in conns:
            endpoints.append((conn, "source", conn.source_port, conn.source_port.scene_center()))
            endpoints.append((conn, "target", conn.target_port, conn.target_port.scene_center()))

        # Find a pair of endpoints from different wires that overlap
        wire_a = wire_b = None
        end_a = end_b = None
        for i in range(len(endpoints)):
            for j in range(i + 1, len(endpoints)):
                ci, ei, pi, posi = endpoints[i]
                cj, ej, pj, posj = endpoints[j]
                if ci is cj:
                    continue  # same wire
                if point_distance(posi, posj) < SNAP:
                    wire_a, end_a = ci, ei
                    wire_b, end_b = cj, ej
                    break
            if wire_a is not None:
                break

        if wire_a is None:
            return

        # Determine merged source, target, and waypoints.
        # Use _key_points() which includes port positions, so we capture the
        # full path even for wires with no intermediate waypoints.
        kps_a = wire_a._key_points()  # [source_pos, *waypoints, target_pos]
        kps_b = wire_b._key_points()

        # Wire A contributes from its far end to the join point
        if end_a == "target":
            # A goes source→...→target(join), keep as-is, far end is source
            merged_source = wire_a.source_port
            path_a = kps_a  # already source→...→join
        else:
            # A goes source(join)→...→target, reverse it, far end is target
            merged_source = wire_a.target_port
            path_a = list(reversed(kps_a))  # target→...→join

        # Wire B contributes from the join point to its far end
        if end_b == "source":
            # B goes source(join)→...→target, keep as-is, far end is target
            merged_target = wire_b.target_port
            path_b = kps_b  # join→...→target
        else:
            # B goes source→...→target(join), reverse it, far end is source
            merged_target = wire_b.source_port
            path_b = list(reversed(kps_b))  # join→...→source

        # Combine: path_a leads to join, path_b leads away from join
        raw = path_a + path_b
        new_radius = min(wire_a.corner_radius, wire_b.corner_radius)

        # Deduplicate consecutive identical points
        merged_wps = [raw[0]] if raw else []
        for pt in raw[1:]:
            prev = merged_wps[-1]
            if abs(pt.x() - prev.x()) > 0.5 or abs(pt.y() - prev.y()) > 0.5:
                merged_wps.append(pt)

        # Execute as undoable macro
        from diagrammer.commands.delete_command import DeleteCommand
        from diagrammer.commands.connect_command import CreateConnectionCommand

        self._scene.undo_stack.beginMacro("Join wires")
        del_cmd = DeleteCommand(self._scene, [wire_a, wire_b])
        self._scene.undo_stack.push(del_cmd)

        cmd = CreateConnectionCommand(self._scene, merged_source, merged_target)
        self._scene.undo_stack.push(cmd)
        if cmd.connection:
            cmd.connection.routing_mode = ROUTE_DIRECT
            cmd.connection.corner_radius = new_radius
            cmd.connection.vertices = merged_wps
            cmd.connection.line_width = wire_a.line_width
            cmd.connection.line_color = wire_a.line_color
        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

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
        v = self._active_view()
        if v is self._view:
            self._view.zoom_window_mode = enabled
        elif hasattr(v, 'set_zoom_window_mode'):
            v.set_zoom_window_mode(enabled)
        if enabled:
            self._mode_label.setText("Mode: Zoom Window")
        else:
            self._mode_label.setText(f"Mode: {self._scene.mode.name.capitalize()}")

    def _toggle_trace_mode(self, enabled: bool) -> None:
        if enabled:
            self._scene.mode = InteractionMode.CONNECT
            self._view.trace_routing = True
        else:
            self._scene.mode = InteractionMode.SELECT
            self._view.trace_routing = False

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
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import LineItem, ShapeItem
        for item in self._scene.items():
            if isinstance(item, (ComponentItem, ConnectionItem, JunctionItem, ShapeItem, LineItem, AnnotationItem)):
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

        Only gathers junctions where a connection has one end on a selected
        item and the other end on the junction.  Does NOT chain through
        junctions (which would pull in the entire circuit).
        """
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem

        selected_ids = set(id(i) for i in selected_items)
        extra_juncs = []
        for item in self._scene.items():
            if not isinstance(item, ConnectionItem):
                continue
            src_comp = item.source_port.component
            tgt_comp = item.target_port.component
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
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import ShapeItem

        # Collect all movable selected items (not connections)
        comp_targets = [i for i in self._scene.selectedItems() if isinstance(i, ComponentItem)]
        junc_targets = [i for i in self._scene.selectedItems() if isinstance(i, JunctionItem)]
        annot_targets = [i for i in self._scene.selectedItems() if isinstance(i, AnnotationItem)]
        shape_targets = [i for i in self._scene.selectedItems() if isinstance(i, ShapeItem)]

        movable = comp_targets + junc_targets + annot_targets + shape_targets
        if not movable:
            return

        # Auto-include connected junctions
        extra_juncs = self._gather_connected_junctions(movable)
        junc_targets.extend(extra_juncs)
        movable = comp_targets + junc_targets + annot_targets + shape_targets

        self._scene.undo_stack.beginMacro(f"Rotate {len(movable)} items")

        if len(movable) == 1 and len(comp_targets) == 1:
            # Single component: rotate around its own center
            cmd = RotateComponentCommand(comp_targets[0], degrees)
            self._scene.undo_stack.push(cmd)
        elif len(movable) == 1 and isinstance(movable[0], (AnnotationItem, ShapeItem)):
            # Single annotation/shape: rotate around its own center
            from diagrammer.commands.transform_command import RotateItemCommand
            cmd = RotateItemCommand(movable[0], degrees)
            self._scene.undo_stack.push(cmd)
        else:
            # Group rotation: rigid-body
            # Compute scene center for each item
            def _scene_center(item):
                if isinstance(item, ComponentItem):
                    return item.mapToScene(QPointF(item._def.width / 2, item._def.height / 2))
                elif isinstance(item, AnnotationItem):
                    br = item.boundingRect()
                    return item.mapToScene(br.center())
                elif isinstance(item, ShapeItem):
                    return item.mapToScene(QPointF(item.shape_width / 2, item.shape_height / 2))
                else:
                    return item.mapToScene(QPointF(0, 0))

            scene_centers = [_scene_center(item) for item in movable]
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

            from diagrammer.commands.transform_command import RotateItemCommand

            for i, item in enumerate(movable):
                if isinstance(item, ComponentItem):
                    # Rotate internally AND orbit
                    cmd = RotateComponentCommand(item, degrees)
                    self._scene.undo_stack.push(cmd)
                elif isinstance(item, (AnnotationItem, ShapeItem)):
                    # Rotate internally (Qt rotation) AND orbit
                    cmd = RotateItemCommand(item, degrees)
                    self._scene.undo_stack.push(cmd)
                # All items: orbit position around group center
                cur_sc = _scene_center(item)
                offset = target_centers[i] - cur_sc
                new_pos = item.pos() + offset
                if hasattr(item, '_skip_snap'):
                    item._skip_snap = True
                move_cmd = MoveComponentCommand(item, item.pos(), new_pos)
                self._scene.undo_stack.push(move_cmd)
                if hasattr(item, '_skip_snap'):
                    item._skip_snap = False

            # Rotate internal connection waypoints around group center
            # and switch to direct routing to preserve the rotated shape.
            from diagrammer.commands.style_command import ChangeStyleCommand
            from diagrammer.items.connection_item import ROUTE_DIRECT
            item_ids = set(id(c) for c in movable)
            for item in self._scene.items():
                if not isinstance(item, ConnectionItem):
                    continue
                if (id(item.source_port.component) in item_ids and
                        id(item.target_port.component) in item_ids):
                    # Freeze the current expanded route as waypoints so the
                    # rotated shape is preserved (connections with no waypoints
                    # would just get re-routed by the auto-router).
                    old_wps = [QPointF(w) for w in item.vertices]
                    if not old_wps:
                        # No user waypoints — freeze the expanded route
                        old_wps = [QPointF(p) for p in item._key_points()[1:-1]]
                        if not old_wps:
                            # Still nothing — use the midpoint
                            kps = item._key_points()
                            if len(kps) >= 2:
                                mid = QPointF(
                                    (kps[0].x() + kps[-1].x()) / 2,
                                    (kps[0].y() + kps[-1].y()) / 2,
                                )
                                old_wps = [mid]

                    if old_wps:
                        new_wps = []
                        for wp in old_wps:
                            dx, dy = wp.x() - gcx, wp.y() - gcy
                            new_wps.append(QPointF(
                                gcx + dx * cos_a - dy * sin_a,
                                gcy + dx * sin_a + dy * cos_a,
                            ))
                        cmd = EditWaypointsCommand(item, [QPointF(w) for w in item.vertices], new_wps)
                        self._scene.undo_stack.push(cmd)

                    # Switch to direct routing so segments between rotated
                    # waypoints aren't forced back to H/V
                    if item.routing_mode != ROUTE_DIRECT:
                        cmd = ChangeStyleCommand(item, 'routing_mode',
                                                 item.routing_mode, ROUTE_DIRECT)
                        self._scene.undo_stack.push(cmd)

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    def _fine_rotate_selected(self, degrees: float) -> None:
        """Fine-rotate selected items as a rigid body around the group center.

        Uses the same rigid-body approach as _rotate_selected: each component
        rotates internally AND orbits around the pivot. Non-component items
        (annotations, shapes, junctions) orbit only.
        """
        import math
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
        from diagrammer.commands.transform_command import RotateComponentCommand
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import ShapeItem

        comp_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, ComponentItem) and i.ports]
        junc_targets = [i for i in self._scene.selectedItems()
                        if isinstance(i, JunctionItem)]
        annot_targets = [i for i in self._scene.selectedItems()
                         if isinstance(i, AnnotationItem)]
        shape_targets = [i for i in self._scene.selectedItems()
                         if isinstance(i, ShapeItem)]

        movable = comp_targets + junc_targets + annot_targets + shape_targets
        if not movable:
            return

        extra_juncs = self._gather_connected_junctions(movable)
        junc_targets.extend(extra_juncs)
        movable = comp_targets + junc_targets + annot_targets + shape_targets

        self._scene.undo_stack.beginMacro(f"Fine rotate {len(movable)} items")

        # Use the same rigid-body rotation as _rotate_selected
        def _scene_center(item):
            if isinstance(item, ComponentItem):
                return item.mapToScene(QPointF(item._def.width / 2, item._def.height / 2))
            elif isinstance(item, AnnotationItem):
                return item.mapToScene(item.boundingRect().center())
            elif isinstance(item, ShapeItem):
                return item.mapToScene(QPointF(item.shape_width / 2, item.shape_height / 2))
            else:
                return item.mapToScene(QPointF(0, 0))

        # Determine pivot
        pivot = self._scene.rotation_pivot_port
        if pivot is not None:
            pivot_scene = pivot.scene_center()
        elif len(movable) == 1 and len(comp_targets) == 1:
            pivot_scene = comp_targets[0].ports[0].scene_center()
        else:
            scene_centers = [_scene_center(item) for item in movable]
            pivot_scene = QPointF(
                sum(p.x() for p in scene_centers) / len(scene_centers),
                sum(p.y() for p in scene_centers) / len(scene_centers),
            )

        rad = math.radians(degrees)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        px, py = pivot_scene.x(), pivot_scene.y()

        from diagrammer.commands.transform_command import RotateItemCommand

        for item in movable:
            if isinstance(item, ComponentItem):
                # Rotate internally
                cmd = RotateComponentCommand(item, degrees)
                self._scene.undo_stack.push(cmd)
            elif isinstance(item, (AnnotationItem, ShapeItem)):
                cmd = RotateItemCommand(item, degrees)
                self._scene.undo_stack.push(cmd)

            # Orbit around pivot (all item types)
            cur_sc = _scene_center(item)
            dx, dy = cur_sc.x() - px, cur_sc.y() - py
            new_sc = QPointF(px + dx * cos_a - dy * sin_a,
                             py + dx * sin_a + dy * cos_a)
            offset = new_sc - cur_sc
            new_pos = item.pos() + offset
            if hasattr(item, '_skip_snap'):
                item._skip_snap = True
            move_cmd = MoveComponentCommand(item, item.pos(), new_pos)
            self._scene.undo_stack.push(move_cmd)
            if hasattr(item, '_skip_snap'):
                item._skip_snap = False

        # Rotate internal connection waypoints around pivot and switch to direct routing
        from diagrammer.commands.style_command import ChangeStyleCommand
        from diagrammer.items.connection_item import ROUTE_DIRECT
        item_ids = set(id(c) for c in movable)
        for item in self._scene.items():
            if not isinstance(item, ConnectionItem):
                continue
            if (id(item.source_port.component) in item_ids and
                    id(item.target_port.component) in item_ids):
                old_wps = [QPointF(w) for w in item.vertices]
                if not old_wps:
                    old_wps = [QPointF(p) for p in item._key_points()[1:-1]]
                if old_wps:
                    new_wps = [QPointF(px + (w.x()-px)*cos_a - (w.y()-py)*sin_a,
                                       py + (w.x()-px)*sin_a + (w.y()-py)*cos_a)
                               for w in old_wps]
                    cmd = EditWaypointsCommand(item, [QPointF(w) for w in item.vertices], new_wps)
                    self._scene.undo_stack.push(cmd)
                if item.routing_mode != ROUTE_DIRECT:
                    cmd = ChangeStyleCommand(item, 'routing_mode',
                                             item.routing_mode, ROUTE_DIRECT)
                    self._scene.undo_stack.push(cmd)

        self._scene.undo_stack.endMacro()
        self._scene.update_connections()

    def _flip_selected(self, horizontal: bool) -> None:
        from diagrammer.commands.add_command import MoveComponentCommand
        from diagrammer.commands.connect_command import EditWaypointsCommand
        from diagrammer.commands.transform_command import FlipComponentCommand
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import ShapeItem

        selected_comps = [i for i in self._scene.selectedItems() if isinstance(i, ComponentItem)]
        selected_juncs = [i for i in self._scene.selectedItems() if isinstance(i, JunctionItem)]
        selected_annots = [i for i in self._scene.selectedItems() if isinstance(i, AnnotationItem)]
        selected_shapes = [i for i in self._scene.selectedItems() if isinstance(i, ShapeItem)]

        all_movable = selected_comps + selected_juncs + selected_annots + selected_shapes
        extra_juncs = self._gather_connected_junctions(all_movable)
        selected_juncs.extend(extra_juncs)
        all_movable = selected_comps + selected_juncs + selected_annots + selected_shapes

        axis = "H" if horizontal else "V"
        self._scene.undo_stack.beginMacro(f"Flip {axis} {len(all_movable)} items")

        if len(all_movable) <= 1:
            for item in selected_comps:
                cmd = FlipComponentCommand(item, horizontal)
                self._scene.undo_stack.push(cmd)
            from diagrammer.commands.transform_command import FlipItemCommand
            for item in selected_annots + selected_shapes:
                cmd = FlipItemCommand(item, horizontal)
                self._scene.undo_stack.push(cmd)
            self._scene.undo_stack.endMacro()
            self._scene.update_connections()
            return

        # Compute group center from all movable items
        def _scene_center(item):
            if isinstance(item, ComponentItem):
                return QPointF(item.pos().x() + item._width / 2, item.pos().y() + item._height / 2)
            elif isinstance(item, AnnotationItem):
                br = item.boundingRect()
                return item.mapToScene(br.center())
            elif isinstance(item, ShapeItem):
                return item.mapToScene(QPointF(item.shape_width / 2, item.shape_height / 2))
            else:
                return item.mapToScene(QPointF(0, 0))

        centers = [_scene_center(item) for item in all_movable]
        group_cx = sum(p.x() for p in centers) / len(centers)
        group_cy = sum(p.y() for p in centers) / len(centers)

        item_set = set(id(c) for c in all_movable)

        # Flip and mirror components
        for comp in selected_comps:
            cmd = FlipComponentCommand(comp, horizontal)
            self._scene.undo_stack.push(cmd)
            old_pos = comp.pos()
            if horizontal:
                new_pos = QPointF(2 * group_cx - old_pos.x() - comp._width, old_pos.y())
            else:
                new_pos = QPointF(old_pos.x(), 2 * group_cy - old_pos.y() - comp._height)
            comp._skip_snap = True
            move_cmd = MoveComponentCommand(comp, old_pos, new_pos)
            self._scene.undo_stack.push(move_cmd)
            comp._skip_snap = False

        # Flip annotations and shapes internally
        from diagrammer.commands.transform_command import FlipItemCommand
        for item in selected_annots + selected_shapes:
            cmd = FlipItemCommand(item, horizontal)
            self._scene.undo_stack.push(cmd)

        # Mirror non-component items (junctions, annotations, shapes)
        for item in selected_juncs + selected_annots + selected_shapes:
            old_pos = item.pos()
            sc = _scene_center(item)
            if horizontal:
                new_sc_x = 2 * group_cx - sc.x()
                new_pos = QPointF(old_pos.x() + (new_sc_x - sc.x()), old_pos.y())
            else:
                new_sc_y = 2 * group_cy - sc.y()
                new_pos = QPointF(old_pos.x(), old_pos.y() + (new_sc_y - sc.y()))
            if hasattr(item, '_skip_snap'):
                item._skip_snap = True
            move_cmd = MoveComponentCommand(item, old_pos, new_pos)
            self._scene.undo_stack.push(move_cmd)
            if hasattr(item, '_skip_snap'):
                item._skip_snap = False

        # Mirror connection waypoints
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

    # ------------------------------------------------------- Z-ordering

    def _bring_forward(self) -> None:
        """Move selected items one step forward in the stacking order (Ctrl+])."""
        selected = self._scene.selectedItems()
        if not selected:
            return
        selected_ids = set(id(i) for i in selected)
        # Find the next item above and swap z-values
        for item in selected:
            best_z = None
            for other in self._scene.items():
                if id(other) in selected_ids:
                    continue
                if other.zValue() > item.zValue():
                    if best_z is None or other.zValue() < best_z:
                        best_z = other.zValue()
            if best_z is not None:
                item.setZValue(best_z + 0.001)

    def _send_backward(self) -> None:
        """Move selected items one step backward in the stacking order (Ctrl+[)."""
        selected = self._scene.selectedItems()
        if not selected:
            return
        selected_ids = set(id(i) for i in selected)
        for item in selected:
            best_z = None
            for other in self._scene.items():
                if id(other) in selected_ids:
                    continue
                if other.zValue() < item.zValue():
                    if best_z is None or other.zValue() > best_z:
                        best_z = other.zValue()
            if best_z is not None:
                item.setZValue(best_z - 0.001)

    def _bring_to_front(self) -> None:
        """Move selected items to the top of the stacking order (Ctrl+Shift+])."""
        selected = self._scene.selectedItems()
        if not selected:
            return
        max_z = max(item.zValue() for item in self._scene.items()) if self._scene.items() else 0
        for item in selected:
            max_z += 0.01
            item.setZValue(max_z)

    def _send_to_back(self) -> None:
        """Move selected items to the bottom of the stacking order (Ctrl+Shift+[)."""
        selected = self._scene.selectedItems()
        if not selected:
            return
        min_z = min(item.zValue() for item in self._scene.items()) if self._scene.items() else 0
        for item in selected:
            min_z -= 0.01
            item.setZValue(min_z)

    # ------------------------------------------------------- Cut / Copy / Paste

    def _copy(self) -> None:
        """Copy selected components, junctions, annotations, and connections to clipboard."""
        from diagrammer.items.annotation_item import AnnotationItem
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
        selected_annots = [
            item for item in self._scene.selectedItems()
            if isinstance(item, AnnotationItem)
        ]
        selected_conns_direct = [
            item for item in self._scene.selectedItems()
            if isinstance(item, ConnectionItem)
        ]

        # Auto-include endpoint junctions for selected connections
        # (free wires have invisible junctions the user can't select directly)
        for conn in selected_conns_direct:
            for port in (conn.source_port, conn.target_port):
                comp = port.component
                if isinstance(comp, JunctionItem) and comp not in selected_juncs:
                    selected_juncs.append(comp)

        if not selected_comps and not selected_juncs and not selected_annots:
            return

        # Auto-include connected junctions (invisible T-junction markers)
        all_selected = selected_comps + selected_juncs
        extra_juncs = self._gather_connected_junctions(all_selected)
        selected_juncs.extend(extra_juncs)
        all_selected = selected_comps + selected_juncs
        item_set = set(id(c) for c in all_selected)

        # Collect connections where both endpoints are in the selection
        selected_conns = []
        for item in self._scene.items():
            if isinstance(item, ConnectionItem):
                if (id(item.source_port.component) in item_set and
                        id(item.target_port.component) in item_set):
                    selected_conns.append(item)

        # Serialize to clipboard
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
                "group": list(comp._group_ids),
            })
            idx += 1
        for junc in selected_juncs:
            id_map[junc.instance_id] = idx
            self._clipboard.append({
                "type": "junction",
                "pos": (junc.pos().x(), junc.pos().y()),
                "visible": junc.isVisible(),
                "group": list(junc._group_ids),
            })
            idx += 1
        for annot in selected_annots:
            self._clipboard.append({
                "type": "annotation",
                "pos": (annot.pos().x(), annot.pos().y()),
                "source_text": annot.source_text,
                "font_family": annot.font_family,
                "font_size": annot.font_size,
                "font_bold": annot.font_bold,
                "font_italic": annot.font_italic,
                "text_color": annot.text_color.name(),
                "rotation": annot.rotation(),
                "group": list(annot._group_ids),
            })
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
                    "group": list(conn._group_ids),
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
        import uuid as _uuid
        from diagrammer.commands.add_command import AddComponentCommand
        from diagrammer.commands.connect_command import CreateConnectionCommand
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.junction_item import JunctionItem

        # Remap group_ids so pasted items form new groups (not shared with originals)
        old_gid_to_new: dict[str, str] = {}

        def _remap_group(target_item, entry_data):
            """Remap the group stack from clipboard data onto a new item."""
            from diagrammer.commands.group_command import set_group_ids
            raw = entry_data.get("group", [])
            if isinstance(raw, str):
                raw = [raw] if raw else []
            if raw:
                new_stack = []
                for gid in raw:
                    if gid not in old_gid_to_new:
                        old_gid_to_new[gid] = _uuid.uuid4().hex[:12]
                    new_stack.append(old_gid_to_new[gid])
                set_group_ids(target_item, new_stack)

        PASTE_OFFSET = 40.0  # offset pasted items from originals

        # First pass: create components, junctions, and annotations
        new_items: list = []  # ComponentItem, JunctionItem, AnnotationItem, or None
        pasted_annotations: list = []
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
                _remap_group(item, entry)
                new_items.append(item)
            elif entry["type"] == "junction":
                junc = JunctionItem()
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                junc._skip_snap = True
                junc.setPos(pos)
                junc._skip_snap = False
                if not entry.get("visible", True):
                    junc.setVisible(False)
                self._scene.addItem(junc)
                _remap_group(junc, entry)
                new_items.append(junc)
            elif entry["type"] == "annotation":
                annot = AnnotationItem(entry.get("source_text", "Text"))
                pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
                if "font_family" in entry:
                    annot.font_family = entry["font_family"]
                if "font_size" in entry:
                    annot.font_size = entry["font_size"]
                if entry.get("font_bold"):
                    annot.font_bold = True
                if entry.get("font_italic"):
                    annot.font_italic = True
                if "text_color" in entry:
                    annot.text_color = QColor(entry["text_color"])
                if entry.get("rotation"):
                    annot.setTransformOriginPoint(annot.boundingRect().center())
                    annot.setRotation(entry["rotation"])
                annot.setPos(pos)
                self._scene.addItem(annot)
                _remap_group(annot, entry)
                pasted_annotations.append(annot)

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
                    _remap_group(conn, entry)

        self._scene.undo_stack.endMacro()

        # Update all routes so approach segments and lead shortening are computed
        self._scene.update_connections()

        # Select the pasted items
        self._scene.clearSelection()
        for item in new_items:
            if item is not None:
                item.setSelected(True)
        for item in pasted_annotations:
            item.setSelected(True)

    # ------------------------------------------------- Copy Selection as Image

    def _copy_selection_as_image(self) -> None:
        """Copy selected items (or entire scene) to clipboard as PDF + PNG."""
        from diagrammer.io import exporter as _exp

        scene = self._active_scene()
        ok = _exp.DiagramExporter.copy_selection_to_clipboard(scene)
        if ok:
            method = _exp.last_clipboard_method
            if method.startswith("native") or method.startswith("subprocess"):
                detail = "as vector (PDF)"
            else:
                detail = "as image (PNG)"
            self.statusBar().showMessage(
                f"Copied to clipboard {detail}  [{method}]", 5000,
            )
        else:
            self.statusBar().showMessage("Nothing to copy", 3000)

    # ----------------------------------------------------------- Annotation tools

    def _on_annotation_tool(self, tool_id: str) -> None:
        """Handle click from the Annotations panel."""
        if tool_id == "text":
            self._add_annotation()
        elif tool_id == "arrow":
            self._add_arrow()
        elif tool_id in ("rectangle", "ellipse", "line"):
            self._add_shape(tool_id)

    def _add_arrow(self) -> None:
        """Add a line with a forward arrowhead, using saved defaults."""
        from diagrammer.commands.shape_command import AddShapeCommand
        from diagrammer.items.shape_item import ARROW_FORWARD, LineItem

        center = self._view.mapToScene(self._view.viewport().rect().center())
        snapped = self._view.snap(center)
        item = LineItem(start=QPointF(0, 0), end=QPointF(100, 0))
        item.arrow_style = ARROW_FORWARD
        item.stroke_color = app_settings.default_shape_stroke_color
        item.stroke_width = app_settings.default_shape_stroke_width
        item.dash_style = app_settings.default_shape_dash_style
        item.cap_style = app_settings.default_shape_cap_style
        item.arrow_type = app_settings.default_shape_arrow_type
        item.arrow_scale = app_settings.default_shape_arrow_scale
        item.arrow_extend = app_settings.default_shape_arrow_extend

        cmd = AddShapeCommand(self._scene, item, snapped)
        self._scene.undo_stack.push(cmd)

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
            item.corner_radius = app_settings.default_shape_corner_radius
        elif shape_type == "ellipse":
            item = EllipseItem(width=80, height=80)
        elif shape_type == "line":
            item = LineItem(start=QPointF(0, 0), end=QPointF(100, 0))
            item.cap_style = app_settings.default_shape_cap_style
        else:
            return

        # Apply saved shape defaults
        item.stroke_color = app_settings.default_shape_stroke_color
        item.stroke_width = app_settings.default_shape_stroke_width
        item.dash_style = app_settings.default_shape_dash_style
        if hasattr(item, 'fill_color'):
            item.fill_color = app_settings.default_shape_fill_color

        cmd = AddShapeCommand(self._scene, item, snapped)
        self._scene.undo_stack.push(cmd)

    def _add_annotation(self) -> None:
        """Add a text annotation at the center of the current view."""
        from diagrammer.items.annotation_item import AnnotationItem

        center = self._view.mapToScene(
            self._view.viewport().rect().center()
        )
        snapped = self._view.snap(center)

        item = AnnotationItem("Text")
        item.font_family = app_settings.default_annotation_font
        item.font_size = app_settings.default_annotation_size
        item.text_color = app_settings.default_annotation_color
        item.font_bold = app_settings.default_annotation_bold
        item.font_italic = app_settings.default_annotation_italic
        item.setPos(snapped)

        # Undoable add: push a command that adds/removes the item
        from PySide6.QtGui import QUndoCommand

        class AddAnnotationCommand(QUndoCommand):
            def __init__(self, scene, annotation):
                super().__init__("Add text annotation")
                self._scene = scene
                self._item = annotation
                self._first = True

            def redo(self):
                if self._first:
                    self._first = False
                    return  # item added manually below
                self._scene.addItem(self._item)

            def undo(self):
                self._scene.removeItem(self._item)

        cmd = AddAnnotationCommand(self._scene, item)
        self._scene.addItem(item)
        self._scene.undo_stack.push(cmd)

        self._scene.clearSelection()
        item.setSelected(True)
        item.start_editing()

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

    def _save_dock_state(self) -> None:
        """Persist window geometry and dock layout to QSettings."""
        from PySide6.QtCore import QSettings
        settings = QSettings()
        settings.setValue("mainwindow/geometry", self.saveGeometry())
        settings.setValue("mainwindow/state", self.saveState())

    def _restore_dock_state(self) -> None:
        """Restore window geometry and dock layout from QSettings."""
        from PySide6.QtCore import QSettings
        settings = QSettings()
        geom = settings.value("mainwindow/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        state = settings.value("mainwindow/state")
        if state is not None:
            self.restoreState(state)

    def closeEvent(self, event) -> None:
        """Prompt for unsaved changes, then auto-save session state and dock layout."""
        if not self._check_unsaved_changes():
            event.ignore()
            return
        try:
            self._save_dock_state()
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

    def _last_dir(self) -> str:
        """Return the last used directory for file dialogs."""
        d = app_settings.last_directory
        if d and Path(d).is_dir():
            return d
        return ""

    def _track_file(self, path: str) -> None:
        """Record a file as recently used and update the last directory."""
        app_settings.last_directory = str(Path(path).parent)
        # Add to recent files (most recent first, deduplicated, max 10)
        recent = app_settings.recent_files
        abs_path = str(Path(path).resolve())
        recent = [f for f in recent if f != abs_path]
        recent.insert(0, abs_path)
        app_settings.recent_files = recent[:10]
        app_settings.save()
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        """Populate the Recent Files submenu."""
        self._recent_menu.clear()
        for path in app_settings.recent_files:
            name = Path(path).name
            act = self._recent_menu.addAction(name)
            act.setToolTip(path)
            act.triggered.connect(lambda checked=False, p=path: self._open_file(p))
        if not app_settings.recent_files:
            act = self._recent_menu.addAction("(No recent files)")
            act.setEnabled(False)
        else:
            self._recent_menu.addSeparator()
            clear_act = self._recent_menu.addAction("Clear Recent Files")
            clear_act.triggered.connect(self._clear_recent_files)

    def _clear_recent_files(self) -> None:
        app_settings.recent_files = []
        app_settings.save()
        self._rebuild_recent_menu()

    def _open_file(self, path: str) -> None:
        """Open a specific .dgm file (used by recent files and file_open)."""
        if not Path(path).exists():
            app_settings.recent_files = [f for f in app_settings.recent_files if f != path]
            app_settings.save()
            self._rebuild_recent_menu()
            return
        from diagrammer.io.serializer import DiagramSerializer
        self._scene.clear()
        self._scene.undo_stack.clear()
        DiagramSerializer.load(self._scene, path, library=self._library)
        self._layers_panel._manager = self._scene._layer_manager
        self._layers_panel.refresh()
        self._scene.apply_layer_state()
        self._current_file = path
        self._update_title()
        self._track_file(path)

    def _file_new(self) -> None:
        if not self._check_unsaved_changes():
            return
        self._scene.clear()
        self._scene.undo_stack.clear()
        from diagrammer.panels.layers_panel import LayerManager
        self._scene._layer_manager = LayerManager()
        self._layers_panel._manager = self._scene._layer_manager
        self._layers_panel.refresh()
        self._current_file = None
        self._update_title()

    def _file_open(self) -> None:
        if not self._check_unsaved_changes():
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Diagram", self._last_dir(),
            "Diagrammer Files (*.dgm);;All Files (*)"
        )
        if path:
            self._open_file(path)

    def _file_save(self) -> None:
        if self._current_file:
            from diagrammer.io.serializer import DiagramSerializer
            DiagramSerializer.save(self._scene, self._current_file)
            self._scene.undo_stack.setClean()
            self._track_file(self._current_file)
        else:
            self._file_save_as()

    def _file_save_as(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Diagram", self._last_dir(),
            "Diagrammer Files (*.dgm);;All Files (*)"
        )
        if path:
            if not path.endswith(".dgm"):
                path += ".dgm"
            from diagrammer.io.serializer import DiagramSerializer
            DiagramSerializer.save(self._scene, path)
            self._scene.undo_stack.setClean()
            self._current_file = path
            self._update_title()
            self._track_file(path)

    def _check_unsaved_changes(self) -> bool:
        """Check for unsaved changes and prompt the user.

        Returns True if it's OK to proceed (saved, discarded, or no changes).
        Returns False if the user cancelled.
        """
        if self._scene.undo_stack.isClean():
            return True
        # Also treat an empty scene as clean
        if not self._scene.items():
            return True
        from PySide6.QtWidgets import QMessageBox
        name = Path(self._current_file).name if self._current_file else "Untitled"
        reply = QMessageBox.warning(
            self,
            "Unsaved Changes",
            f'"{name}" has unsaved changes.\n\nDo you want to save before continuing?',
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            self._file_save()
            # If user cancelled the Save As dialog, the stack is still dirty
            return self._scene.undo_stack.isClean()
        if reply == QMessageBox.StandardButton.Discard:
            return True
        return False  # Cancel

    def _export_svg(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SVG", self._last_dir(), "SVG Files (*.svg);;All Files (*)"
        )
        if path:
            from diagrammer.io.exporter import DiagramExporter
            DiagramExporter.export_svg(self._active_scene(), path)
            app_settings.last_directory = str(Path(path).parent)
            app_settings.save()

    def _export_png(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", self._last_dir(), "PNG Files (*.png);;All Files (*)"
        )
        if path:
            from diagrammer.io.exporter import DiagramExporter
            DiagramExporter.export_png(self._active_scene(), path)
            app_settings.last_directory = str(Path(path).parent)
            app_settings.save()

    def _export_pdf(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", self._last_dir(), "PDF Files (*.pdf);;All Files (*)"
        )
        if path:
            from diagrammer.io.exporter import DiagramExporter
            DiagramExporter.export_pdf(self._active_scene(), path)
            app_settings.last_directory = str(Path(path).parent)
            app_settings.save()

    def _create_component_from_selection(self) -> None:
        """Export selected items as a reusable SVG component in the library."""
        from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

        selected = self._scene.selectedItems()
        if not selected:
            QMessageBox.information(self, "No Selection",
                                   "Select components and connections first.")
            return

        # Ask for component name
        name, ok = QInputDialog.getText(self, "Create Component",
                                        "Component name:")
        if not ok or not name.strip():
            return
        name = name.strip()

        # Ask for save location within components directory
        from diagrammer.models.library import ComponentLibrary
        comp_dir = Path(__file__).resolve().parent / "components"
        if not comp_dir.is_dir():
            comp_dir = Path.home() / ".diagrammer" / "components"
            comp_dir.mkdir(parents=True, exist_ok=True)

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Component", str(comp_dir / f"{name}.svg"),
            "SVG Files (*.svg);;All Files (*)"
        )
        if not path:
            return
        if not path.endswith(".svg"):
            path += ".svg"

        from diagrammer.io.compound_export import export_compound_component
        success = export_compound_component(
            self._scene, selected, Path(path), name
        )
        if success:
            # Rescan library to pick up the new component
            builtin_path = Path(__file__).resolve().parent / "components"
            if builtin_path.is_dir():
                self._library.scan(builtin_path)
            self._library_panel._refresh_views()
            QMessageBox.information(self, "Component Created",
                                   f"'{name}' saved to:\n{path}")
        else:
            QMessageBox.warning(self, "Export Failed",
                                "Could not create component from selection.")
