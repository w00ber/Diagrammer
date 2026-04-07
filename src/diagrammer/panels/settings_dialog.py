"""SettingsDialog — application-wide style and behavior settings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
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
        self.custom_library_paths: list[str] = []  # additional library directories

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
        self.prefer_system_latex_for_math: bool = False
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
                "show_junctions": self.show_junctions,
                "junction_color": self.junction_color.name(),
                "junction_outline": self.junction_outline,
                "junction_radius": self.junction_radius,
                "discrete_angle_routing": self.discrete_angle_routing,
                "hidden_libraries": sorted(self.hidden_libraries),
                "library_view_mode": self.library_view_mode,
                "custom_library_paths": self.custom_library_paths,
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
                self.show_junctions = data.get("show_junctions", self.show_junctions)
                jc = data.get("junction_color")
                if jc:
                    self.junction_color = QColor(jc)
                self.junction_outline = data.get("junction_outline", self.junction_outline)
                self.junction_radius = data.get("junction_radius", self.junction_radius)
                self.discrete_angle_routing = data.get("discrete_angle_routing", self.discrete_angle_routing)
                self.hidden_libraries = set(data.get("hidden_libraries", []))
                self.library_view_mode = data.get("library_view_mode", self.library_view_mode)
                self.custom_library_paths = data.get("custom_library_paths", [])
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
        except Exception:
            pass


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
        self._lib_checkboxes: dict[str, QCheckBox] = {}
        self._parent_checkboxes: dict[str, QCheckBox] = {}
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

            def _update_parent(parent_cb, children_cbs):
                """When parent is toggled, set all children to match."""
                checked = parent_cb.isChecked()
                for ccb in children_cbs:
                    ccb.setChecked(checked)
                    if not checked:
                        ccb.setEnabled(False)
                        ccb.setStyleSheet("color: #999;")
                    else:
                        ccb.setEnabled(True)
                        ccb.setStyleSheet("")

            # Top-level categories (no parent)
            for cat in top_level:
                cb = QCheckBox(cat.replace("_", " ").title())
                cb.setChecked(cat not in settings.hidden_libraries)
                lib_form.addWidget(cb)
                self._lib_checkboxes[cat] = cb

            # Parent groups with children
            for parent, children in sorted(parents.items()):
                # Parent checkbox
                all_visible = all(c not in settings.hidden_libraries for c in children)
                none_visible = all(c in settings.hidden_libraries for c in children)
                parent_cb = QCheckBox(f"{parent.replace('_', ' ').title()}")
                parent_cb.setStyleSheet("font-weight: bold; margin-top: 4px;")
                parent_cb.setChecked(not none_visible)
                lib_form.addWidget(parent_cb)
                self._parent_checkboxes[parent] = parent_cb

                # Child checkboxes
                child_cbs = []
                for cat in children:
                    display = cat.split("/")[-1].replace("_", " ").title()
                    cb = QCheckBox(f"    {display}")
                    cb.setChecked(cat not in settings.hidden_libraries)
                    if none_visible:
                        cb.setEnabled(False)
                        cb.setStyleSheet("color: #999;")
                    lib_form.addWidget(cb)
                    self._lib_checkboxes[cat] = cb
                    child_cbs.append(cb)

                # Connect parent to children
                parent_cb.toggled.connect(
                    lambda checked, p=parent_cb, cs=child_cbs: _update_parent(p, cs)
                )
        else:
            lib_form.addWidget(QLabel("No library loaded"))
        lib_group.setLayout(lib_form)
        lib_layout.addWidget(lib_group)

        # Custom library paths
        from PySide6.QtWidgets import QListWidget, QFileDialog as _FD
        paths_group = QGroupBox("Additional Library Folders")
        paths_layout = QVBoxLayout()
        self._lib_paths_list = QListWidget()
        self._lib_paths_list.setMaximumHeight(100)
        for p in settings.custom_library_paths:
            self._lib_paths_list.addItem(p)
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

        # -- OK / Cancel --
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setAutoDefault(False)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

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
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "Select Library Folder")
        if path:
            # Don't add duplicates
            existing = [self._lib_paths_list.item(i).text()
                        for i in range(self._lib_paths_list.count())]
            if path not in existing:
                self._lib_paths_list.addItem(path)

    def _remove_library_path(self) -> None:
        row = self._lib_paths_list.currentRow()
        if row >= 0:
            self._lib_paths_list.takeItem(row)

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

    def apply(self) -> None:
        """Write dialog values into the settings object."""
        self._settings.default_line_width = self._line_width_spin.value()
        self._settings.default_line_color = QColor(self._line_color)
        self._settings.default_corner_radius = self._corner_radius_spin.value()
        self._settings.snap_to_port = self._snap_to_port_cb.isChecked()
        self._settings.snap_to_angle = self._snap_to_angle_cb.isChecked()
        self._settings.angle_snap_increment = self._angle_increment_spin.value()
        # Junction appearance
        self._settings.junction_color = QColor(self._junc_color)
        self._settings.junction_outline = self._junc_outline_cb.isChecked()
        self._settings.junction_radius = self._junc_radius_spin.value()
        # Library visibility
        self._settings.hidden_libraries = set()
        for cat, cb in self._lib_checkboxes.items():
            if not cb.isChecked():
                self._settings.hidden_libraries.add(cat)
        # Also hide all children of unchecked parents
        for parent, pcb in self._parent_checkboxes.items():
            if not pcb.isChecked():
                for cat in self._lib_checkboxes:
                    if cat.startswith(parent + "/"):
                        self._settings.hidden_libraries.add(cat)
        # Custom library paths
        self._settings.custom_library_paths = [
            self._lib_paths_list.item(i).text()
            for i in range(self._lib_paths_list.count())
        ]
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
        # Persist to disk
        self._settings.save()
