"""The Component Inspector: validate one component, preview fixes, save.

Three ideas carry the design.

**The grid is the main instrument.** Most real faults in a component are
positional — a port 3 pt off its edge, artwork that is not where the viewBox
says it is — and they are invisible in a picture of the symbol. So the symbol is
drawn on the same grid the canvas uses, at 1:1 scene points, with the viewBox
frame outlined. That is also why grid *conformance* is not a check: 97 of 208
shipped files miss the 20 pt grid and most do so by design, so showing the grid
and letting the eye decide beats a report that cries wolf.

**The candidate is always recomputed from the pristine original.** Ticking a fix
never mutates state. ``apply_fixes`` runs again from ``_original_text`` on every
toggle, which makes unticking exact with no inverse transform and makes the
preview a pure function of the selected set rather than of the click order.

**Nothing is written until Save.** The whole loop works on text, including the
render, which is why ``ComponentDef.from_svg_text`` and
``prepare_svg_bytes_from_root`` exist.
"""

from __future__ import annotations

import difflib
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGraphicsItem,
    QGraphicsScene,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diagrammer import svglint
from diagrammer.canvas.grid import draw_grid
from diagrammer.panels.library_table_view import LibraryTableView
from diagrammer.panels.settings_dialog import app_settings
from diagrammer.svglint.model import Finding, Marker

logger = logging.getLogger(__name__)

SEVERITY_COLOR = {
    "error": QColor("#D0342C"),
    "warning": QColor("#D98B00"),
    "info": QColor("#2C6FD0"),
}
SEVERITY_GLYPH = {"error": "✕", "warning": "⚠", "info": "ℹ"}

#: Grid spacings offered in the combo. 20 is the authoring grid; 5 and 10 are
#: for inspecting a part that was drawn on a finer one.
GRID_CHOICES = (5.0, 10.0, 20.0)

#: How far, in *pixels*, the pointer may be from a marker and still hit it.
#: Pixels rather than scene units on purpose: an item-space hit shape of a few
#: units is sub-pixel when zoomed out, which makes a finding ungrabbable exactly
#: when the user is looking at the whole part.
HIT_RADIUS_PX = 7

GHOST_OPACITY = 0.4


#: Layers that carry positions rather than artwork. The canvas hides them; the
#: inspector shows them, but behind the drawing — see ``_demote_marker_layers``.
MARKER_LAYERS = ("ports", "labels", "stretch", "snap")

#: How solid those markers are drawn. Enough to locate a port, sheer enough to
#: read a lead end underneath it.
MARKER_OPACITY = "0.45"


def _demote_marker_layers(root, *, opacity: str = MARKER_OPACITY) -> None:
    """Move the position-marker layers behind the drawing, and fade them.

    A port marker is a filled disc of radius 3 sitting exactly on the lead's
    endpoint, and the lead is 2 pt wide — so wherever the document happens to
    put ``<g id="ports">`` after ``<g id="leads">``, which is 105 of the 238
    shipped components, the disc covers the very endpoint you opened the
    inspector to look at. Document order decides that, and it is an accident of
    how each file was exported rather than anything meaningful.

    So the inspector reorders them for display: markers first, everything that
    paints the symbol after. The lead now wins where they overlap and the disc
    still reads as a ring around it. Fading them as well keeps a marker that
    sits over artwork from masking it.

    Display only — this mutates the tree being rendered, never the candidate
    text that Save writes.
    """
    children = list(root)
    markers = [
        el for el in children
        if (el.tag.split("}")[-1] if "}" in el.tag else el.tag) == "g"
        and el.get("id") in MARKER_LAYERS
    ]
    if not markers:
        return
    # Insert after <defs>/<style>/<metadata>, which must stay at the top.
    head = 0
    for el in children:
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag in ("defs", "style", "metadata", "title", "desc"):
            head = children.index(el) + 1
        else:
            break
    for el in markers:
        root.remove(el)
    for offset, el in enumerate(markers):
        if opacity:
            el.set("opacity", opacity)
        root.insert(head + offset, el)


def render_component_bytes(text: str, *, show_markers: bool = True) -> bytes:
    """Prepare SVG *text* for display.

    ``hide_layers=()`` is the first thing the inspector changes about
    rendering: the canvas hides ports, labels, stretch and snap because they
    carry positions rather than artwork, but showing where they sit is this
    dialog's entire job. The second is ``_demote_marker_layers``, so showing
    them does not hide what they mark.

    ``ET.fromstring`` rather than ``parse_svg``, because ``parse_svg`` takes a
    path and — worse — repairs the file on disk when ET rejects it. A candidate
    must never touch the disk.
    """
    from diagrammer.io.svg_parse import normalize_svg_text
    from diagrammer.items.component_item import prepare_svg_bytes_from_root

    normalized, _ = normalize_svg_text(text)
    root = ET.fromstring(normalized)
    if show_markers:
        _demote_marker_layers(root)
        hide = ()
    else:
        hide = MARKER_LAYERS
    return prepare_svg_bytes_from_root(root, hide_layers=hide)


# ---------------------------------------------------------------------------
# scene items
# ---------------------------------------------------------------------------

