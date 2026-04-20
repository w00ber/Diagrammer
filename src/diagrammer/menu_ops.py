"""Menu and toolbar construction mixin for MainWindow."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QLabel,
    QPushButton,
    QToolBar,
)

from diagrammer.canvas.grid import DEFAULT_GRID_SPACING
from diagrammer.canvas.scene import InteractionMode
from diagrammer.panels.settings_dialog import app_settings
from diagrammer.shortcuts import get as get_shortcut

logger = logging.getLogger(__name__)


class MenuMixin:
    """Mixin that builds menus and the toolbar for the main window.

    Expects the host class to expose ``_scene``, ``_view``,
    ``_shortcut_actions``, and the various slot methods wired up here.
    """

    # ------------------------------------------------------------------ Menus

    def _create_menus(self) -> None:
        menu_bar = self.menuBar()

        # ---- File ----
        file_menu = menu_bar.addMenu("&File")

        new_act = QAction("&New", self)
        self._register_shortcut(new_act, "file.new")
        new_act.triggered.connect(self._file_new)
        file_menu.addAction(new_act)

        open_act = QAction("&Open...", self)
        self._register_shortcut(open_act, "file.open")
        open_act.triggered.connect(self._file_open)
        file_menu.addAction(open_act)

        examples_act = QAction("E&xamples...", self)
        examples_act.triggered.connect(self._file_open_example)
        file_menu.addAction(examples_act)

        save_act = QAction("&Save", self)
        self._register_shortcut(save_act, "file.save")
        save_act.triggered.connect(self._file_save)
        file_menu.addAction(save_act)

        save_as_act = QAction("Save &As...", self)
        self._register_shortcut(save_as_act, "file.save_as")
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

        import_svg_act = QAction("&Import SVG Image...", self)
        import_svg_act.triggered.connect(self._import_svg_image)
        file_menu.addAction(import_svg_act)

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
        self._register_shortcut(quit_act, "file.quit")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # ---- Edit ----
        edit_menu = menu_bar.addMenu("&Edit")

        undo_act = self._scene.undo_stack.createUndoAction(self, "&Undo")
        self._register_shortcut(undo_act, "edit.undo")
        edit_menu.addAction(undo_act)

        redo_act = self._scene.undo_stack.createRedoAction(self, "&Redo")
        self._register_shortcut(redo_act, "edit.redo")
        edit_menu.addAction(redo_act)

        edit_menu.addSeparator()

        cut_act = QAction("Cu&t", self)
        self._register_shortcut(cut_act, "edit.cut")
        cut_act.triggered.connect(self._cut)
        edit_menu.addAction(cut_act)

        copy_act = QAction("&Copy", self)
        self._register_shortcut(copy_act, "edit.copy")
        copy_act.triggered.connect(self._copy)
        edit_menu.addAction(copy_act)

        paste_act = QAction("&Paste", self)
        self._register_shortcut(paste_act, "edit.paste")
        paste_act.triggered.connect(self._paste)
        edit_menu.addAction(paste_act)

        copy_image_act = QAction("Copy Selection as &Image", self)
        self._register_shortcut(copy_image_act, "edit.copy_as_image")
        copy_image_act.triggered.connect(self._copy_selection_as_image)
        edit_menu.addAction(copy_image_act)

        edit_menu.addSeparator()

        select_all_act = QAction("Select &All", self)
        self._register_shortcut(select_all_act, "edit.select_all")
        select_all_act.triggered.connect(self._select_all)
        edit_menu.addAction(select_all_act)

        delete_act = QAction("&Delete", self)
        self._register_shortcut(
            delete_act, "edit.delete",
            extra=[QKeySequence(Qt.Key.Key_Backspace)],
        )
        delete_act.triggered.connect(self._delete_selected)
        edit_menu.addAction(delete_act)

        edit_menu.addSeparator()

        # -- Rotation (90°, center) --
        rotate_ccw_act = QAction("Rotate CCW (&90\u00b0)", self)
        self._register_shortcut(rotate_ccw_act, "edit.rotate_ccw")
        rotate_ccw_act.triggered.connect(lambda: self._rotate_selected(-90))
        edit_menu.addAction(rotate_ccw_act)

        rotate_cw_act = QAction("Rotate CW (9&0\u00b0)", self)
        self._register_shortcut(rotate_cw_act, "edit.rotate_cw")
        rotate_cw_act.triggered.connect(lambda: self._rotate_selected(90))
        edit_menu.addAction(rotate_cw_act)

        # -- Fine rotation (15°, around first port) --
        fine_cw_act = QAction("Fine Rotate C&W (15\u00b0)", self)
        self._register_shortcut(fine_cw_act, "edit.fine_cw")
        fine_cw_act.triggered.connect(lambda: self._fine_rotate_selected(15))
        edit_menu.addAction(fine_cw_act)

        fine_ccw_act = QAction("Fine Rotate CC&W (15\u00b0)", self)
        self._register_shortcut(fine_ccw_act, "edit.fine_ccw")
        fine_ccw_act.triggered.connect(lambda: self._fine_rotate_selected(-15))
        edit_menu.addAction(fine_ccw_act)

        # -- Flip --
        flip_h_act = QAction("Flip &Horizontal", self)
        self._register_shortcut(flip_h_act, "edit.flip_h")
        flip_h_act.triggered.connect(lambda: self._flip_selected(horizontal=True))
        edit_menu.addAction(flip_h_act)

        flip_v_act = QAction("Flip &Vertical", self)
        self._register_shortcut(flip_v_act, "edit.flip_v")
        flip_v_act.triggered.connect(lambda: self._flip_selected(horizontal=False))
        edit_menu.addAction(flip_v_act)

        edit_menu.addSeparator()

        edit_menu.addSeparator()

        # -- Alignment --
        align_h_act = QAction("Align Hori&zontally", self)
        self._register_shortcut(align_h_act, "edit.align_h")
        align_h_act.triggered.connect(lambda: self._align_selected("horizontal"))
        edit_menu.addAction(align_h_act)

        align_v_act = QAction("Align Vert&ically", self)
        self._register_shortcut(align_v_act, "edit.align_v")
        align_v_act.triggered.connect(lambda: self._align_selected("vertical"))
        edit_menu.addAction(align_v_act)

        edit_menu.addSeparator()

        hide_layer_act = QAction("&Hide Active Layer", self)
        self._register_shortcut(hide_layer_act, "edit.hide_layer")
        hide_layer_act.triggered.connect(self._hide_active_layer)
        edit_menu.addAction(hide_layer_act)

        show_layer_act = QAction("S&how Active Layer", self)
        self._register_shortcut(show_layer_act, "edit.show_layer")
        show_layer_act.triggered.connect(self._show_active_layer)
        edit_menu.addAction(show_layer_act)

        lock_layer_act = QAction("&Lock Active Layer", self)
        self._register_shortcut(lock_layer_act, "edit.lock_layer")
        lock_layer_act.triggered.connect(self._lock_active_layer)
        edit_menu.addAction(lock_layer_act)

        unlock_layer_act = QAction("&Unlock Active Layer", self)
        self._register_shortcut(unlock_layer_act, "edit.unlock_layer")
        unlock_layer_act.triggered.connect(self._unlock_active_layer)
        edit_menu.addAction(unlock_layer_act)

        edit_menu.addSeparator()

        bring_fwd_act = QAction("Bring For&ward", self)
        self._register_shortcut(bring_fwd_act, "edit.bring_fwd")
        bring_fwd_act.triggered.connect(self._bring_forward)
        edit_menu.addAction(bring_fwd_act)

        send_bwd_act = QAction("Send Back&ward", self)
        self._register_shortcut(send_bwd_act, "edit.send_bwd")
        send_bwd_act.triggered.connect(self._send_backward)
        edit_menu.addAction(send_bwd_act)

        bring_front_act = QAction("Bring to &Front", self)
        self._register_shortcut(bring_front_act, "edit.bring_front")
        bring_front_act.triggered.connect(self._bring_to_front)
        edit_menu.addAction(bring_front_act)

        send_back_act = QAction("Send to &Back", self)
        self._register_shortcut(send_back_act, "edit.send_back")
        send_back_act.triggered.connect(self._send_to_back)
        edit_menu.addAction(send_back_act)

        edit_menu.addSeparator()

        group_act = QAction("&Group", self)
        self._register_shortcut(group_act, "edit.group")
        group_act.triggered.connect(self._group_selected)
        edit_menu.addAction(group_act)

        ungroup_act = QAction("U&ngroup", self)
        self._register_shortcut(ungroup_act, "edit.ungroup")
        ungroup_act.triggered.connect(self._ungroup_selected)
        edit_menu.addAction(ungroup_act)

        join_wires_act = QAction("&Join Wires", self)
        self._register_shortcut(join_wires_act, "edit.join_wires")
        join_wires_act.triggered.connect(self._join_wires)
        edit_menu.addAction(join_wires_act)

        edit_menu.addSeparator()

        settings_act = QAction("Se&ttings\u2026", self)
        self._register_shortcut(settings_act, "edit.settings")
        settings_act.triggered.connect(self._open_settings)
        edit_menu.addAction(settings_act)

        # ---- View ----
        view_menu = menu_bar.addMenu("&View")

        zoom_in_act = QAction("Zoom &In", self)
        self._register_shortcut(zoom_in_act, "view.zoom_in")
        zoom_in_act.triggered.connect(lambda: self._zoom_active_view(1.25))
        view_menu.addAction(zoom_in_act)

        zoom_out_act = QAction("Zoom &Out", self)
        self._register_shortcut(zoom_out_act, "view.zoom_out")
        zoom_out_act.triggered.connect(lambda: self._zoom_active_view(0.8))
        view_menu.addAction(zoom_out_act)

        fit_act = QAction("Zoom &All / Fit", self)
        self._register_shortcut(fit_act, "view.fit_all", "view.fit_all2")
        fit_act.triggered.connect(self._fit_active_view)
        view_menu.addAction(fit_act)

        view_menu.addSeparator()

        toggle_grid_act = QAction("Show &Grid", self)
        toggle_grid_act.setCheckable(True)
        toggle_grid_act.setChecked(True)
        self._register_shortcut(toggle_grid_act, "view.toggle_grid")
        toggle_grid_act.toggled.connect(self._toggle_grid)
        view_menu.addAction(toggle_grid_act)

        view_menu.addSeparator()

        self._zoom_window_act = QAction("&Zoom Window", self)
        self._zoom_window_act.setCheckable(True)
        self._zoom_window_act.setShortcut(get_shortcut("view.zoom_window"))
        self._zoom_window_act.toggled.connect(self._toggle_zoom_window)
        view_menu.addAction(self._zoom_window_act)

        view_menu.addSeparator()

        # ---- Appearance ----
        appearance_menu = view_menu.addMenu("&Appearance")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        for mode, label in (
            ("system", "&System"),
            ("light", "&Light"),
            ("dark", "&Dark"),
        ):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setData(mode)
            act.triggered.connect(
                lambda _checked=False, m=mode: self._on_theme_selected(m)
            )
            self._theme_group.addAction(act)
            appearance_menu.addAction(act)
            self._theme_actions[mode] = act
        current = app_settings.theme if app_settings.theme in self._theme_actions \
            else "system"
        self._theme_actions[current].setChecked(True)

        view_menu.addSeparator()

        close_tab_act = QAction("Close Tab", self)
        self._register_shortcut(close_tab_act, "view.close_tab")
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
        self._register_shortcut(draw_text_act, "draw.text")
        draw_text_act.triggered.connect(self._add_annotation)
        draw_menu.addAction(draw_text_act)

        # ---- Help ----
        help_menu = menu_bar.addMenu("&Help")

        help_act = QAction("&Help", self)
        self._register_shortcut(help_act, "help.help")
        help_act.triggered.connect(self._show_help)
        help_menu.addAction(help_act)

        tutorial_act = QAction("&Tutorial", self)
        tutorial_act.triggered.connect(self._show_tutorial)
        help_menu.addAction(tutorial_act)

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

    # ----------------------------------------------------------- shortcuts

    def _register_shortcut(self, action: QAction, *ids: str, extra=None) -> None:
        """Apply shortcut(s) by id and remember them for later refresh."""
        seqs = [get_shortcut(i) for i in ids]
        if extra:
            seqs.extend(extra)
        if len(seqs) == 1:
            action.setShortcut(seqs[0])
        else:
            action.setShortcuts(seqs)
        self._shortcut_actions.append((action, list(ids), list(extra or [])))

    def _refresh_action_shortcuts(self) -> None:
        """Re-apply shortcuts from the registry to all menu actions."""
        for action, ids, extra in self._shortcut_actions:
            seqs = [get_shortcut(i) for i in ids] + list(extra)
            if len(seqs) == 1:
                action.setShortcut(seqs[0])
            else:
                action.setShortcuts(seqs)
