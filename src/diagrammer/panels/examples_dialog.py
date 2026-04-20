"""ExamplesDialog — browse and open bundled example diagrams.

The dialog shows a scrollable grid of example cards. Each card renders a
preview thumbnail by loading the example into a hidden ``DiagramScene`` and
calling ``render_scene_to_qimage``. Picking an example signals back to the
main window, which opens it as an untitled document so the read-only
bundled file is never modified by Save.

Examples ship in ``diagrammer/examples/*.dgm``. Filename (without extension)
is the display name; ``-`` and ``_`` are turned into spaces.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from diagrammer._resources import resource_path

logger = logging.getLogger(__name__)

# Thumbnail target size in device pixels.
_THUMB_W = 220
_THUMB_H = 140

# In-process cache so reopening the dialog doesn't re-render previews.
_preview_cache: dict[str, QPixmap] = {}


def examples_dir() -> Path:
    """Return the bundled examples directory (may not exist)."""
    return resource_path("examples")


def list_example_files() -> list[Path]:
    """Return all bundled example .dgm files, sorted by display name."""
    d = examples_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("*.dgm"), key=lambda p: p.stem.lower())


def _display_name(path: Path) -> str:
    return path.stem.replace("-", " ").replace("_", " ")


def _render_preview(path: Path, library) -> QPixmap | None:
    """Load *path* into a temporary scene and render a preview pixmap.

    Returns None if the file fails to load. Cached by absolute path.
    """
    key = str(path.resolve())
    if key in _preview_cache:
        return _preview_cache[key]

    try:
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.exporter import render_scene_to_qimage
        from diagrammer.io.serializer import DiagramSerializer

        scene = DiagramScene(library=library)
        DiagramSerializer.load(scene, path, library=library)
        from PySide6.QtGui import QColor
        image = render_scene_to_qimage(
            scene, dpi=96, margin=10.0, background=QColor(Qt.GlobalColor.white),
        )
        if image.isNull():
            return None
        pix = QPixmap.fromImage(image).scaled(
            QSize(_THUMB_W, _THUMB_H),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        _preview_cache[key] = pix
        return pix
    except Exception:
        logger.debug("Failed to generate preview for example %s", path.name, exc_info=True)
        return None


class ExamplesDialog(QDialog):
    """Modal dialog presenting a grid of example diagrams."""

    example_chosen = Signal(Path)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Examples")
        self.resize(780, 560)

        self._library = library
        self._selected_path: Path | None = None

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Pick an example to open as a new untitled diagram. "
            "Bundled examples are read-only — your edits won't affect them."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        container = QWidget()
        scroll.setWidget(container)
        grid = QGridLayout(container)
        grid.setSpacing(12)

        files = list_example_files()
        if not files:
            empty = QLabel(
                "No examples are bundled with this build yet.\n\n"
                f"Drop .dgm files into:\n{examples_dir()}"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: gray; padding: 40px;")
            grid.addWidget(empty, 0, 0)
        else:
            cols = 3
            for i, path in enumerate(files):
                card = self._make_card(path)
                grid.addWidget(card, i // cols, i % cols)
            grid.setRowStretch(grid.rowCount(), 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _make_card(self, path: Path) -> QWidget:
        btn = QToolButton()
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setAutoRaise(True)
        btn.setText(_display_name(path))
        btn.setToolTip(str(path))
        btn.setIconSize(QSize(_THUMB_W, _THUMB_H))
        btn.setMinimumSize(_THUMB_W + 24, _THUMB_H + 48)

        pix = _render_preview(path, self._library)
        if pix is not None:
            from PySide6.QtGui import QIcon
            btn.setIcon(QIcon(pix))

        btn.clicked.connect(lambda checked=False, p=path: self._on_pick(p))
        return btn

    def _on_pick(self, path: Path) -> None:
        self._selected_path = path
        self.example_chosen.emit(path)
        self.accept()

    @property
    def selected_path(self) -> Path | None:
        return self._selected_path