class _SvgItem(QGraphicsItem):
    """A component rendered at its native viewBox size.

    Native size is what makes the markers free: a component's viewBox units are
    scene points, 1:1, so a finding's coordinates can be used as-is with no
    transform anywhere.
    """

    def __init__(self, svg_bytes: bytes, width: float, height: float,
                 parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self._renderer = QSvgRenderer(QByteArray(svg_bytes))
        self._w, self._h = width, height
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self._renderer.isValid():
            self._renderer.render(painter, self.boundingRect())


class _FrameItem(QGraphicsItem):
    """The viewBox outline, with its origin marked.

    Drawn because half the positional bugs are "the artwork is not where the
    viewBox says it is", and that is simply invisible without the frame.
    """

    def __init__(self, width: float, height: float, caption: str = "",
                 parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self._w, self._h = width, height
        self._caption = caption
        self.setZValue(10)

    def boundingRect(self) -> QRectF:
        return QRectF(-14, -22, self._w + 28, self._h + 36)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        pen = QPen(QColor(90, 90, 90), 0)
        painter.setPen(pen)
        painter.drawRect(QRectF(0, 0, self._w, self._h))
        # origin tick, so a rebased viewBox is obvious
        painter.drawLine(QPointF(0, 0), QPointF(7, 0))
        painter.drawLine(QPointF(0, 0), QPointF(0, 7))
        if self._caption:
            font = QFont()
            font.setPointSizeF(9)
            painter.setFont(font)
            painter.setPen(QPen(QColor(70, 70, 70)))
            painter.save()
            # Counter the view scale so the caption stays legible at any zoom.
            scale = painter.worldTransform().m11() or 1.0
            painter.translate(0, -6)
            painter.scale(1 / scale, 1 / scale)
            painter.drawText(QPointF(0, 0), self._caption)
            painter.restore()


class FindingMarkerItem(QGraphicsItem):
    """Paints one Marker over the artwork, in the component's own coordinates.

    Cosmetic pens throughout (width 0), so a marker stays one pixel wide at any
    zoom instead of swelling into the symbol it is pointing at.
    """

    def __init__(self, marker: Marker, finding: Finding,
                 frame: tuple[float, float],
                 parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self.marker = marker
        self.finding = finding
        self._fw, self._fh = frame
        self._emphasis = False
        self.setZValue(20)
        self.setToolTip(self._tooltip_text())
        self.setAcceptHoverEvents(False)

    # -- geometry ---------------------------------------------------------

    def boundingRect(self) -> QRectF:
        m = self.marker
        pad = 10.0
        if m.kind == "point" and len(m.coords) >= 2:
            x, y = m.coords[0], m.coords[1]
            return QRectF(x - pad, y - pad, pad * 2, pad * 2)
        if m.kind == "segment" and len(m.coords) >= 4:
            x1, y1, x2, y2 = m.coords[:4]
            return QRectF(min(x1, x2) - pad, min(y1, y2) - pad,
                          abs(x2 - x1) + pad * 2, abs(y2 - y1) + pad * 2)
        if m.kind == "rect" and len(m.coords) >= 4:
            x, y, w, h = m.coords[:4]
            return QRectF(x - pad, y - pad, w + pad * 2, h + pad * 2)
        return QRectF(-pad, -pad, self._fw + pad * 2, self._fh + pad * 2)

    def set_emphasis(self, on: bool) -> None:
        if on != self._emphasis:
            self._emphasis = on
            self.update()

    def _tooltip_text(self) -> str:
        f = self.finding
        parts = [f"<b>{f.severity.upper()}</b> — {f.message}"]
        if f.detail:
            parts.append(f.detail)
        return "<br><br>".join(parts)

    # -- paint ------------------------------------------------------------

    def paint(self, painter: QPainter, option, widget=None) -> None:
        color = SEVERITY_COLOR.get(self.finding.severity, QColor("#888"))
        width = 0  # cosmetic
        pen = QPen(color, width)
        if self._emphasis:
            pen.setWidthF(2.0)
            pen.setCosmetic(True)
        else:
            pen.setCosmetic(True)
        painter.setPen(pen)
        m = self.marker

        if m.kind == "point" and len(m.coords) >= 2:
            x, y = m.coords[0], m.coords[1]
            r = 6.0
            painter.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))
            painter.drawLine(QPointF(x - r - 2, y), QPointF(x - 2, y))
            painter.drawLine(QPointF(x + 2, y), QPointF(x + r + 2, y))
            painter.drawLine(QPointF(x, y - r - 2), QPointF(x, y - 2))
            painter.drawLine(QPointF(x, y + 2), QPointF(x, y + r + 2))
        elif m.kind == "segment" and len(m.coords) >= 4:
            x1, y1, x2, y2 = m.coords[:4]
            dashed = QPen(pen)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        elif m.kind == "rect" and len(m.coords) >= 4:
            x, y, w, h = m.coords[:4]
            dashed = QPen(pen)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.drawRect(QRectF(x, y, w, h))
        elif m.kind == "edge":
            dashed = QPen(pen)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            w, h = self._fw, self._fh
            lines = {
                "left": (QPointF(0, 0), QPointF(0, h)),
                "right": (QPointF(w, 0), QPointF(w, h)),
                "top": (QPointF(0, 0), QPointF(w, 0)),
                "bottom": (QPointF(0, h), QPointF(w, h)),
            }
            seg = lines.get(m.edge)
            if seg:
                painter.drawLine(*seg)

        if m.label:
            font = QFont()
            font.setPointSizeF(8)
            painter.setFont(font)
            painter.setPen(QPen(color))
            anchor = (QPointF(m.coords[0], m.coords[1])
                      if len(m.coords) >= 2 else QPointF(2, 10))
            painter.save()
            scale = painter.worldTransform().m11() or 1.0
            painter.translate(anchor.x() + 8, anchor.y() - 8)
            painter.scale(1 / scale, 1 / scale)
            painter.drawText(QPointF(0, 0), m.label)
            painter.restore()


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------

class ComponentInspectorView(LibraryTableView):
    """A grid-backed view of one component.

    Subclasses ``LibraryTableView`` for its zoom/pan/pinch/fit plumbing, which
    is identical to what this needs, and overrides the two things that differ:
    the background gets a grid, and the table's component context menu is
    suppressed since there is no table here.
    """

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None):
        super().__init__(scene, parent)
        self._spacing = 20.0
        self._grid_visible = True
        self._on_marker_hover = None

    def set_grid_spacing(self, spacing: float) -> None:
        self._spacing = max(1.0, spacing)
        self.viewport().update()

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = visible
        self.viewport().update()

    def drawBackground(self, painter: QPainter, rect) -> None:
        super().drawBackground(painter, rect)
        if self._grid_visible:
            draw_grid(
                painter, rect, self._spacing, self._current_scale(),
                minor_color=app_settings.current_grid_color(),
                major_color=app_settings.current_grid_major_color(),
            )

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        # The base class opens the library's component menu, which makes no
        # sense here — the inspector is already acting on this component.
        event.ignore()

    def markers_at(self, view_pos) -> list[FindingMarkerItem]:
        """Markers within ``HIT_RADIUS_PX`` of a viewport point.

        Returns all of them rather than the topmost, because findings cluster:
        a port off its edge usually also has a lead endpoint missing it, and
        reporting only one of the two would hide the other.
        """
        r = HIT_RADIUS_PX
        from PySide6.QtCore import QRect
        rect = QRect(view_pos.x() - r, view_pos.y() - r, r * 2, r * 2)
        return [it for it in self.items(rect)
                if isinstance(it, FindingMarkerItem)]

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        super().mouseMoveEvent(event)
        pos = event.position().toPoint()
        hits = self.markers_at(pos)
        if hits:
            seen: list[str] = []
            for it in hits:
                text = it._tooltip_text()
                if text not in seen:
                    seen.append(text)
            QToolTip.showText(event.globalPosition().toPoint(),
                              "<hr>".join(seen), self)
            if self._on_marker_hover:
                self._on_marker_hover(hits[0].finding)
        else:
            QToolTip.hideText()
            if self._on_marker_hover:
                self._on_marker_hover(None)


