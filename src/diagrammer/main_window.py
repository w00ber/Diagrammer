"""MainWindow — the top-level application window for Diagrammer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QKeySequence
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
        file_menu.addAction(new_act)

        open_act = QAction("&Open...", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        file_menu.addAction(open_act)

        save_act = QAction("&Save", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        file_menu.addAction(save_act)

        save_as_act = QAction("Save &As...", self)
        save_as_act.setShortcut(QKeySequence.StandardKey.SaveAs)
        file_menu.addAction(save_as_act)

        file_menu.addSeparator()

        export_svg_act = QAction("Export as SV&G...", self)
        file_menu.addAction(export_svg_act)

        export_png_act = QAction("Export as &PNG...", self)
        file_menu.addAction(export_png_act)

        export_pdf_act = QAction("Export as P&DF...", self)
        file_menu.addAction(export_pdf_act)

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
        align_h_act.setShortcut(QKeySequence(Qt.Key.Key_H))
        align_h_act.triggered.connect(lambda: self._align_selected("horizontal"))
        edit_menu.addAction(align_h_act)

        align_v_act = QAction("Align Vert&ically", self)
        align_v_act.setShortcut(QKeySequence(Qt.Key.Key_V))
        align_v_act.triggered.connect(lambda: self._align_selected("vertical"))
        edit_menu.addAction(align_v_act)

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

        props_dock = QDockWidget("Properties", self)
        props_dock.setWidget(QLabel("  Properties panel \u2014 coming soon  "))
        props_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, props_dock)

        layers_dock = QDockWidget("Layers", self)
        layers_dock.setWidget(QLabel("  Layers panel \u2014 coming soon  "))
        layers_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, layers_dock)
        self.tabifyDockWidget(props_dock, layers_dock)

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

    def _delete_selected(self) -> None:
        from diagrammer.commands.delete_command import DeleteCommand
        selected = self._scene.selectedItems()
        if selected:
            cmd = DeleteCommand(self._scene, selected)
            self._scene.undo_stack.push(cmd)

    def _rotate_selected(self, degrees: float) -> None:
        from diagrammer.commands.transform_command import RotateComponentCommand
        from diagrammer.items.component_item import ComponentItem

        for item in self._scene.selectedItems():
            if isinstance(item, ComponentItem):
                cmd = RotateComponentCommand(item, degrees)
                self._scene.undo_stack.push(cmd)
        self._scene.update_connections()

    def _fine_rotate_selected(self, degrees: float) -> None:
        """Fine-rotate selected components around the pivot port (or first port).

        Set the pivot port by Shift+clicking on a port before pressing R.
        """
        from diagrammer.commands.transform_command import RotateAroundPortCommand
        from diagrammer.items.component_item import ComponentItem

        pivot = self._scene.rotation_pivot_port

        for item in self._scene.selectedItems():
            if isinstance(item, ComponentItem) and item.ports:
                # Use the scene's pivot port if it belongs to this component,
                # otherwise use the component's first port
                if pivot is not None and pivot.component is item:
                    port = pivot
                else:
                    port = item.ports[0]
                cmd = RotateAroundPortCommand(item, port, degrees)
                self._scene.undo_stack.push(cmd)
        self._scene.update_connections()

    def _flip_selected(self, horizontal: bool) -> None:
        from diagrammer.commands.transform_command import FlipComponentCommand
        from diagrammer.items.component_item import ComponentItem

        for item in self._scene.selectedItems():
            if isinstance(item, ComponentItem):
                cmd = FlipComponentCommand(item, horizontal)
                self._scene.undo_stack.push(cmd)
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

        self._scene.update_connections()
        # Clear alignment selection after aligning
        self._scene.clear_alignment_ports()

    # ------------------------------------------------------- Cut / Copy / Paste

    def _copy(self) -> None:
        """Copy selected components (and connections between them) to clipboard."""
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem

        selected_comps = [
            item for item in self._scene.selectedItems()
            if isinstance(item, ComponentItem)
        ]
        if not selected_comps:
            return

        comp_set = set(id(c) for c in selected_comps)

        # Collect connections where both endpoints are in the selection
        selected_conns = []
        for item in self._scene.items():
            if isinstance(item, ConnectionItem):
                if (id(item.source_port.component) in comp_set and
                        id(item.target_port.component) in comp_set):
                    selected_conns.append(item)

        # Serialize to clipboard: store component defs, positions, transforms, and connection topology
        self._clipboard = []
        comp_id_map: dict[str, int] = {}  # instance_id -> clipboard index
        for i, comp in enumerate(selected_comps):
            comp_id_map[comp.instance_id] = i
            self._clipboard.append({
                "type": "component",
                "def_key": comp.library_key,
                "pos": (comp.pos().x(), comp.pos().y()),
                "rotation": comp.rotation_angle,
                "flip_h": comp.flip_h,
                "flip_v": comp.flip_v,
            })
        for conn in selected_conns:
            src_idx = comp_id_map.get(conn.source_port.component.instance_id)
            tgt_idx = comp_id_map.get(conn.target_port.component.instance_id)
            if src_idx is not None and tgt_idx is not None:
                self._clipboard.append({
                    "type": "connection",
                    "src_comp": src_idx,
                    "src_port": conn.source_port.port_name,
                    "tgt_comp": tgt_idx,
                    "tgt_port": conn.target_port.port_name,
                    "vertices": [(v.x(), v.y()) for v in conn.vertices],
                })

    def _cut(self) -> None:
        """Copy selected items, then delete them."""
        self._copy()
        self._delete_selected()

    def _paste(self) -> None:
        """Paste clipboard contents, offset from original positions."""
        if not self._clipboard:
            return

        from diagrammer.commands.add_command import AddComponentCommand
        from diagrammer.commands.connect_command import CreateConnectionCommand

        PASTE_OFFSET = 40.0  # offset pasted items from originals

        # First pass: create components
        new_comps: list = []  # list of ComponentItem, indexed by clipboard order
        for entry in self._clipboard:
            if entry["type"] != "component":
                continue
            comp_def = self._library.get(entry["def_key"])
            if comp_def is None:
                new_comps.append(None)
                continue
            pos = QPointF(entry["pos"][0] + PASTE_OFFSET, entry["pos"][1] + PASTE_OFFSET)
            cmd = AddComponentCommand(self._scene, comp_def, pos)
            self._scene.undo_stack.push(cmd)
            item = cmd.item
            # Apply transforms
            if entry.get("rotation", 0):
                item.rotate_by(entry["rotation"])
            if entry.get("flip_h"):
                item.set_flip_h(True)
            if entry.get("flip_v"):
                item.set_flip_v(True)
            new_comps.append(item)

        # Second pass: create connections
        for entry in self._clipboard:
            if entry["type"] != "connection":
                continue
            src_comp = new_comps[entry["src_comp"]] if entry["src_comp"] < len(new_comps) else None
            tgt_comp = new_comps[entry["tgt_comp"]] if entry["tgt_comp"] < len(new_comps) else None
            if src_comp is None or tgt_comp is None:
                continue
            src_port = src_comp.port_by_name(entry["src_port"])
            tgt_port = tgt_comp.port_by_name(entry["tgt_port"])
            if src_port and tgt_port:
                cmd = CreateConnectionCommand(self._scene, src_port, tgt_port)
                self._scene.undo_stack.push(cmd)
                # Restore vertices (offset)
                if entry.get("vertices") and cmd.connection:
                    cmd.connection.vertices = [
                        QPointF(v[0] + PASTE_OFFSET, v[1] + PASTE_OFFSET)
                        for v in entry["vertices"]
                    ]

        # Select the pasted items
        self._scene.clearSelection()
        for comp in new_comps:
            if comp is not None:
                comp.setSelected(True)

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
