"""SettingsDialog — application-wide style and behavior settings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


_SETTINGS_FILE = Path.home() / ".diagrammer" / "settings.json"


class AppSettings:
    """Central store for application-wide default settings, persisted to disk."""

    def __init__(self) -> None:
        self.reset_all()
        self.load()

    def reset_all(self) -> None:
        """Reset all settings to factory defaults from defaults.yaml."""
        from diagrammer.defaults import get as _d

        # Appearance / theme
        # One of: "system", "light", "dark". "system" lets the OS style
        # decide (native look, honors Windows dark mode); "light"/"dark"
        # force a Fusion palette in that mode. The canvas and library
        # panel always render on a light background regardless of the
        # chrome theme, because the diagram artwork is authored that way.
        self.theme: str = "system"

        # Line styles (wiring)
        self.default_line_width = _d("wiring", "line_width", 3.0)
        self.default_line_color = QColor(_d("wiring", "line_color", "#323232"))
        self.default_corner_radius = _d("wiring", "corner_radius", 8.0)
        self.default_routing_mode = _d("wiring", "routing_mode", "ortho")

        # Snap behavior
        self.snap_to_port = _d("snap", "snap_to_port", True)
        self.snap_to_angle = _d("snap", "snap_to_angle", False)
        self.snap_to_grid = _d("snap", "snap_to_grid", True)
        self.angle_snap_increment = _d("snap", "angle_snap_increment", 15.0)
        # Arrow-key nudge step as a fraction of the current grid spacing.
        # Shift+Arrow uses half this value for fine nudge.
        self.nudge_fraction = _d("snap", "nudge_fraction", 0.2)

        # Junction appearance
        self.show_junctions = _d("junction", "show", True)
        self.junction_color = QColor(_d("junction", "color", "#000000"))
        self.junction_outline = _d("junction", "outline", False)
        self.junction_radius = _d("junction", "radius", 5.0)

        # Routing menu state
        self.discrete_angle_routing = _d("routing", "discrete_angle", False)

        # Component styles
        self.default_component_fill = QColor(255, 255, 255, 0)

        # Annotation defaults
        self.default_annotation_font = _d("annotation", "font_family", "STIX Two Text")
        self.default_annotation_size = _d("annotation", "font_size", 12.0)
        self.default_annotation_color = QColor(_d("annotation", "text_color", "#000000"))
        self.default_annotation_bold = _d("annotation", "bold", False)
        self.default_annotation_italic = _d("annotation", "italic", False)

        # Shape/line annotation defaults
        self.default_shape_stroke_color = QColor(_d("shape", "stroke_color", "#323232"))
        self.default_shape_stroke_width = _d("shape", "stroke_width", 2.0)
        self.default_shape_fill_color = QColor(_d("shape", "fill_color", "#00ffffff"))
        self.default_shape_dash_style = _d("shape", "dash_style", "solid")
        self.default_shape_corner_radius = _d("shape", "corner_radius", 0.0)
        self.default_shape_cap_style = _d("shape", "cap_style", "round")
        self.default_shape_arrow_type = _d("shape", "arrow_type", "triangle")
        self.default_shape_arrow_scale = _d("shape", "arrow_scale", 1.0)
        self.default_shape_arrow_extend = _d("shape", "arrow_extend", 0.0)

        # Library
        self.hidden_libraries: set[str] = set()
        self.library_view_mode = "tree"  # "tree" or "grid"
        # Additional library directories. Each entry: {"path": str, "enabled": bool}.
        self.custom_library_paths: list[dict] = []

        # Keyboard shortcut user overrides: {action_id: portable_key_string}
        self.keyboard_shortcuts: dict[str, str] = {}

        # Library tab UI state: parent group names whose child list is expanded.
        # Default empty = everything collapsed on first run.
        self.library_tab_expanded: list[str] = []

        # Session
        self.last_opened_file: str = ""
        self.last_directory: str = ""
        self.recent_files: list[str] = []

        # User-saved compounds: when enabled, every compound saved via
        # "Create Component from Selection" is automatically registered with
        # the library. The list persists the actual file paths so they
        # reappear on next launch.
        self.auto_add_saved_compounds: bool = True
        self.user_compound_files: list[str] = []

        # Math rendering: when True, ``$$...$$`` display-math annotations
        # are rendered through matplotlib's ``usetex`` mode (requires a
        # system LaTeX install) in preference to the pure-Python ziamath
        # backend. Useful when ziamath's bracket sizing or layout falls
        # short and the user has MacTeX/TeX Live available.
        # Default to True so users with a system LaTeX install get the
        # highest-quality output (real TeX typesetting) without having
        # to discover the setting. If LaTeX isn't available the renderer
        # in annotation_item.py automatically falls back to ziamath /
        # mathtext, so this default is safe even on machines without
        # MacTeX/TeX Live.
        self.prefer_system_latex_for_math: bool = True
        # Optional explicit path to the directory containing the ``latex``
        # binary (and friends like ``dvips``, ``gs``). Needed on macOS
        # when launching from a .app bundle, because GUI processes don't
        # inherit the shell PATH where ``/Library/TeX/texbin`` is added.
        # Empty string means "use the inherited PATH".
        self.latex_bin_path: str = ""
        # Matrix typography knobs (LaTeX usetex path only). These map to
        # \arraycolsep (inter-column spacing inside bmatrix/pmatrix/array)
        # and \arraystretch (row-height multiplier). The LaTeX defaults
        # are 5pt and 1.0 respectively — both look cramped through
        # matplotlib's tight-bbox cropping.
        self.latex_arraycolsep_pt: float = 48.0
        self.latex_arraystretch: float = 1.15

    def save(self) -> None:
        """Persist settings to disk."""
        try:
            # Pull live keyboard overrides from the registry
            from diagrammer import shortcuts as _sc
            self.keyboard_shortcuts = _sc.dump_user_overrides()
            _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "default_line_width": self.default_line_width,
                "default_line_color": self.default_line_color.name(),
                "default_corner_radius": self.default_corner_radius,
                "default_routing_mode": self.default_routing_mode,
                "snap_to_port": self.snap_to_port,
                "snap_to_angle": self.snap_to_angle,
                "snap_to_grid": self.snap_to_grid,
                "angle_snap_increment": self.angle_snap_increment,
                "nudge_fraction": self.nudge_fraction,
                "show_junctions": self.show_junctions,
                "junction_color": self.junction_color.name(),
                "junction_outline": self.junction_outline,
                "junction_radius": self.junction_radius,
                "discrete_angle_routing": self.discrete_angle_routing,
                "hidden_libraries": sorted(self.hidden_libraries),
                "library_view_mode": self.library_view_mode,
                "custom_library_paths": self.custom_library_paths,
                "keyboard_shortcuts": self.keyboard_shortcuts,
                "library_tab_expanded": self.library_tab_expanded,
                "last_opened_file": self.last_opened_file,
                "last_directory": self.last_directory,
                "recent_files": self.recent_files[:10],
                "auto_add_saved_compounds": self.auto_add_saved_compounds,
                "user_compound_files": self.user_compound_files,
                "prefer_system_latex_for_math": self.prefer_system_latex_for_math,
                "latex_bin_path": self.latex_bin_path,
                "latex_arraycolsep_pt": self.latex_arraycolsep_pt,
                "latex_arraystretch": self.latex_arraystretch,
                "default_annotation_font": self.default_annotation_font,
                "default_annotation_size": self.default_annotation_size,
                "default_annotation_color": self.default_annotation_color.name(),
                "default_annotation_bold": self.default_annotation_bold,
                "default_annotation_italic": self.default_annotation_italic,
                "default_shape_stroke_color": self.default_shape_stroke_color.name(),
                "default_shape_stroke_width": self.default_shape_stroke_width,
                "default_shape_fill_color": self.default_shape_fill_color.name(QColor.NameFormat.HexArgb),
                "default_shape_dash_style": self.default_shape_dash_style,
                "default_shape_corner_radius": self.default_shape_corner_radius,
                "default_shape_cap_style": self.default_shape_cap_style,
                "default_shape_arrow_type": self.default_shape_arrow_type,
                "default_shape_arrow_scale": self.default_shape_arrow_scale,
                "default_shape_arrow_extend": self.default_shape_arrow_extend,
                "theme": self.theme,
            }
            import json
            _SETTINGS_FILE.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def load(self) -> None:
        """Load settings from disk if available."""
        try:
            if _SETTINGS_FILE.exists():
                import json
                data = json.loads(_SETTINGS_FILE.read_text())
                self.default_line_width = data.get("default_line_width", self.default_line_width)
                color = data.get("default_line_color")
                if color:
                    self.default_line_color = QColor(color)
                self.default_corner_radius = data.get("default_corner_radius", self.default_corner_radius)
                self.default_routing_mode = data.get("default_routing_mode", self.default_routing_mode)
                self.snap_to_port = data.get("snap_to_port", self.snap_to_port)
                self.snap_to_angle = data.get("snap_to_angle", self.snap_to_angle)
                self.snap_to_grid = data.get("snap_to_grid", self.snap_to_grid)
                self.angle_snap_increment = data.get("angle_snap_increment", self.angle_snap_increment)
                self.nudge_fraction = data.get("nudge_fraction", self.nudge_fraction)
                self.show_junctions = data.get("show_junctions", self.show_junctions)
                jc = data.get("junction_color")
                if jc:
                    self.junction_color = QColor(jc)
                self.junction_outline = data.get("junction_outline", self.junction_outline)
                self.junction_radius = data.get("junction_radius", self.junction_radius)
                self.discrete_angle_routing = data.get("discrete_angle_routing", self.discrete_angle_routing)
                self.hidden_libraries = set(data.get("hidden_libraries", []))
                self.library_view_mode = data.get("library_view_mode", self.library_view_mode)
                raw_paths = data.get("custom_library_paths", [])
                migrated: list[dict] = []
                for entry in raw_paths:
                    if isinstance(entry, str):
                        migrated.append({"path": entry, "enabled": True})
                    elif isinstance(entry, dict) and "path" in entry:
                        migrated.append({
                            "path": entry["path"],
                            "enabled": bool(entry.get("enabled", True)),
                        })
                self.custom_library_paths = migrated
                # Keyboard shortcut overrides
                self.keyboard_shortcuts = dict(data.get("keyboard_shortcuts", {}))
                self.library_tab_expanded = list(
                    data.get("library_tab_expanded", []))
                from diagrammer import shortcuts as _sc
                _sc.reset_all_overrides()
                _sc.load_user_overrides(self.keyboard_shortcuts)
                self.last_opened_file = data.get("last_opened_file", "")
                self.last_directory = data.get("last_directory", "")
                self.recent_files = data.get("recent_files", [])[:10]
                self.auto_add_saved_compounds = data.get(
                    "auto_add_saved_compounds", self.auto_add_saved_compounds)
                self.user_compound_files = data.get("user_compound_files", [])
                self.prefer_system_latex_for_math = data.get(
                    "prefer_system_latex_for_math",
                    self.prefer_system_latex_for_math)
                self.latex_bin_path = data.get(
                    "latex_bin_path", self.latex_bin_path)
                self.latex_arraycolsep_pt = data.get(
                    "latex_arraycolsep_pt", self.latex_arraycolsep_pt)
                self.latex_arraystretch = data.get(
                    "latex_arraystretch", self.latex_arraystretch)
                self.default_annotation_font = data.get("default_annotation_font", self.default_annotation_font)
                self.default_annotation_size = data.get("default_annotation_size", self.default_annotation_size)
                ac = data.get("default_annotation_color")
                if ac:
                    self.default_annotation_color = QColor(ac)
                self.default_annotation_bold = data.get("default_annotation_bold", self.default_annotation_bold)
                self.default_annotation_italic = data.get("default_annotation_italic", self.default_annotation_italic)
                sc = data.get("default_shape_stroke_color")
                if sc:
                    self.default_shape_stroke_color = QColor(sc)
                self.default_shape_stroke_width = data.get("default_shape_stroke_width", self.default_shape_stroke_width)
                fc = data.get("default_shape_fill_color")
                if fc:
                    self.default_shape_fill_color = QColor(fc)
                self.default_shape_dash_style = data.get("default_shape_dash_style", self.default_shape_dash_style)
                self.default_shape_corner_radius = data.get("default_shape_corner_radius", self.default_shape_corner_radius)
                self.default_shape_cap_style = data.get("default_shape_cap_style", self.default_shape_cap_style)
                self.default_shape_arrow_type = data.get("default_shape_arrow_type", self.default_shape_arrow_type)
                self.default_shape_arrow_scale = data.get("default_shape_arrow_scale", self.default_shape_arrow_scale)
                self.default_shape_arrow_extend = data.get("default_shape_arrow_extend", self.default_shape_arrow_extend)
                theme = data.get("theme", self.theme)
                if theme in ("system", "light", "dark"):
                    self.theme = theme
        except Exception:
            pass


    def enabled_custom_library_paths(self) -> list[str]:
        """Return only the custom library paths the user has enabled."""
        return [
            entry["path"]
            for entry in self.custom_library_paths
            if entry.get("enabled", True)
        ]


# Singleton instance
app_settings = AppSettings()


class SettingsDialog(QDialog):
    """Dialog for editing application-wide style, snap, and library settings."""

    def __init__(self, settings: AppSettings, parent=None, library=None):
        super().__init__(parent)
        self._settings = settings
        self._library = library
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        def _scrollable(content: QWidget) -> QScrollArea:
            # Wrap a tab's content in a scroll area so that tabs with a lot
            # of widgets (notably the library category list) don't force the
            # whole dialog's minimum size to match their natural height,
            # which otherwise makes the dialog fill the screen vertically
            # with no way to shrink it.
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QScrollArea.Shape.NoFrame)
            sa.setWidget(content)
            return sa

        # -- Line Styles tab --
        line_tab = QWidget()
        line_layout = QVBoxLayout(line_tab)

        line_group = QGroupBox("Connection Line Defaults")
        line_form = QFormLayout()

        self._line_width_spin = QDoubleSpinBox()
        self._line_width_spin.setRange(0.5, 20.0)
        self._line_width_spin.setValue(settings.default_line_width)
        self._line_width_spin.setSuffix(" pt")
        self._line_width_spin.setSingleStep(0.5)
        line_form.addRow("Line width:", self._line_width_spin)

        self._line_color = QColor(settings.default_line_color)
        self._line_color_btn = QPushButton()
        self._line_color_btn.setFixedSize(60, 24)
        self._line_color_btn.setAutoDefault(False)
        self._update_color_btn(self._line_color_btn, self._line_color)
        self._line_color_btn.clicked.connect(self._pick_line_color)
        line_form.addRow("Line color:", self._line_color_btn)

        self._corner_radius_spin = QDoubleSpinBox()
        self._corner_radius_spin.setRange(0.0, 50.0)
        self._corner_radius_spin.setValue(settings.default_corner_radius)
        self._corner_radius_spin.setSuffix(" pt")
        self._corner_radius_spin.setSingleStep(1.0)
        line_form.addRow("Corner radius:", self._corner_radius_spin)

        line_group.setLayout(line_form)
        line_layout.addWidget(line_group)

        line_reset = QPushButton("Reset to Default")
        line_reset.setAutoDefault(False)
        line_reset.clicked.connect(self._reset_line_defaults)
        line_layout.addWidget(line_reset, alignment=Qt.AlignmentFlag.AlignRight)
        line_layout.addStretch()

        tabs.addTab(_scrollable(line_tab), "Line Styles")

        # -- Snap Behavior tab --
        snap_tab = QWidget()
        snap_layout = QVBoxLayout(snap_tab)

        snap_group = QGroupBox("Snap Options")
        snap_form = QFormLayout()

        self._snap_to_port_cb = QCheckBox("Snap to port when dragging connections")
        self._snap_to_port_cb.setChecked(settings.snap_to_port)
        snap_form.addRow(self._snap_to_port_cb)

        self._snap_to_angle_cb = QCheckBox("Snap to angle (0\u00b0/45\u00b0/90\u00b0) when grid snap is off")
        self._snap_to_angle_cb.setChecked(settings.snap_to_angle)
        snap_form.addRow(self._snap_to_angle_cb)

        self._angle_increment_spin = QDoubleSpinBox()
        self._angle_increment_spin.setRange(5.0, 90.0)
        self._angle_increment_spin.setValue(settings.angle_snap_increment)
        self._angle_increment_spin.setSuffix("\u00b0")
        self._angle_increment_spin.setSingleStep(5.0)
        snap_form.addRow("Angle increment:", self._angle_increment_spin)

        self._nudge_fraction_spin = QDoubleSpinBox()
        self._nudge_fraction_spin.setRange(0.01, 5.0)
        self._nudge_fraction_spin.setSingleStep(0.05)
        self._nudge_fraction_spin.setDecimals(2)
        self._nudge_fraction_spin.setValue(settings.nudge_fraction)
        self._nudge_fraction_spin.setToolTip(
            "Arrow-key nudge step as a fraction of the current grid spacing. "
            "Shift+Arrow uses half this value."
        )
        snap_form.addRow("Nudge step (× grid):", self._nudge_fraction_spin)

        snap_group.setLayout(snap_form)
        snap_layout.addWidget(snap_group)

        snap_reset = QPushButton("Reset to Default")
        snap_reset.setAutoDefault(False)
        snap_reset.clicked.connect(self._reset_snap_defaults)
        snap_layout.addWidget(snap_reset, alignment=Qt.AlignmentFlag.AlignRight)
        snap_layout.addStretch()

        tabs.addTab(_scrollable(snap_tab), "Snap Behavior")

        # -- Libraries tab --
        lib_tab = QWidget()
        lib_layout = QVBoxLayout(lib_tab)
        lib_group = QGroupBox("Visible Component Libraries")
        lib_form = QVBoxLayout()
        # Maps category name → QTreeWidgetItem (leaf items: actual categories;
        # parent items: synthetic group rows)
        self._lib_checkboxes: dict[str, QTreeWidgetItem] = {}
        self._parent_checkboxes: dict[str, QTreeWidgetItem] = {}
        if library:
            # Group categories by parent
            parents: dict[str, list[str]] = {}
            top_level: list[str] = []
            for cat in sorted(library.categories.keys()):
                parts = cat.split("/")
                if len(parts) > 1:
                    parent = parts[0]
                    parents.setdefault(parent, []).append(cat)
                else:
                    top_level.append(cat)

            # Native QTreeWidget with checkable items + auto tri-state parents
            self._lib_tree = QTreeWidget()
            self._lib_tree.setHeaderHidden(True)
            self._lib_tree.setRootIsDecorated(True)
            self._lib_tree.setUniformRowHeights(True)
            self._lib_tree.setMinimumHeight(220)
            lib_form.addWidget(self._lib_tree)

            expanded_set = set(settings.library_tab_expanded)

            def _make_checkable(item: QTreeWidgetItem, checked: bool) -> None:
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked if checked
                    else Qt.CheckState.Unchecked,
                )

            # Top-level categories (no children) — leaf items at root
            for cat in top_level:
                item = QTreeWidgetItem(self._lib_tree)
                item.setText(0, cat.replace("_", " ").title())
                _make_checkable(item, cat not in settings.hidden_libraries)
                self._lib_checkboxes[cat] = item

            # Parent groups with children — tri-state via ItemIsAutoTristate
            for parent, children in sorted(parents.items()):
                parent_item = QTreeWidgetItem(self._lib_tree)
                parent_item.setText(0, parent.replace("_", " ").title())
                font = parent_item.font(0)
                font.setBold(True)
                parent_item.setFont(0, font)
                parent_item.setFlags(
                    parent_item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsAutoTristate
                )
                self._parent_checkboxes[parent] = parent_item

                for cat in children:
                    display = cat.split("/")[-1].replace("_", " ").title()
                    child_item = QTreeWidgetItem(parent_item)
                    child_item.setText(0, display)
                    _make_checkable(
                        child_item, cat not in settings.hidden_libraries)
                    self._lib_checkboxes[cat] = child_item

                # Set the parent's check state explicitly (it'll be auto-managed
                # afterward by ItemIsAutoTristate as the user toggles children)
                n_total = parent_item.childCount()
                n_checked = sum(
                    1 for i in range(n_total)
                    if parent_item.child(i).checkState(0) == Qt.CheckState.Checked
                )
                if n_checked == 0:
                    parent_item.setCheckState(0, Qt.CheckState.Unchecked)
                elif n_checked == n_total:
                    parent_item.setCheckState(0, Qt.CheckState.Checked)
                else:
                    parent_item.setCheckState(0, Qt.CheckState.PartiallyChecked)

                parent_item.setExpanded(parent in expanded_set)
        else:
            lib_form.addWidget(QLabel("No library loaded"))
        lib_group.setLayout(lib_form)
        lib_layout.addWidget(lib_group)

        # Custom library paths
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        paths_group = QGroupBox("Additional Library Folders")
        paths_layout = QVBoxLayout()
        paths_hint = QLabel(
            "Check a folder to include it in the library. "
            "Removing a folder also clears its visibility selections."
        )
        paths_hint.setStyleSheet("color: #666; font-size: 11px;")
        paths_hint.setWordWrap(True)
        paths_layout.addWidget(paths_hint)
        self._lib_paths_list = QListWidget()
        self._lib_paths_list.setMaximumHeight(120)
        for entry in settings.custom_library_paths:
            item = QListWidgetItem(entry["path"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if entry.get("enabled", True)
                else Qt.CheckState.Unchecked
            )
            self._lib_paths_list.addItem(item)
        paths_layout.addWidget(self._lib_paths_list)
        paths_btn_row = QHBoxLayout()
        add_path_btn = QPushButton("Add...")
        add_path_btn.setAutoDefault(False)
        add_path_btn.clicked.connect(self._add_library_path)
        remove_path_btn = QPushButton("Remove")
        remove_path_btn.setAutoDefault(False)
        remove_path_btn.clicked.connect(self._remove_library_path)
        paths_btn_row.addWidget(add_path_btn)
        paths_btn_row.addWidget(remove_path_btn)
        paths_btn_row.addStretch()
        paths_layout.addLayout(paths_btn_row)
        paths_group.setLayout(paths_layout)
        lib_layout.addWidget(paths_group)

        # Auto-add saved compounds toggle
        self._auto_add_compounds_cb = QCheckBox(
            "Automatically add saved compounds to the library")
        self._auto_add_compounds_cb.setChecked(settings.auto_add_saved_compounds)
        lib_layout.addWidget(self._auto_add_compounds_cb)

        lib_layout.addStretch()
        tabs.addTab(_scrollable(lib_tab), "Libraries")

        # -- Junction tab --
        junc_tab = QWidget()
        junc_layout = QVBoxLayout(junc_tab)
        junc_group = QGroupBox("Junction Appearance")
        junc_form = QFormLayout()

        self._junc_color = QColor(settings.junction_color)
        self._junc_color_btn = QPushButton()
        self._junc_color_btn.setFixedSize(60, 24)
        self._junc_color_btn.setAutoDefault(False)
        self._junc_color_btn.setStyleSheet(
            f"background-color: {self._junc_color.name()}; border: 1px solid #888;"
        )
        self._junc_color_btn.clicked.connect(self._pick_junc_color)
        junc_form.addRow("Fill color:", self._junc_color_btn)

        self._junc_outline_cb = QCheckBox("Show outline")
        self._junc_outline_cb.setChecked(settings.junction_outline)
        junc_form.addRow(self._junc_outline_cb)

        self._junc_radius_spin = QDoubleSpinBox()
        self._junc_radius_spin.setRange(2.0, 20.0)
        self._junc_radius_spin.setValue(settings.junction_radius)
        self._junc_radius_spin.setSuffix(" pt")
        self._junc_radius_spin.setSingleStep(1.0)
        junc_form.addRow("Radius:", self._junc_radius_spin)

        junc_group.setLayout(junc_form)
        junc_layout.addWidget(junc_group)
        junc_layout.addStretch()
        tabs.addTab(_scrollable(junc_tab), "Junctions")

        # -- Annotations tab --
        annot_tab = QWidget()
        annot_layout = QVBoxLayout(annot_tab)
        annot_group = QGroupBox("Default Annotation Style")
        annot_form = QFormLayout()

        from diagrammer.items.annotation_item import FONT_FAMILIES
        self._annot_font_combo = QComboBox()
        self._annot_font_combo.addItems(FONT_FAMILIES)
        current_font = settings.default_annotation_font
        if current_font in FONT_FAMILIES:
            self._annot_font_combo.setCurrentText(current_font)
        else:
            self._annot_font_combo.addItem(current_font)
            self._annot_font_combo.setCurrentText(current_font)
        annot_form.addRow("Font:", self._annot_font_combo)

        self._annot_size_spin = QDoubleSpinBox()
        self._annot_size_spin.setRange(4.0, 144.0)
        self._annot_size_spin.setValue(settings.default_annotation_size)
        self._annot_size_spin.setSuffix(" pt")
        self._annot_size_spin.setSingleStep(1.0)
        annot_form.addRow("Size:", self._annot_size_spin)

        self._annot_color = QColor(settings.default_annotation_color)
        self._annot_color_btn = QPushButton()
        self._annot_color_btn.setFixedSize(60, 24)
        self._annot_color_btn.setAutoDefault(False)
        self._update_color_btn(self._annot_color_btn, self._annot_color)
        self._annot_color_btn.clicked.connect(self._pick_annot_color)
        annot_form.addRow("Color:", self._annot_color_btn)

        annot_group.setLayout(annot_form)
        annot_layout.addWidget(annot_group)

        annot_hint = QLabel("Use $...$ in annotations for inline LaTeX math\n"
                            "and $$...$$ for display math (matrices, etc.).\n"
                            "STIX Two Text and CMU Serif closely match\n"
                            "TeX's Computer Modern style.")
        annot_hint.setStyleSheet("color: #666; font-size: 11px; margin-top: 8px;")
        annot_layout.addWidget(annot_hint)

        # Display-math backend toggle. By default we prefer ziamath
        # (pure-Python, no install) when available; users with a working
        # MacTeX/TeX Live install may flip this on to get matplotlib's
        # usetex pipeline instead — useful when ziamath's bracket sizing
        # or layout falls short.
        self._prefer_latex_cb = QCheckBox(
            "Prefer system LaTeX over ziamath for $$...$$ display math")
        self._prefer_latex_cb.setChecked(settings.prefer_system_latex_for_math)
        self._prefer_latex_cb.setToolTip(
            "Requires a system LaTeX install (MacTeX, TeX Live, MiKTeX). "
            "When unchecked, ziamath is used if available."
        )
        annot_layout.addWidget(self._prefer_latex_cb)

        # Explicit path to the LaTeX bin directory. macOS GUI apps
        # launched from a .app bundle don't inherit the shell PATH where
        # /Library/TeX/texbin lives, so users need a way to point us at
        # the install explicitly.
        from PySide6.QtWidgets import QLineEdit
        latex_path_row = QHBoxLayout()
        latex_path_row.addWidget(QLabel("LaTeX bin path:"))
        self._latex_path_edit = QLineEdit(settings.latex_bin_path)
        self._latex_path_edit.setPlaceholderText(
            "Auto-detect (e.g. /Library/TeX/texbin)")
        self._latex_path_edit.setToolTip(
            "Directory containing the 'latex' binary. Leave blank to "
            "use the inherited PATH. Typical values:\n"
            "  • macOS (MacTeX): /Library/TeX/texbin\n"
            "  • Linux (TeX Live): /usr/local/texlive/2024/bin/x86_64-linux\n"
            "  • Homebrew (BasicTeX): /usr/local/texlive/.../bin/universal-darwin"
        )
        latex_path_row.addWidget(self._latex_path_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setAutoDefault(False)
        browse_btn.clicked.connect(self._browse_latex_bin_path)
        latex_path_row.addWidget(browse_btn)
        annot_layout.addLayout(latex_path_row)

        # Matrix typography knobs (LaTeX usetex path)
        matrix_form = QFormLayout()
        self._latex_arraycolsep_spin = QDoubleSpinBox()
        self._latex_arraycolsep_spin.setRange(0.0, 128.0)
        self._latex_arraycolsep_spin.setSingleStep(0.5)
        self._latex_arraycolsep_spin.setSuffix(" pt")
        self._latex_arraycolsep_spin.setValue(settings.latex_arraycolsep_pt)
        self._latex_arraycolsep_spin.setToolTip(
            "Inter-column spacing inside bmatrix / pmatrix / array. "
            "LaTeX default is 5 pt; 6 pt feels closer to standard "
            "typeset matrices."
        )
        matrix_form.addRow("Matrix column gap (\\arraycolsep):",
                           self._latex_arraycolsep_spin)

        self._latex_arraystretch_spin = QDoubleSpinBox()
        self._latex_arraystretch_spin.setRange(0.5, 3.0)
        self._latex_arraystretch_spin.setSingleStep(0.05)
        self._latex_arraystretch_spin.setDecimals(2)
        self._latex_arraystretch_spin.setValue(settings.latex_arraystretch)
        self._latex_arraystretch_spin.setToolTip(
            "Row-height multiplier inside matrix / array environments. "
            "LaTeX default is 1.0; 1.15 adds a bit of vertical breathing "
            "room."
        )
        matrix_form.addRow("Matrix row stretch (\\arraystretch):",
                           self._latex_arraystretch_spin)

        annot_layout.addLayout(matrix_form)

        reset_all_btn = QPushButton("Reset All to Original Defaults")
        reset_all_btn.setAutoDefault(False)
        reset_all_btn.clicked.connect(self._reset_to_factory)
        annot_layout.addWidget(reset_all_btn, alignment=Qt.AlignmentFlag.AlignRight)
        annot_layout.addStretch()
        tabs.addTab(_scrollable(annot_tab), "Annotations")

        # -- Keyboard Shortcuts tab --
        tabs.addTab(self._build_shortcuts_tab(), "Keyboard Shortcuts")

        # -- OK / Cancel --
        btn_row = QHBoxLayout()
        self._ok_btn = QPushButton("OK")
        self._ok_btn.setAutoDefault(False)
        self._ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self._ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        # Initial conflict pass
        self._refresh_shortcut_conflicts()

    def _update_color_btn(self, btn: QPushButton, color: QColor) -> None:
        btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #888;")

    def _pick_line_color(self) -> None:
        c = QColorDialog.getColor(self._line_color, self, "Default Line Color")
        if c.isValid():
            self._line_color = c
            self._update_color_btn(self._line_color_btn, c)

    def _reset_line_defaults(self) -> None:
        self._line_width_spin.setValue(3.0)
        self._line_color = QColor(50, 50, 50)
        self._update_color_btn(self._line_color_btn, self._line_color)
        self._corner_radius_spin.setValue(8.0)

    def _reset_snap_defaults(self) -> None:
        self._snap_to_port_cb.setChecked(True)
        self._snap_to_angle_cb.setChecked(False)
        self._angle_increment_spin.setValue(15.0)
        self._nudge_fraction_spin.setValue(0.2)

    def _pick_junc_color(self) -> None:
        c = QColorDialog.getColor(self._junc_color, self, "Junction Color")
        if c.isValid():
            self._junc_color = c
            self._junc_color_btn.setStyleSheet(
                f"background-color: {c.name()}; border: 1px solid #888;"
            )

    def _browse_latex_bin_path(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        start = self._latex_path_edit.text() or "/Library/TeX/texbin"
        path = QFileDialog.getExistingDirectory(
            self, "Select LaTeX bin directory", start)
        if path:
            self._latex_path_edit.setText(path)

    def _add_library_path(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QListWidgetItem
        path = QFileDialog.getExistingDirectory(self, "Select Library Folder")
        if not path:
            return
        existing = [self._lib_paths_list.item(i).text()
                    for i in range(self._lib_paths_list.count())]
        if path in existing:
            return
        item = QListWidgetItem(path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self._lib_paths_list.addItem(item)

    def _remove_library_path(self) -> None:
        row = self._lib_paths_list.currentRow()
        if row < 0:
            return
        item = self._lib_paths_list.item(row)
        removed_path = item.text()
        self._lib_paths_list.takeItem(row)
        # Strip any hidden_libraries entries that came from this path's
        # categories. We re-derive them by scanning the directory now (cheap;
        # the user just removed it). Categories also present in another
        # source are left alone.
        try:
            from pathlib import Path
            from diagrammer.models.library import ComponentLibrary
            removed_lib = ComponentLibrary()
            p = Path(removed_path)
            if p.is_dir():
                removed_lib.scan(p)
            removed_cats = set(removed_lib.categories.keys())
            # Categories still provided by other enabled sources
            other_cats: set[str] = set()
            if self._library is not None:
                other_cats |= set(self._library.categories.keys())
            # Subtract: only purge from hidden_libraries the cats unique to removed path
            unique_to_removed = removed_cats - other_cats
            # But _library may already include the removed path's cats — also
            # purge any hidden cat that lives under removed_cats since the
            # user explicitly removed the source.
            for cat in list(self._lib_checkboxes.keys()):
                if cat in removed_cats and cat not in (other_cats - removed_cats):
                    # If the same category exists from another source, leave it.
                    pass
            for cat in unique_to_removed:
                self._settings.hidden_libraries.discard(cat)
        except Exception:
            pass

    def _reset_to_factory(self) -> None:
        """Reset ALL settings to factory defaults from defaults.yaml."""
        self._settings.reset_all()
        self._settings.save()
        # Update dialog controls to reflect reset values
        self._line_width_spin.setValue(self._settings.default_line_width)
        self._line_color = QColor(self._settings.default_line_color)
        self._update_color_btn(self._line_color_btn, self._line_color)
        self._corner_radius_spin.setValue(self._settings.default_corner_radius)
        self._snap_to_port_cb.setChecked(self._settings.snap_to_port)
        self._snap_to_angle_cb.setChecked(self._settings.snap_to_angle)
        self._angle_increment_spin.setValue(self._settings.angle_snap_increment)
        self._junc_color = QColor(self._settings.junction_color)
        self._junc_color_btn.setStyleSheet(
            f"background-color: {self._junc_color.name()}; border: 1px solid #888;")
        self._junc_outline_cb.setChecked(self._settings.junction_outline)
        self._junc_radius_spin.setValue(self._settings.junction_radius)
        self._annot_font_combo.setCurrentText(self._settings.default_annotation_font)
        self._annot_size_spin.setValue(self._settings.default_annotation_size)
        self._annot_color = QColor(self._settings.default_annotation_color)
        self._update_color_btn(self._annot_color_btn, self._annot_color)

    def _pick_annot_color(self) -> None:
        c = QColorDialog.getColor(self._annot_color, self, "Default Annotation Color")
        if c.isValid():
            self._annot_color = c
            self._update_color_btn(self._annot_color_btn, c)

    # ----------------------- Keyboard Shortcuts tab -----------------------

    def _build_shortcuts_tab(self) -> QWidget:
        from diagrammer import shortcuts as _sc

        tab = QWidget()
        v = QVBoxLayout(tab)

        hint = QLabel(
            "Customize keyboard shortcuts. Conflicts must be resolved before "
            "you can save."
        )
        hint.setStyleSheet("color: #555;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # Search box
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._sc_search = QLineEdit()
        self._sc_search.setPlaceholderText("Filter by action, category, or key…")
        self._sc_search.setClearButtonEnabled(True)
        self._sc_search.textChanged.connect(self._filter_shortcut_rows)
        search_row.addWidget(self._sc_search, 1)
        v.addLayout(search_row)

        # Tree
        self._sc_tree = QTreeWidget()
        self._sc_tree.setColumnCount(4)
        self._sc_tree.setHeaderLabels(["Action", "Shortcut", "Default", ""])
        self._sc_tree.setRootIsDecorated(True)
        self._sc_tree.setUniformRowHeights(False)
        self._sc_tree.setAlternatingRowColors(True)
        v.addWidget(self._sc_tree, 1)

        # action_id -> {item, edit, reset_btn, default_str, group_item, description}
        self._sc_rows: dict[str, dict] = {}

        for category, shortcuts in _sc.shortcuts_by_category().items():
            group = QTreeWidgetItem(self._sc_tree, [category])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setFirstColumnSpanned(True)
            group.setExpanded(True)
            for s in shortcuts:
                item = QTreeWidgetItem(group)
                label = s.description or s.action_id
                item.setText(0, label)
                item.setToolTip(0, s.action_id)
                # Shortcut editor
                edit = QKeySequenceEdit(s.key_sequence)
                edit.keySequenceChanged.connect(self._refresh_shortcut_conflicts)
                self._sc_tree.setItemWidget(item, 1, edit)
                # Default column
                item.setText(2, s.default_display_text or "—")
                item.setForeground(2, QBrush(QColor("#888")))
                # Per-row reset
                reset_btn = QPushButton("Reset")
                reset_btn.setAutoDefault(False)
                reset_btn.setFixedWidth(60)
                reset_btn.clicked.connect(
                    lambda _=False, aid=s.action_id: self._reset_one_shortcut(aid))
                self._sc_tree.setItemWidget(item, 3, reset_btn)

                self._sc_rows[s.action_id] = {
                    "item": item,
                    "edit": edit,
                    "reset_btn": reset_btn,
                    "default_str": s.default_key_sequence.toString(
                        QKeySequence.SequenceFormat.PortableText),
                    "group_item": group,
                    "description": s.description or s.action_id,
                    "category": category,
                }

        for col in (0, 2, 3):
            self._sc_tree.resizeColumnToContents(col)

        # Status + reset all
        bottom_row = QHBoxLayout()
        self._sc_status = QLabel("")
        self._sc_status.setStyleSheet("color: #b00;")
        bottom_row.addWidget(self._sc_status, 1)
        reset_all_btn = QPushButton("Reset Keyboard Shortcuts")
        reset_all_btn.setAutoDefault(False)
        reset_all_btn.clicked.connect(self._reset_all_shortcuts)
        bottom_row.addWidget(reset_all_btn)
        v.addLayout(bottom_row)

        return tab

    def _current_proposed_shortcuts(self) -> dict[str, str]:
        """Snapshot of every row's editor as {action_id: portable_string}."""
        out: dict[str, str] = {}
        for aid, row in self._sc_rows.items():
            seq = row["edit"].keySequence()
            out[aid] = seq.toString(QKeySequence.SequenceFormat.PortableText)
        return out

    def _refresh_shortcut_conflicts(self) -> None:
        from diagrammer import shortcuts as _sc

        if not hasattr(self, "_sc_rows"):
            return
        proposed = self._current_proposed_shortcuts()
        conflicts = _sc.find_conflicts(proposed)

        # Map action_id -> list of other action_ids it conflicts with
        conflict_partners: dict[str, list[str]] = {}
        for _key, ids in conflicts.items():
            for aid in ids:
                conflict_partners[aid] = [o for o in ids if o != aid]

        red = QBrush(QColor("#ffd5d5"))
        clear = QBrush(Qt.GlobalColor.transparent)
        for aid, row in self._sc_rows.items():
            item = row["item"]
            if aid in conflict_partners:
                others = conflict_partners[aid]
                others_desc = ", ".join(
                    self._sc_rows[o]["description"] for o in others
                    if o in self._sc_rows
                )
                tip = f"Conflicts with: {others_desc}"
                for c in range(self._sc_tree.columnCount()):
                    item.setBackground(c, red)
                    item.setToolTip(c, tip)
            else:
                for c in range(self._sc_tree.columnCount()):
                    item.setBackground(c, clear)
                item.setToolTip(0, aid)

        n = len({a for ids in conflicts.values() for a in ids})
        if n:
            self._sc_status.setText(
                f"{n} shortcut conflict{'s' if n != 1 else ''} — resolve before saving"
            )
            if hasattr(self, "_ok_btn"):
                self._ok_btn.setEnabled(False)
        else:
            self._sc_status.setText("")
            if hasattr(self, "_ok_btn"):
                self._ok_btn.setEnabled(True)

    def _filter_shortcut_rows(self, text: str) -> None:
        text = text.strip().lower()
        for row in self._sc_rows.values():
            item = row["item"]
            if not text:
                item.setHidden(False)
                continue
            haystack = " ".join([
                row["description"].lower(),
                row["category"].lower(),
                row["edit"].keySequence().toString(
                    QKeySequence.SequenceFormat.NativeText).lower(),
                row["default_str"].lower(),
            ])
            item.setHidden(text not in haystack)
        # Hide group rows whose children are all hidden
        root = self._sc_tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            visible_children = any(
                not group.child(j).isHidden() for j in range(group.childCount())
            )
            group.setHidden(not visible_children)

    def _reset_one_shortcut(self, action_id: str) -> None:
        from diagrammer import shortcuts as _sc
        s = _sc.SHORTCUTS.get(action_id)
        if s is None:
            return
        row = self._sc_rows.get(action_id)
        if row is None:
            return
        row["edit"].setKeySequence(s.default_key_sequence)
        self._refresh_shortcut_conflicts()

    def _reset_all_shortcuts(self) -> None:
        from diagrammer import shortcuts as _sc
        for aid, row in self._sc_rows.items():
            s = _sc.SHORTCUTS.get(aid)
            if s is not None:
                row["edit"].setKeySequence(s.default_key_sequence)
        self._refresh_shortcut_conflicts()

    def apply(self) -> None:
        """Write dialog values into the settings object."""
        self._settings.default_line_width = self._line_width_spin.value()
        self._settings.default_line_color = QColor(self._line_color)
        self._settings.default_corner_radius = self._corner_radius_spin.value()
        self._settings.snap_to_port = self._snap_to_port_cb.isChecked()
        self._settings.snap_to_angle = self._snap_to_angle_cb.isChecked()
        self._settings.angle_snap_increment = self._angle_increment_spin.value()
        self._settings.nudge_fraction = self._nudge_fraction_spin.value()
        # Junction appearance
        self._settings.junction_color = QColor(self._junc_color)
        self._settings.junction_outline = self._junc_outline_cb.isChecked()
        self._settings.junction_radius = self._junc_radius_spin.value()
        # Library visibility (children are independently controllable;
        # parent items use auto tri-state purely as a UI convenience)
        self._settings.hidden_libraries = {
            cat for cat, item in self._lib_checkboxes.items()
            if item.checkState(0) != Qt.CheckState.Checked
        }
        # Library tab UI state: which parent groups are currently expanded
        if self._parent_checkboxes:
            self._settings.library_tab_expanded = sorted(
                p for p, item in self._parent_checkboxes.items()
                if item.isExpanded()
            )
        # Custom library paths (each entry: {"path": str, "enabled": bool})
        new_paths: list[dict] = []
        for i in range(self._lib_paths_list.count()):
            item = self._lib_paths_list.item(i)
            new_paths.append({
                "path": item.text(),
                "enabled": item.checkState() == Qt.CheckState.Checked,
            })
        self._settings.custom_library_paths = new_paths
        # Auto-add toggle for compounds saved via "Create Component"
        self._settings.auto_add_saved_compounds = self._auto_add_compounds_cb.isChecked()
        # Prefer system LaTeX over ziamath for display math
        self._settings.prefer_system_latex_for_math = self._prefer_latex_cb.isChecked()
        self._settings.latex_bin_path = self._latex_path_edit.text().strip()
        self._settings.latex_arraycolsep_pt = self._latex_arraycolsep_spin.value()
        self._settings.latex_arraystretch = self._latex_arraystretch_spin.value()
        # Annotation defaults
        self._settings.default_annotation_font = self._annot_font_combo.currentText()
        self._settings.default_annotation_size = self._annot_size_spin.value()
        self._settings.default_annotation_color = QColor(self._annot_color)
        # Keyboard shortcut overrides
        from diagrammer import shortcuts as _sc
        for aid, row in self._sc_rows.items():
            s = _sc.SHORTCUTS.get(aid)
            if s is not None:
                s.set_override(row["edit"].keySequence())
        # Persist to disk (save() also dumps live overrides)
        self._settings.save()