# ---------------------------------------------------------------------------
# dialog
# ---------------------------------------------------------------------------

MODE_FIXED = "fixed"
MODE_SIDE = "side"
MODE_OVERLAY = "overlay"


class ComponentInspectorDialog(QDialog):
    """Validate one component, preview the suggested fixes, save or fork.

    Modeless, so several components can be compared at once and so the Library
    panel stays usable behind it.
    """

    def __init__(self, path: Path, parent: QWidget | None = None, *,
                 index=None, builtin_root: Path | None = None,
                 overlay_root: Path | None = None,
                 on_saved=None, profile_overrides=None):
        super().__init__(parent)
        self._path = Path(path)
        self._index = index
        self._builtin_root = builtin_root
        self._overlay_root = overlay_root
        self._on_saved = on_saved
        self._profile_overrides = profile_overrides

        self.setWindowTitle(f"Inspect — {self._path.name}")
        self.resize(1040, 720)
        self.setSizeGripEnabled(True)

        self._original_text = self._read()
        self._profile = svglint.profile_for(self._path,
                                            overrides=profile_overrides)
        self._selected_fix_ids: set[str] = set()
        self._findings: list[Finding] = []
        self._candidate = self._original_text
        self._candidate_findings: list[Finding] = []
        self._notes: list[str] = []
        self._mode = MODE_FIXED
        self._markers: list[FindingMarkerItem] = []
        self._recompute_pending = False
        # Set while a list or tree is being rebuilt, so a widget's
        # own change signal cannot re-enter the recompute that is
        # rebuilding it.
        self._populating = False

        self._build_ui()
        self._revalidate_original()
        self._select_default_fixes()
        self._schedule_recompute()
        self._fit_soon()

    # -- io ---------------------------------------------------------------

    def _read(self) -> str:
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("cannot read %s: %s", self._path, exc)
            return ""

    # -- construction -----------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        outer.addLayout(self._build_header())

        split = QSplitter(Qt.Orientation.Horizontal, self)
        self._scene = QGraphicsScene(self)
        self._view = ComponentInspectorView(self._scene, self)
        self._view.set_grid_spacing(self._profile.grid)
        self._view._on_marker_hover = self._highlight_finding_row
        split.addWidget(self._view)

        right = QSplitter(Qt.Orientation.Vertical, self)
        right.addWidget(self._build_findings_box())
        right.addWidget(self._build_fixes_box())
        right.addWidget(self._build_changes_box())
        right.setSizes([260, 240, 200])
        split.addWidget(right)
        split.setSizes([620, 400])
        self._split = split
        outer.addWidget(split, 1)

        outer.addLayout(self._build_buttons())

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._where = QLabel()
        self._where.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self._where, 1)

        row.addWidget(QLabel("Grid:"))
        from PySide6.QtWidgets import QComboBox, QDoubleSpinBox
        self._grid_combo = QComboBox()
        for g in GRID_CHOICES:
            self._grid_combo.addItem(f"{g:g} pt", g)
        self._grid_combo.addItem("Custom…", None)
        start = self._profile.grid
        idx = self._grid_combo.findData(start)
        self._grid_combo.setCurrentIndex(idx if idx >= 0
                                         else self._grid_combo.count() - 1)
        self._grid_combo.currentIndexChanged.connect(self._on_grid_changed)
        row.addWidget(self._grid_combo)

        self._grid_spin = QDoubleSpinBox()
        self._grid_spin.setRange(1.0, 200.0)
        self._grid_spin.setDecimals(2)
        self._grid_spin.setValue(start)
        self._grid_spin.setVisible(self._grid_combo.currentData() is None)
        self._grid_spin.valueChanged.connect(self._view_grid_from_spin)
        row.addWidget(self._grid_spin)

        self._markers_cb = QCheckBox("Show ports")
        self._markers_cb.setChecked(True)
        self._markers_cb.setToolTip(
            "Draw the ports, labels and stretch axes. They are painted behind "
            "the symbol and faded, so a lead end stays readable under its "
            "port marker; switch them off to see the drawing alone.")
        self._markers_cb.toggled.connect(lambda _on: self._rebuild_scene())
        row.addWidget(self._markers_cb)

        self._grid_cb = QCheckBox("Show grid")
        self._grid_cb.setChecked(True)
        self._grid_cb.toggled.connect(lambda on: self._view.set_grid_visible(on))
        row.addWidget(self._grid_cb)

        fit = QPushButton("Fit")
        fit.setAutoDefault(False)
        fit.clicked.connect(lambda: self._view.fit_all())
        row.addWidget(fit)

        recheck = QPushButton("Re-check")
        recheck.setAutoDefault(False)
        recheck.setToolTip("Re-read the file from disk and validate it again — "
                           "use this after editing it in an external editor.")
        recheck.clicked.connect(self.reload_from_disk)
        row.addWidget(recheck)
        return row

    def _build_findings_box(self) -> QWidget:
        box = QGroupBox("Findings")
        v = QVBoxLayout(box)
        self._findings_tree = QTreeWidget()
        self._findings_tree.setHeaderLabels(["Issue", "Check"])
        self._findings_tree.setRootIsDecorated(False)
        self._findings_tree.setColumnWidth(0, 260)
        self._findings_tree.currentItemChanged.connect(self._on_finding_selected)
        v.addWidget(self._findings_tree)
        self._findings_summary = QLabel()
        self._findings_summary.setWordWrap(True)
        v.addWidget(self._findings_summary)
        return box

    def _build_fixes_box(self) -> QWidget:
        box = QGroupBox("Suggested fixes")
        v = QVBoxLayout(box)
        self._fixes_list = QListWidget()
        self._fixes_list.itemChanged.connect(self._on_fix_toggled)
        v.addWidget(self._fixes_list)
        hint = QLabel(
            "Fixes marked ▲ move geometry and are never pre-selected; "
            "check the result in Overlay mode before saving.")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return box

    def _build_changes_box(self) -> QWidget:
        box = QGroupBox("Changes")
        v = QVBoxLayout(box)
        self._notes_label = QLabel()
        self._notes_label.setWordWrap(True)
        v.addWidget(self._notes_label)
        self._diff = QPlainTextEdit()
        self._diff.setReadOnly(True)
        self._diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(10)
        self._diff.setFont(mono)
        v.addWidget(self._diff)
        return box

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("View:"))
        self._mode_group = QButtonGroup(self)
        for label, mode, tip in (
            ("Fixed", MODE_FIXED,
             "The candidate alone. Findings disappear as you tick fixes."),
            ("Side by side", MODE_SIDE,
             "Original and candidate over one continuous grid, so a shift "
             "reads straight off the grid lines."),
            ("Overlay", MODE_OVERLAY,
             "Candidate over a ghost of the original — the only mode in which "
             "a sub-point change is visible at all."),
        ):
            rb = QRadioButton(label)
            rb.setToolTip(tip)
            rb.setAutoExclusive(True)
            rb.setChecked(mode == self._mode)
            rb.toggled.connect(
                lambda on, m=mode: self._set_mode(m) if on else None)
            self._mode_group.addButton(rb)
            row.addWidget(rb)

        row.addStretch(1)
        self._save_btn = QPushButton("Save")
        self._save_btn.setAutoDefault(False)
        self._save_btn.clicked.connect(self._save_in_place)
        row.addWidget(self._save_btn)

        self._save_builtin_btn = QPushButton("Save to Library…")
        self._save_builtin_btn.setAutoDefault(False)
        self._save_builtin_btn.setVisible(False)
        self._save_builtin_btn.clicked.connect(self._save_over_builtin)
        row.addWidget(self._save_builtin_btn)

        self._save_as_btn = QPushButton("Save as New…")
        self._save_as_btn.setAutoDefault(False)
        self._save_as_btn.clicked.connect(self._save_as_new)
        row.addWidget(self._save_as_btn)

        close = QPushButton("Close")
        # setAutoDefault(False) on every button: without it Return inside the
        # dialog closes it, which is wrong when the focus is a fix checkbox.
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        return row

    # -- grid -------------------------------------------------------------

    def _on_grid_changed(self) -> None:
        data = self._grid_combo.currentData()
        self._grid_spin.setVisible(data is None)
        if data is not None:
            self._view.set_grid_spacing(float(data))
        else:
            self._view.set_grid_spacing(self._grid_spin.value())

    def _view_grid_from_spin(self, value: float) -> None:
        if self._grid_combo.currentData() is None:
            self._view.set_grid_spacing(value)

    def _fit_soon(self) -> None:
        QTimer.singleShot(0, self._view.fit_all)

    # -- the recompute loop ----------------------------------------------

    def _revalidate_original(self) -> None:
        self._findings = svglint.check_text(
            self._original_text, path=self._path, profile=self._profile)
        self._where.setText(
            f"<b>{self._path.name}</b> — {self._profile.label} "
            f"(conventions from {self._profile.source}) · {self._path.parent}")

    def _select_default_fixes(self) -> None:
        self._selected_fix_ids = {
            f.fix_id for f in svglint.default_fixes(self._findings)}

    def _schedule_recompute(self) -> None:
        """Coalesce bursts of toggles into one recompute.

        Ticking five boxes should cost one pass, not five. A zero-delay timer
        is enough: the work is a few regex passes plus one parse of a file of a
        few KB, so there is nothing here worth a thread or a progress bar.
        """
        if self._recompute_pending:
            return
        self._recompute_pending = True
        QTimer.singleShot(0, self._recompute)

    def _recompute(self) -> None:
        self._recompute_pending = False
        chosen = [svglint.FIXES_BY_ID[i] for i in self._selected_fix_ids
                  if i in svglint.FIXES_BY_ID]
        # Always from the pristine original: see the module docstring.
        self._candidate, self._notes = svglint.apply_fixes(
            self._original_text, chosen, self._findings,
            profile=self._profile, path=self._path)
        self._candidate_findings = svglint.check_text(
            self._candidate, path=self._path, profile=self._profile)
        self._refresh_fixes_list()
        self._refresh_findings_tree()
        self._refresh_changes()
        self._rebuild_scene()
        self._refresh_save_buttons()

    # -- views ------------------------------------------------------------

    def _refresh_fixes_list(self) -> None:
        """Rebuild the checkbox list to match the current findings.

        Guarded by ``_populating`` as well as ``blockSignals``, because without
        it this method and ``_on_fix_toggled`` close a loop: setting a check
        state emits ``itemChanged``, which schedules a recompute, which calls
        back here. Driven by the event loop that spins at 100% CPU forever —
        which is exactly how it was found. ``blockSignals`` alone is not enough
        to rule it out, since Qt also emits ``itemChanged`` on insertion and the
        placeholder below used to be added after signals were unblocked.
        """
        available = svglint.available_fixes(self._findings)
        self._populating = True
        self._fixes_list.blockSignals(True)
        try:
            self._fixes_list.clear()
            for fix in available:
                mark = " ▲" if fix.moves_geometry else ""
                item = QListWidgetItem(f"{fix.title}{mark}")
                item.setToolTip(fix.description)
                item.setData(Qt.ItemDataRole.UserRole, fix.fix_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if fix.fix_id in self._selected_fix_ids
                    else Qt.CheckState.Unchecked)
                self._fixes_list.addItem(item)
            if not available:
                placeholder = QListWidgetItem(
                    "— no automatic fixes for these findings —")
                placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
                self._fixes_list.addItem(placeholder)
        finally:
            self._fixes_list.blockSignals(False)
            self._populating = False

    def _on_fix_toggled(self, item: QListWidgetItem) -> None:
        if self._populating:
            return
        fix_id = item.data(Qt.ItemDataRole.UserRole)
        if not fix_id:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_fix_ids.add(fix_id)
        else:
            self._selected_fix_ids.discard(fix_id)
        self._schedule_recompute()

    def _refresh_findings_tree(self) -> None:
        tree = self._findings_tree
        self._populating = True
        tree.blockSignals(True)
        tree.clear()
        for f in self._candidate_findings:
            item = QTreeWidgetItem(tree, [
                f"{SEVERITY_GLYPH.get(f.severity, '')} {f.message}", f.check_id])
            item.setForeground(0, SEVERITY_COLOR.get(f.severity, QColor("#333")))
            item.setToolTip(0, f.detail or f.message)
            item.setData(0, Qt.ItemDataRole.UserRole, f)
        tree.blockSignals(False)
        self._populating = False

        errors = sum(1 for f in self._candidate_findings if f.severity == "error")
        warns = sum(1 for f in self._candidate_findings if f.severity == "warning")
        was = len(self._findings)
        now = len(self._candidate_findings)
        if not self._candidate_findings:
            text = ("No findings." if not was
                    else f"All {was} finding(s) resolved by the selected fixes.")
        else:
            text = f"{errors} error(s), {warns} warning(s)"
            if self._selected_fix_ids and now < was:
                text += f" — down from {was} before fixes"
        self._findings_summary.setText(text)

    def _refresh_changes(self) -> None:
        """Show the diff, and the notes — especially when there is no diff.

        A fix that declines explains itself in its notes. Dropping them
        whenever the candidate matched the original meant the user ticked a
        box, read "No changes", and was told nothing: the explanation existed
        and was thrown away. So the notes show either way, and the no-change
        line distinguishes "nothing left to do" from "a fix had something to
        say about why it did nothing".
        """
        notes = "\n".join(f"• {n}" for n in self._notes)
        if self._candidate == self._original_text:
            if self._notes:
                self._notes_label.setText("No change was made, because:\n"
                                          + notes)
            elif self._selected_fix_ids:
                self._notes_label.setText(
                    "The selected fixes found nothing left to change.")
            else:
                self._notes_label.setText("No changes — the file would be "
                                          "saved exactly as it is.")
            self._diff.setPlainText("")
            return
        self._notes_label.setText(notes or "(no notes)")
        diff = difflib.unified_diff(
            self._original_text.splitlines(keepends=True),
            self._candidate.splitlines(keepends=True),
            fromfile=f"{self._path.name} (on disk)",
            tofile=f"{self._path.name} (with fixes)",
            n=2,
        )
        self._diff.setPlainText("".join(diff))

    def _on_finding_selected(self, current, _previous) -> None:
        if self._populating:
            return
        finding = (current.data(0, Qt.ItemDataRole.UserRole)
                   if current is not None else None)
        for m in self._markers:
            m.set_emphasis(finding is not None and m.finding is finding)

    def _highlight_finding_row(self, finding: Finding | None) -> None:
        """Hovering a marker selects its row, and vice versa.

        The two-way link is what makes a list of nine findings navigable: the
        message explains the marker, and the marker locates the message.
        """
        tree = self._findings_tree
        if finding is None:
            return
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) is finding:
                if tree.currentItem() is not item:
                    tree.setCurrentItem(item)
                return

    # -- scene ------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self._rebuild_scene()

    def _add_component(self, text: str, findings, *, x: float,
                       caption: str, ghost: bool = False) -> None:
        from diagrammer.models.component_def import ComponentDef

        try:
            cdef = ComponentDef.from_svg_text(text, path=self._path)
            w, h = cdef.width, cdef.height
            svg_bytes = render_component_bytes(
                text, show_markers=self._markers_cb.isChecked())
        except Exception as exc:
            # A file that does not parse is the single most likely thing to be
            # inspected, so say so in the view rather than showing blank space.
            logger.debug("cannot render %s: %s", caption, exc, exc_info=True)
            label = self._scene.addText(f"{caption or 'component'}: "
                                        f"cannot render — {exc}")
            label.setPos(x, 0)
            return

        item = _SvgItem(svg_bytes, w, h)
        item.setPos(x, 0)
        if ghost:
            item.setOpacity(GHOST_OPACITY)
            item.setZValue(-1)
        self._scene.addItem(item)

        frame = _FrameItem(w, h, caption)
        frame.setPos(x, 0)
        if ghost:
            frame.setOpacity(GHOST_OPACITY)
        self._scene.addItem(frame)

        if ghost:
            return  # markers belong to the live copy only
        for f in findings:
            for marker in f.markers:
                m = FindingMarkerItem(marker, f, (w, h))
                m.setPos(x, 0)
                self._scene.addItem(m)
                self._markers.append(m)

    def _rebuild_scene(self) -> None:
        self._scene.clear()
        self._markers = []
        spacing = self._view._spacing

        if self._mode == MODE_SIDE:
            from diagrammer.models.component_def import ComponentDef
            try:
                width = ComponentDef.from_svg_text(
                    self._original_text, path=self._path).width
            except Exception:
                width = 100.0
            self._add_component(self._original_text, self._findings,
                                x=0.0, caption="Original")
            self._add_component(self._candidate, self._candidate_findings,
                                x=width + spacing, caption="Fixed")
        elif self._mode == MODE_OVERLAY:
            self._add_component(self._original_text, [], x=0.0,
                                caption="", ghost=True)
            self._add_component(self._candidate, self._candidate_findings,
                                x=0.0, caption="Fixed over original")
        else:
            self._add_component(self._candidate, self._candidate_findings,
                                x=0.0, caption=self._path.name)

        self._scene.setSceneRect(
            self._scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))
        self._view.viewport().update()

    # -- reload, for the external-editor loop -----------------------------

    def start_watching(self) -> None:
        """Notice when an external editor saves the file under us.

        Needed because "Open in Illustrator" and "Inspect" are two halves of
        one workflow. The file is *not* reloaded automatically: that would
        discard the user's fix selections without being asked. A bar appears
        and they decide.
        """
        from PySide6.QtCore import QFileSystemWatcher

        self._watcher = QFileSystemWatcher([str(self._path)], self)
        self._watcher.fileChanged.connect(self._on_file_changed)

    def _on_file_changed(self, _path: str) -> None:
        if getattr(self, "_reload_bar", None) is not None:
            return
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 2, 6, 2)
        label = QLabel("This file changed on disk.")
        row.addWidget(label, 1)
        reload_btn = QPushButton("Reload")
        reload_btn.setAutoDefault(False)
        reload_btn.clicked.connect(self.reload_from_disk)
        row.addWidget(reload_btn)
        dismiss = QPushButton("Ignore")
        dismiss.setAutoDefault(False)
        dismiss.clicked.connect(self._dismiss_reload_bar)
        row.addWidget(dismiss)
        bar.setStyleSheet("background: #FFF3CD; border: 1px solid #E0C87A;")
        self._reload_bar = bar
        self.layout().insertWidget(1, bar)
        # Some editors write by replace-and-rename, which drops the watch.
        if hasattr(self, "_watcher"):
            self._watcher.addPath(str(self._path))

    def _show_banner(self, message: str) -> None:
        """A dismissable inline note, in place of a modal for a success path."""
        self._dismiss_banner()
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 2, 6, 2)
        label = QLabel(message)
        label.setWordWrap(True)
        row.addWidget(label, 1)
        ok = QPushButton("Dismiss")
        ok.setAutoDefault(False)
        ok.clicked.connect(self._dismiss_banner)
        row.addWidget(ok)
        bar.setStyleSheet("background: #DFF2E1; border: 1px solid #9ACBA5;")
        self._banner = bar
        self.layout().insertWidget(1, bar)

    def _dismiss_banner(self) -> None:
        bar = getattr(self, "_banner", None)
        if bar is not None:
            bar.setParent(None)
            bar.deleteLater()
            self._banner = None

    def _dismiss_reload_bar(self) -> None:
        bar = getattr(self, "_reload_bar", None)
        if bar is not None:
            bar.setParent(None)
            bar.deleteLater()
            self._reload_bar = None

    def reload_from_disk(self) -> None:
        """Re-read the file and re-run the checks, keeping what fixes we can.

        Selections are re-applied **by id**, because the findings are recomputed
        and a fix that no longer applies has simply disappeared — silently
        carrying a stale selection would make the preview describe a fix that
        is not there.
        """
        self._dismiss_reload_bar()
        wanted = set(self._selected_fix_ids)
        self._original_text = self._read()
        self._profile = svglint.profile_for(self._path,
                                            overrides=self._profile_overrides)
        self._view.set_grid_spacing(
            self._grid_combo.currentData() or self._grid_spin.value())
        self._revalidate_original()
        still = {f.fix_id for f in svglint.available_fixes(self._findings)}
        self._selected_fix_ids = (wanted & still) | {
            f.fix_id for f in svglint.default_fixes(self._findings)
            if f.fix_id not in wanted and not wanted}
        self._schedule_recompute()

    # -- saving -----------------------------------------------------------

    @property
    def _builtin_is_writable(self) -> bool:
        """Can the shipped tree actually be written to?

        True in a source checkout, false in an installed ``.app`` — the bundle
        is read-only and code-signed, so writing into it would fail and, if it
        somehow did not, would break the signature. Curating the shipped
        libraries is a real workflow, but only where the tree is editable, so
        the entry appears only there.
        """
        if not self._target_is_builtin:
            return False
        return os.access(self._path.parent, os.W_OK) and os.access(
            self._path, os.W_OK)

    @property
    def _target_is_builtin(self) -> bool:
        if self._builtin_root is None:
            return False
        try:
            self._path.resolve().relative_to(self._builtin_root.resolve())
            return True
        except (ValueError, OSError):
            return False

    def _refresh_save_buttons(self) -> None:
        """Relabel Save so a fork is announced before it happens, not after.

        Editing a shipped component never fails and never asks permission — the
        tree may be inside a signed, read-only .app bundle, so a copy is the
        only thing that *can* happen — but the user should know the result is
        their own copy rather than a change to the app.
        """
        dirty = self._candidate != self._original_text
        writable = self._builtin_is_writable
        self._save_builtin_btn.setVisible(writable)
        self._save_builtin_btn.setEnabled(dirty)
        if writable:
            self._save_builtin_btn.setToolTip(
                "Overwrite the shipped file itself, in "
                f"{self._path.parent}.\n\nOnly offered because this copy of "
                "Diagrammer runs from a writable source tree. The change is to "
                "the library everyone gets, so commit it — reinstalling or "
                "updating replaces the file.")
        if self._target_is_builtin:
            self._save_btn.setText("Save (creates your copy)")
            self._save_btn.setToolTip(
                "This component ships with Diagrammer, so it cannot be changed "
                "in place. Saving writes your own copy to "
                f"{self._overlay_root or '~/.diagrammer/components'} and that "
                "copy is used from then on. The shipped one is left alone, and "
                "you can revert at any time.")
        else:
            self._save_btn.setText("Save")
            self._save_btn.setToolTip(
                "Overwrite this file. A timestamped backup is kept.")
        self._save_btn.setEnabled(dirty)

    def _write(self, *, as_new: bool, force_in_place: bool = False) -> None:
        from diagrammer import __version__
        from diagrammer.io import component_writer as writer

        if self._index is None:
            QMessageBox.warning(self, "Cannot Save",
                                "This inspector was opened without a library "
                                "override index, so it cannot save.")
            return

        moves = any(svglint.FIXES_BY_ID[i].moves_geometry
                    for i in self._selected_fix_ids
                    if i in svglint.FIXES_BY_ID)
        try:
            if as_new:
                target_dir, name = self._ask_new_location()
                if target_dir is None:
                    return
                result = writer.save_as_new(
                    self._candidate, target_dir, name,
                    original_text=self._original_text, index=self._index,
                    app_version=__version__, allow_shrink=moves)
            else:
                result = writer.save_component_text(
                    self._candidate, self._path,
                    original_text=self._original_text, index=self._index,
                    builtin_root=None if force_in_place else (
                        self._builtin_root if self._target_is_builtin
                        else None),
                    app_version=__version__, allow_shrink=moves)
        except writer.WriteRefused as exc:
            QMessageBox.critical(self, "Save Refused", str(exc))
            return
        except OSError as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
            return

        # Reported inline rather than in a modal. A modal after every
        # successful save is noise the user has to dismiss, and the Save button
        # already said "creates your copy" before they pressed it; the caller
        # also gets the result and puts it in the status bar.
        if result.forked:
            self._show_banner(
                f"Your copy is now at {result.path}. It takes the place of the "
                "version that ships with Diagrammer, which is unchanged — and "
                "if a future update changes it you will be told.")
        else:
            where = f" (backup: {result.backup.name})" if result.backup else ""
            self._show_banner(f"Saved {result.path.name}.{where}")
        if self._on_saved is not None:
            self._on_saved(result)

        if as_new or result.forked:
            # The file we are inspecting is no longer the file we wrote, so
            # follow the new one rather than leaving a stale path behind.
            self._path = result.path
            self.setWindowTitle(f"Inspect — {self._path.name}")
            if hasattr(self, "_watcher"):
                self._watcher.addPath(str(self._path))
        self._original_text = self._candidate
        self._revalidate_original()
        # Synchronously, not via the debounced recompute: after a fork the
        # target is no longer a built-in, and a Save button still offering to
        # "create your copy" would be lying about what the next press does.
        self._refresh_save_buttons()
        self._schedule_recompute()

    def _save_in_place(self) -> None:
        self._write(as_new=False)

    def _save_over_builtin(self) -> None:
        """Overwrite the shipped file itself rather than forking.

        The author's path, not the user's: it appears only when the component
        tree is writable, which means a source checkout. ``_write`` forks
        whenever it is handed a ``builtin_root``, so declining to pass one is
        all this takes.
        """
        reply = QMessageBox.question(
            self, "Overwrite the Shipped Component?",
            "Write these changes straight into the component library?"
            f"<br><br><code>{self._path}</code><br><br>"
            "This edits the file that ships with Diagrammer rather than making "
            "your own copy, so the change reaches every user once released — "
            "and is lost on reinstall unless it is committed. A timestamped "
            "backup is kept either way.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._write(as_new=False, force_in_place=True)

    def _save_as_new(self) -> None:
        self._write(as_new=True)

    def _ask_new_location(self) -> tuple[Path | None, str]:
        """Pick a folder and name for Save as New.

        Defaults to the overlay rather than the source folder: the source may
        be read-only, and the overlay is the one place a user component is
        always allowed and always scanned.
        """
        default_dir = self._overlay_root or self._path.parent
        if self._target_is_builtin and self._overlay_root is not None:
            default_dir = self._overlay_root
            if self._builtin_root is not None:
                try:
                    rel = self._path.resolve().relative_to(
                        self._builtin_root.resolve()).parent
                    default_dir = self._overlay_root / rel
                except (ValueError, OSError):
                    pass
        existing = {p.stem for p in default_dir.glob("*.svg")} \
            if default_dir.is_dir() else set()
        from diagrammer.io.component_writer import suggest_new_name
        suggested = suggest_new_name(self._path, existing)

        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save Component As New",
            str(default_dir / f"{suggested}.svg"),
            "SVG Files (*.svg);;All Files (*)")
        if not chosen:
            return None, ""
        target = Path(chosen)
        return target.parent, target.stem
