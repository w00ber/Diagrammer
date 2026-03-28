"""SettingsDialog — application-wide style and behavior settings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
        # Line styles
        self.default_line_width = 3.0
        self.default_line_color = QColor(50, 50, 50)
        self.default_corner_radius = 8.0

        # Snap behavior
        self.snap_to_port = True
        self.snap_to_angle = False
        self.snap_to_grid = True
        self.angle_snap_increment = 15.0  # degrees

        # Junction appearance
        self.show_junctions = True
        self.junction_color = QColor(0, 0, 0)       # black fill
        self.junction_outline = False                 # no outline by default
        self.junction_radius = 5.0

        # Routing menu state
        self.discrete_angle_routing = False

        # Component styles
        self.default_component_fill = QColor(255, 255, 255, 0)

        # Library
        self.hidden_libraries: set[str] = set()
        self.library_view_mode = "tree"  # "tree" or "grid"

    def save(self) -> None:
        """Persist settings to disk."""
        try:
            _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "default_line_width": self.default_line_width,
                "default_line_color": self.default_line_color.name(),
                "default_corner_radius": self.default_corner_radius,
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
        line_reset.clicked.connect(self._reset_line_defaults)
        line_layout.addWidget(line_reset, alignment=Qt.AlignmentFlag.AlignRight)
        line_layout.addStretch()

        tabs.addTab(line_tab, "Line Styles")

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
        snap_reset.clicked.connect(self._reset_snap_defaults)
        snap_layout.addWidget(snap_reset, alignment=Qt.AlignmentFlag.AlignRight)
        snap_layout.addStretch()

        tabs.addTab(snap_tab, "Snap Behavior")

        # -- Libraries tab --
        lib_tab = QWidget()
        lib_layout = QVBoxLayout(lib_tab)
        lib_group = QGroupBox("Visible Component Libraries")
        lib_form = QVBoxLayout()
        self._lib_checkboxes: dict[str, QCheckBox] = {}
        if library:
            for cat in sorted(library.categories.keys()):
                cb = QCheckBox(cat.replace("_", " ").title())
                cb.setChecked(cat not in settings.hidden_libraries)
                lib_form.addWidget(cb)
                self._lib_checkboxes[cat] = cb
        else:
            lib_form.addWidget(QLabel("No library loaded"))
        lib_group.setLayout(lib_form)
        lib_layout.addWidget(lib_group)
        lib_layout.addStretch()
        tabs.addTab(lib_tab, "Libraries")

        # -- Junction tab --
        junc_tab = QWidget()
        junc_layout = QVBoxLayout(junc_tab)
        junc_group = QGroupBox("Junction Appearance")
        junc_form = QFormLayout()

        self._junc_color = QColor(settings.junction_color)
        self._junc_color_btn = QPushButton()
        self._junc_color_btn.setFixedSize(60, 24)
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
        tabs.addTab(junc_tab, "Junctions")

        # -- OK / Cancel --
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
        # Persist to disk
        self._settings.save()
