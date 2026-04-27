"""MainWindow — the top-level application window for Diagrammer."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from diagrammer.canvas.grid import DEFAULT_GRID_SPACING
from diagrammer.canvas.scene import DiagramScene, InteractionMode
from diagrammer.canvas.view import DiagramView
from diagrammer.clipboard_ops import ClipboardMixin
from diagrammer.menu_ops import MenuMixin
from diagrammer.models.library import ComponentLibrary
from diagrammer.panels.library_panel import LibraryPanel
from diagrammer.panels.settings_dialog import SettingsDialog, app_settings
from diagrammer.transform_ops import TransformMixin

logger = logging.getLogger(__name__)


def _find_builtin_components() -> Path:
    """Locate the built-in components directory shipped with the package.

    Components are stored inside the package at diagrammer/components/,
    so the same path works in both development and installed modes.
    """
    here = Path(__file__).resolve().parent
    return here / "components"


class MainWindow(MenuMixin, ClipboardMixin, TransformMixin, QMainWindow):
    """Top-level window containing the diagram canvas, menus, and panels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Diagrammer")
        self.resize(1200, 800)

        # Action → list of shortcut ids (populated as menus are built;
        # used to re-apply shortcuts when the user customizes them)
        self._shortcut_actions: list[tuple[QAction, list[str], list[QKeySequence]]] = []

        # -- Component library --
        self._library = ComponentLibrary()
        self._builtin_components_path = _find_builtin_components()
        self._rebuild_library()
        # On a fresh install, restrict the visible libraries to a small
        # curated default set so new users aren't overwhelmed.
        app_settings.apply_first_launch_library_defaults(self._library)
        # Load individually-registered user compounds (saved via
        # "Create Component from Selection"). Drop any whose file is gone.
        surviving: list[str] = []
        for f in app_settings.user_compound_files:
            fp = Path(f)
            if fp.is_file():
                self._library.add_file(fp)
                surviving.append(f)
        if len(surviving) != len(app_settings.user_compound_files):
            app_settings.user_compound_files = surviving
            app_settings.save()

        # -- Scene and View --
        self._scene = DiagramScene(library=self._library, parent=self)
        self._view = DiagramView(self._scene, self)

        # -- Tab widget (diagram tab + closable library table tabs) --
        self._tab_widget = QTabWidget(self)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.setMovable(False)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        # Wrap canvas with mode label above it (right-aligned)
        self._mode_label = QLabel("Mode: Select")
        self._mode_label.setStyleSheet("font-weight: bold;")
        diagram_container = QWidget()
        diagram_layout = QVBoxLayout(diagram_container)
        diagram_layout.setContentsMargins(0, 0, 0, 0)
        diagram_layout.setSpacing(0)
        mode_bar = QHBoxLayout()
        mode_bar.setContentsMargins(0, 2, 6, 2)
        mode_bar.addStretch()
        mode_bar.addWidget(self._mode_label)
        diagram_layout.addLayout(mode_bar)
        diagram_layout.addWidget(self._view)
        self._tab_widget.addTab(diagram_container, "Diagram")
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
        self.statusBar().addWidget(self._pos_label)

        self._scene.cursor_scene_pos_changed.connect(self._update_pos_label)
        self._scene.mode_changed.connect(self._update_mode_label)

        # -- Menus --
        self._create_menus()

        # -- Toolbar --
        self._create_toolbar()

        # -- Panels --
        self._create_panels()

        # -- Pin canvas + library to a light background regardless of
        # the current chrome theme, so grid and component artwork stay
        # legible in dark mode.
        self._reassert_light_surfaces()

        # -- Restore state from settings --
        self._restore_from_settings()

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
        self._layers_panel.layers_reordered.connect(self._scene.remap_layer_swap)
        self._layers_panel.layer_removed.connect(self._scene.remap_layer_removed)
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

        # Collect component defs for this category and subcategories,
        # skipping any categories the user has hidden in Settings.
        hidden = app_settings.hidden_libraries
        defs_by_subcat: dict[str, list] = {}
        if category == "__all__":
            for cat, cat_defs in self._library.categories.items():
                if cat not in hidden:
                    defs_by_subcat[cat] = cat_defs
        else:
            for cat, cat_defs in self._library.categories.items():
                if cat in hidden:
                    continue
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

    def _on_theme_selected(self, mode: str) -> None:
        """Handle Appearance submenu selection: live-switch the theme."""
        from PySide6.QtWidgets import QApplication
        from diagrammer.app import apply_theme

        app = QApplication.instance()
        if app is None:
            return
        apply_theme(app, mode)
        app_settings.theme = mode
        app_settings.save()
        # The canvas and library panel are pinned to a light background
        # regardless of theme; re-assert that pin so a style change
        # can't leak a dark Base palette into their viewports.
        self._reassert_light_surfaces()

    def _reassert_light_surfaces(self) -> None:
        """Re-pin canvas + library panel to the light surface color.

        Called after a live theme switch. Qt propagates the new app
        palette to every widget, but we want the diagram canvas and the
        library dock to stay on a white background so the grid and
        component artwork remain legible.
        """
        from PySide6.QtGui import QBrush
        bg = app_settings.current_background_color()
        try:
            self._view.setBackgroundBrush(QBrush(bg))
            self._view.viewport().update()
        except Exception:
            logger.debug("Failed to reassert canvas background brush", exc_info=True)
        try:
            self._library_panel.apply_light_surface(bg)
        except Exception:
            logger.debug("Failed to reassert library panel light surface", exc_info=True)
        try:
            self._annotations_panel.apply_light_surface(bg)
        except Exception:
            logger.debug("Failed to reassert annotations panel light surface", exc_info=True)

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

    def _rebuild_library(self) -> None:
        """Wipe and re-scan: built-ins + enabled custom paths + user compounds."""
        self._library.clear()
        if self._builtin_components_path.is_dir():
            self._library.scan(self._builtin_components_path)
        for custom_path in app_settings.enabled_custom_library_paths():
            p = Path(custom_path)
            if p.is_dir():
                self._library.scan(p)
        # Individually-registered user compounds
        surviving: list[str] = []
        for f in app_settings.user_compound_files:
            fp = Path(f)
            if fp.is_file():
                self._library.add_file(fp)
                surviving.append(f)
        if len(surviving) != len(app_settings.user_compound_files):
            app_settings.user_compound_files = surviving
            app_settings.save()

    def _refresh_libraries(self) -> None:
        """Re-scan SVG component sources and refresh the library panel.

        Existing canvas items keep their current ComponentDef references,
        so open diagrams are unaffected. Only newly placed components
        pick up the freshly-scanned artwork.
        """
        self._rebuild_library()
        self._library_panel._refresh_views()
        count = len(self._library.all_defs())
        self.statusBar().showMessage(
            f"Libraries refreshed — {count} components loaded", 3000
        )

    def _is_under_builtin(self, folder: Path) -> bool:
        """True if ``folder`` is the built-in components dir or a subdir."""
        if not self._builtin_components_path.is_dir():
            return False
        try:
            target = folder.resolve()
            base = self._builtin_components_path.resolve()
        except OSError:
            return False
        if target == base:
            return True
        try:
            target.relative_to(base)
            return True
        except ValueError:
            return False

    def _find_custom_library_entry(self, folder: Path) -> dict | None:
        """Return the ``custom_library_paths`` entry whose path equals
        ``folder`` or is an ancestor of it, or None."""
        try:
            target = folder.resolve()
        except OSError:
            target = folder
        for entry in app_settings.custom_library_paths:
            p = Path(entry.get("path", ""))
            if not str(p):
                continue
            try:
                cp = p.resolve()
            except OSError:
                cp = p
            if target == cp:
                return entry
            try:
                target.relative_to(cp)
                return entry
            except ValueError:
                continue
        return None

    def _offer_register_library_path(self, folder: Path) -> None:
        """Ask the user whether to add ``folder`` to the additional library
        paths and, if so, whether to enable it immediately. The compound
        file has already been written to disk; this only governs visibility
        in the Library panel."""
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Add Library Path?",
            f"The folder\n\n{folder}\n\nis not in your library paths.\n\n"
            "Add it as an additional library path?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            QMessageBox.information(
                self,
                "Component Saved",
                "The component was saved. You can register this folder "
                "later via Settings → Libraries.",
            )
            return

        enable_reply = QMessageBox.question(
            self,
            "Enable Library?",
            "Enable this library now so its components are visible in the "
            "Library panel?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        enabled = enable_reply == QMessageBox.StandardButton.Yes

        app_settings.custom_library_paths.append(
            {"path": str(folder), "enabled": enabled}
        )
        app_settings.save()
        if enabled:
            self._rebuild_library()

    def _offer_enable_existing_path(self, entry: dict) -> None:
        """The compound was saved into a folder already covered by a
        registered custom library path that is currently disabled. Offer
        to turn it on."""
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Enable Library?",
            f"The library path\n\n{entry.get('path', '')}\n\n"
            "is registered but currently disabled. Enable it now so the "
            "new component is visible in the Library panel?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            entry["enabled"] = True
            app_settings.save()
            self._rebuild_library()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(app_settings, self, library=self._library)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            dlg.apply()
            self._apply_settings()
            self._reassert_light_surfaces()
            # If the user changed the LaTeX bin path, force the next math
            # render to re-probe so the new path takes effect immediately.
            from diagrammer.items.annotation_item import (
                invalidate_latex_availability_cache,
            )
            invalidate_latex_availability_cache()
            # Rebuild library from scratch (handles add/remove + enable/disable)
            self._rebuild_library()
            self._apply_library_visibility()
            # Re-apply keyboard shortcuts (may have been customized)
            self._refresh_action_shortcuts()
            # Refresh help window if open so the live shortcut table updates
            try:
                from diagrammer.panels.help_window import HelpWindow
                for inst in HelpWindow._instances.values():
                    if inst.isVisible():
                        inst._load_help()
            except Exception:
                logger.debug("Failed to refresh open help windows after settings change", exc_info=True)

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
        from diagrammer.items.connection_item import ConnectionItem
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
            cmd.connection.routing_mode = wire_a.routing_mode
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
        mode = ROUTE_ORTHO_45 if enabled else ROUTE_ORTHO
        self._scene.default_routing_mode = mode
        app_settings.default_routing_mode = mode
        app_settings.discrete_angle_routing = enabled
        app_settings.save()

    def _restore_from_settings(self) -> None:
        """Restore UI state from persisted settings on startup."""
        # Block signals on toggle actions to prevent their handlers from
        # firing (and calling app_settings.save()) during restoration.
        toggle_acts = [self._snap_grid_act, self._snap_port_act,
                       self._snap_angle_act, self._discrete_angle_act]
        for act in toggle_acts:
            act.blockSignals(True)

        # Snap to grid
        self._view.snap_enabled = app_settings.snap_to_grid
        self._snap_grid_act.setChecked(app_settings.snap_to_grid)

        # Snap to port
        self._snap_port_act.setChecked(app_settings.snap_to_port)

        # Snap to angle
        self._snap_angle_act.setChecked(app_settings.snap_to_angle)

        # Discrete angle routing
        self._discrete_angle_act.setChecked(app_settings.discrete_angle_routing)

        # Default routing mode (sync scene with persisted setting)
        self._scene.default_routing_mode = app_settings.default_routing_mode

        for act in toggle_acts:
            act.blockSignals(False)

        # Library view mode
        self._library_panel._set_view_mode(app_settings.library_view_mode)
        if app_settings.library_view_mode == "grid":
            self._library_panel._grid_btn.setChecked(True)
            self._library_panel._tree_btn.setChecked(False)

        # Library visibility
        self._apply_library_visibility()

    # ------------------------------------------------------------ Selection

    def _select_all(self) -> None:
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        from diagrammer.items.shape_item import LineItem, ShapeItem
        from diagrammer.items.svg_image_item import SvgImageItem
        for item in self._scene.items():
            if isinstance(item, (ComponentItem, ConnectionItem, JunctionItem, ShapeItem, LineItem, AnnotationItem, SvgImageItem)):
                if item.flags() & item.GraphicsItemFlag.ItemIsSelectable:
                    item.setSelected(True)

    # NOTE: _rotate_closed_polygons, _rotate_selected, _fine_rotate_selected,
    # _flip_selected, and _align_selected are provided by TransformMixin.
    # _delete_selected, _gather_connected_junctions, _copy, _cut,
    # _try_paste_external_svg, and _paste are provided by ClipboardMixin.
    # _create_menus, _create_toolbar, _register_shortcut, and
    # _refresh_action_shortcuts are provided by MenuMixin.

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
        self._scene.assign_active_layer(item)
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
            fallback_keys = DiagramSerializer.load(self._scene, restore_path, library=self._library)
            self._warn_embedded_components(fallback_keys)
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
            logger.debug("Failed to autosave on close", exc_info=True)
        super().closeEvent(event)

    def _show_help(self) -> None:
        from diagrammer.panels.help_window import HelpWindow
        HelpWindow.show_help(self)

    def _show_tutorial(self) -> None:
        from diagrammer.panels.help_window import HelpWindow
        HelpWindow.show_tutorial(self)

    def _show_about(self) -> None:
        import sys

        import PySide6
        from PySide6.QtCore import qVersion
        from PySide6.QtWidgets import QMessageBox

        from diagrammer import __version__

        py = ".".join(str(p) for p in sys.version_info[:3])
        text = (
            f"<h3>Diagrammer {__version__}</h3>"
            "<p>A tool for building diagrams, including flowcharts, "
            "circuits, and more.</p>"
            f"<p><small>PySide6 {PySide6.__version__} &middot; "
            f"Qt {qVersion()} &middot; Python {py}</small></p>"
            "<p><small>&copy; J Aumentado &middot; "
            "<a href='https://github.com/w00ber/Diagrammer'>"
            "github.com/w00ber/Diagrammer</a></small></p>"
        )
        QMessageBox.about(self, "About Diagrammer", text)

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
        fallback_keys = DiagramSerializer.load(self._scene, path, library=self._library)
        self._warn_embedded_components(fallback_keys)
        self._layers_panel._manager = self._scene._layer_manager
        self._layers_panel.refresh()
        self._scene.apply_layer_state()
        self._current_file = path
        self._update_title()
        self._track_file(path)

    def _warn_embedded_components(self, fallback_keys: list[str]) -> None:
        """Show a warning if any components were loaded from embedded data."""
        if not fallback_keys:
            return
        from PySide6.QtWidgets import QMessageBox
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for k in fallback_keys:
            if k not in seen:
                seen.add(k)
                unique.append(k)
        keys_text = "\n".join(f"  \u2022 {k}" for k in unique)
        QMessageBox.warning(
            self,
            "Missing Library Components",
            f"The following components were not found in the current "
            f"library and were reconstructed from embedded data:\n\n"
            f"{keys_text}\n\n"
            f"These components will not receive library updates.",
        )

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

    def _file_open_example(self) -> None:
        """Show the Examples dialog and load the chosen example as Untitled."""
        if not self._check_unsaved_changes():
            return
        from diagrammer.panels.examples_dialog import ExamplesDialog
        dlg = ExamplesDialog(self._library, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        path = dlg.selected_path
        if path is None:
            return
        from diagrammer.io.serializer import DiagramSerializer
        self._scene.clear()
        self._scene.undo_stack.clear()
        try:
            fallback_keys = DiagramSerializer.load(self._scene, str(path), library=self._library)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Open Example", f"Failed to open example:\n\n{exc}")
            return
        self._warn_embedded_components(fallback_keys)
        self._layers_panel._manager = self._scene._layer_manager
        self._layers_panel.refresh()
        self._scene.apply_layer_state()
        # Open as untitled — Save will become Save As, protecting the bundled file.
        self._current_file = None
        self._update_title()
        # Mark scene as dirty so the user is prompted to save before discarding.
        # (load() leaves the undo stack clean, but the example is unsaved work
        # from the user's perspective the moment they pick it.)
        # We deliberately do NOT call _track_file — examples shouldn't pollute
        # the Recent Files list.

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

    def _import_svg_image(self) -> None:
        """Import an arbitrary SVG file as a resizable image on the canvas."""
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Import SVG Image", self._last_dir(),
            "SVG Files (*.svg);;All Files (*)",
        )
        if not path:
            return
        svg_data = Path(path).read_bytes()
        if not svg_data:
            return
        from diagrammer.commands.shape_command import AddShapeCommand
        from diagrammer.items.svg_image_item import SvgImageItem
        item = SvgImageItem(svg_data)
        # Place at center of current view
        view = self._view
        center = view.mapToScene(view.viewport().rect().center())
        cmd = AddShapeCommand(self._scene, item, center)
        self._scene.undo_stack.push(cmd)
        # Select the newly placed item
        self._scene.clearSelection()
        item.setSelected(True)
        app_settings.last_directory = str(Path(path).parent)
        app_settings.save()
        self.statusBar().showMessage(f"Imported SVG: {Path(path).name}", 5000)

    def _create_component_from_selection(self) -> None:
        """Export selected items as a reusable component in the library.

        The user picks one of two formats:

        * **Flattened SVG** — bakes the current geometry into a single static
          SVG. Smallest, most portable, and renders identically anywhere; but
          stretchable sub-components lose their stretchability and the
          placement is a single opaque item that can't be ungrouped.

        * **Structural compound (.dgmcomp + preview .svg)** — writes a JSON
          manifest of the sub-items alongside a preview SVG. Stretchable
          sub-components stay stretchable, individual styles round-trip,
          and the placement can be ungrouped to edit pieces. Requires the
          original sub-components' library entries to be present on the
          machine where the file is opened.
        """
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QLabel,
            QMessageBox, QRadioButton, QVBoxLayout,
        )

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

        # Ask which format to save in
        fmt_dlg = QDialog(self)
        fmt_dlg.setWindowTitle("Save Compound — Choose Format")
        fmt_layout = QVBoxLayout(fmt_dlg)
        intro = QLabel(
            "Save '<b>{n}</b>' as:".format(n=name))
        intro.setTextFormat(Qt.TextFormat.RichText)
        fmt_layout.addWidget(intro)

        rb_compound = QRadioButton("Structural compound (.dgmcomp + preview .svg)")
        rb_compound.setChecked(True)
        fmt_layout.addWidget(rb_compound)
        compound_caveats = QLabel(
            "  • Stretchable sub-components stay stretchable.\n"
            "  • Per-instance styles round-trip exactly.\n"
            "  • Placement can be ungrouped to edit individual pieces.\n"
            "  • Requires the original sub-component libraries on any\n"
            "    machine where the file is opened."
        )
        from diagrammer.app import hint_text_color as _htc
        compound_caveats.setStyleSheet(
            f"color: {_htc()}; margin-left: 18px;")
        fmt_layout.addWidget(compound_caveats)

        rb_svg = QRadioButton("Flattened SVG (single static file)")
        fmt_layout.addWidget(rb_svg)
        svg_caveats = QLabel(
            "  • Self-contained, smallest, most portable.\n"
            "  • Renders identically anywhere.\n"
            "  • Sub-components lose their stretchability.\n"
            "  • Placement is a single opaque item — cannot be ungrouped\n"
            "    or edited piece-by-piece."
        )
        svg_caveats.setStyleSheet(f"color: {_htc()}; margin-left: 18px;")
        fmt_layout.addWidget(svg_caveats)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(fmt_dlg.accept)
        btns.rejected.connect(fmt_dlg.reject)
        fmt_layout.addWidget(btns)

        if fmt_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        write_manifest = rb_compound.isChecked()

        # Default save location: a writable user directory, NOT the builtin
        # components folder. On macOS the package is shipped inside a signed
        # .app bundle, so its components/ subdir lives under
        # Foo.app/Contents/Resources/... which is read-only — opening the
        # save dialog there causes "New Folder" to silently fail and the
        # save panel treats the .app as an opaque package, blocking outward
        # navigation. Use the user's home library dir instead.
        comp_dir = Path(app_settings.last_directory) if app_settings.last_directory else Path()
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
        from diagrammer.io.compound_manifest import save_compound_manifest
        # Both formats need the preview SVG (the library scans .svg files
        # and the panel uses it for the thumbnail). The structural manifest
        # is the optional sidecar that triggers the manifest-based drop
        # path; without it, drops fall back to placing the static SVG.
        success = export_compound_component(
            self._scene, selected, Path(path), name
        )
        if success and write_manifest:
            manifest_path = Path(path).with_suffix(".dgmcomp")
            save_compound_manifest(self._scene, selected, manifest_path, name)
        elif success and not write_manifest:
            # User explicitly chose flat SVG. If a stale manifest from a
            # previous save exists at this path, remove it so the drop
            # handler doesn't keep using the old structural version.
            stale = Path(path).with_suffix(".dgmcomp")
            if stale.exists():
                try:
                    stale.unlink()
                except OSError:
                    pass
        if success:
            parent_dir = Path(path).parent
            app_settings.last_directory = str(parent_dir)
            # The compound has been written to disk regardless of what
            # follows. Now decide whether to surface it in the Library
            # panel — by registering the parent folder as a library path
            # (if it isn't already), or by enabling an existing-but-off
            # custom path that already covers it.
            if app_settings.auto_add_saved_compounds:
                if self._is_under_builtin(parent_dir):
                    pass  # already visible via the built-in scan
                else:
                    entry = self._find_custom_library_entry(parent_dir)
                    if entry is None:
                        self._offer_register_library_path(parent_dir)
                    elif not entry.get("enabled", True):
                        self._offer_enable_existing_path(entry)
            app_settings.save()
            # Hot-load the newly written SVG so it appears immediately
            # without a full library rebuild.
            self._library.add_file(Path(path))
            self._library_panel._refresh_views()
            QMessageBox.information(self, "Component Created",
                                   f"'{name}' saved to:\n{path}")
        else:
            QMessageBox.warning(self, "Export Failed",
                                "Could not create component from selection.")
