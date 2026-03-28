"""LibraryPanel — component library with search, dense view, recently used, and favorites."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diagrammer.models.component_def import ComponentDef
from diagrammer.models.library import ComponentLibrary

# Drag MIME type for component library keys
COMPONENT_MIME_TYPE = "application/x-diagrammer-component"

# Thumbnail sizes
THUMB_SIZE = QSize(48, 48)
DENSE_THUMB_SIZE = QSize(40, 40)

# Favorites/recents persistence file
_PREFS_FILE = Path.home() / ".diagrammer" / "library_prefs.json"


class LibraryPanel(QDockWidget):
    """Dock widget showing the component library with search, dense view, recents, and favorites."""

    def __init__(self, library: ComponentLibrary, parent=None):
        super().__init__("Component Library", parent)
        self._library = library
        self._favorites: set[str] = set()   # set of library keys
        self._recents: list[str] = []       # ordered list of library keys (most recent first)
        self._load_prefs()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # -- Search bar --
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search components\u2026")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # -- View toggle (segmented control) --
        from PySide6.QtWidgets import QButtonGroup
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(0)
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)

        self._tree_btn = QPushButton("List")
        self._tree_btn.setCheckable(True)
        self._tree_btn.setChecked(True)
        self._tree_btn.setFixedHeight(22)
        self._tree_btn.setStyleSheet(
            "QPushButton { border: 1px solid #999; border-right: none; "
            "border-radius: 0; padding: 2px 8px; }"
            "QPushButton:checked { background: #0078D4; color: white; }"
        )
        self._view_group.addButton(self._tree_btn, 0)
        toggle_row.addWidget(self._tree_btn)

        self._grid_btn = QPushButton("Grid")
        self._grid_btn.setCheckable(True)
        self._grid_btn.setFixedHeight(22)
        self._grid_btn.setStyleSheet(
            "QPushButton { border: 1px solid #999; "
            "border-radius: 0; padding: 2px 8px; }"
            "QPushButton:checked { background: #0078D4; color: white; }"
        )
        self._view_group.addButton(self._grid_btn, 1)
        toggle_row.addWidget(self._grid_btn)

        toggle_row.addStretch()
        self._view_group.idClicked.connect(
            lambda idx: self._set_view_mode("tree" if idx == 0 else "grid")
        )
        layout.addLayout(toggle_row)

        # -- Stacked views --
        self._stack = QStackedWidget()

        # Tree view (list mode)
        self._tree = _DragTree(library, self._favorites)
        self._stack.addWidget(self._tree)

        # Grid view (dense mode)
        self._grid = _DragGrid(library)
        self._stack.addWidget(self._grid)

        layout.addWidget(self._stack)

        self.setWidget(container)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

    def record_use(self, key: str) -> None:
        """Record a component as recently used."""
        if key in self._recents:
            self._recents.remove(key)
        self._recents.insert(0, key)
        self._recents = self._recents[:20]  # keep last 20
        self._tree.populate(self._library, self._favorites, self._recents)
        self._save_prefs()

    def toggle_favorite(self, key: str) -> None:
        """Toggle a component's favorite status."""
        if key in self._favorites:
            self._favorites.discard(key)
        else:
            self._favorites.add(key)
        self._tree.populate(self._library, self._favorites, self._recents)
        self._save_prefs()

    def reload(self, root: Path) -> None:
        self._library.scan(root)
        self._tree.populate(self._library, self._favorites, self._recents)
        self._grid.populate(self._library)

    # -- View mode --

    def _set_view_mode(self, mode: str) -> None:
        self._stack.setCurrentIndex(0 if mode == "tree" else 1)
        from diagrammer.panels.settings_dialog import app_settings
        app_settings.library_view_mode = mode
        app_settings.save()

    # -- Search --

    def _on_search(self, text: str) -> None:
        text = text.strip().lower()
        if not text:
            self._tree.populate(self._library, self._favorites, self._recents)
            self._grid.populate(self._library)
            return
        # Filter components matching the search
        filtered = ComponentLibrary()
        for cat, defs in self._library.categories.items():
            matches = [d for d in defs if text in d.name.lower() or text in cat.lower()]
            if matches:
                filtered._categories[cat] = matches
                for d in matches:
                    filtered._by_key[f"{cat}/{d.name}"] = d
        self._tree.populate(filtered, self._favorites, self._recents)
        self._grid.populate(filtered)

    # -- Persistence --

    def _load_prefs(self) -> None:
        try:
            if _PREFS_FILE.exists():
                data = json.loads(_PREFS_FILE.read_text())
                self._favorites = set(data.get("favorites", []))
                self._recents = data.get("recents", [])
        except Exception:
            pass

    def _save_prefs(self) -> None:
        try:
            _PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PREFS_FILE.write_text(json.dumps({
                "favorites": sorted(self._favorites),
                "recents": self._recents,
            }))
        except Exception:
            pass


class _DragTree(QTreeWidget):
    """Tree view with categories, favorites section, and recently used section."""

    def __init__(self, library: ComponentLibrary, favorites: set[str], parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setIconSize(THUMB_SIZE)
        self.setDragEnabled(True)
        self._favorites = favorites
        self.populate(library, favorites, [])

    def populate(self, library: ComponentLibrary, favorites: set[str] | None = None, recents: list[str] | None = None) -> None:
        self.clear()
        if favorites is not None:
            self._favorites = favorites
        fav_set = self._favorites
        recent_list = recents or []

        # Favorites section
        fav_defs = [library.get(k) for k in sorted(fav_set) if library.get(k)]
        if fav_defs:
            fav_item = QTreeWidgetItem(self, ["\u2605 Favorites"])
            fav_item.setFlags(fav_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            for comp_def in fav_defs:
                self._add_comp_child(fav_item, comp_def)
            fav_item.setExpanded(True)

        # Recently used section
        recent_defs = []
        seen = set()
        for k in recent_list:
            if k not in seen:
                d = library.get(k)
                if d:
                    recent_defs.append(d)
                    seen.add(k)
            if len(recent_defs) >= 10:
                break
        if recent_defs:
            rec_item = QTreeWidgetItem(self, ["\u23f0 Recently Used"])
            rec_item.setFlags(rec_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            for comp_def in recent_defs:
                self._add_comp_child(rec_item, comp_def)
            rec_item.setExpanded(True)

        # Regular categories
        for category, defs in sorted(library.categories.items()):
            cat_item = QTreeWidgetItem(self, [category.replace("_", " ").title()])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            for comp_def in defs:
                self._add_comp_child(cat_item, comp_def)
            cat_item.setExpanded(True)

    def _add_comp_child(self, parent_item: QTreeWidgetItem, comp_def: ComponentDef) -> None:
        key = f"{comp_def.category}/{comp_def.name}"
        label = comp_def.name.replace("_", " ").title()
        if key in self._favorites:
            label = f"\u2605 {label}"
        child = QTreeWidgetItem(parent_item, [label])
        child.setData(0, Qt.ItemDataRole.UserRole, key)
        child.setIcon(0, _make_icon(comp_def))
        child.setToolTip(0, f"{key}\nPorts: {', '.join(p.name for p in comp_def.ports)}")

    def startDrag(self, supportedActions) -> None:
        item = self.currentItem()
        if item is None:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key is None:
            return
        mime = QMimeData()
        mime.setData(COMPONENT_MIME_TYPE, key.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = item.icon(0)
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(THUMB_SIZE))
        drag.exec(Qt.DropAction.CopyAction)

    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        if item is None:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key is None:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        is_fav = key in self._favorites
        fav_act = menu.addAction("\u2605 Remove from Favorites" if is_fav else "\u2606 Add to Favorites")
        action = menu.exec(event.globalPos())
        if action is fav_act:
            # Notify the panel to toggle
            panel = self.parent()
            while panel and not isinstance(panel, LibraryPanel):
                panel = panel.parent()
            if isinstance(panel, LibraryPanel):
                panel.toggle_favorite(key)


class _DragGrid(QListWidget):
    """Dense Nx3 grid view of component thumbnails."""

    def __init__(self, library: ComponentLibrary, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(DENSE_THUMB_SIZE)
        self.setGridSize(QSize(DENSE_THUMB_SIZE.width() + 8, DENSE_THUMB_SIZE.height() + 8))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setWrapping(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSpacing(2)
        self.populate(library)

    def populate(self, library: ComponentLibrary) -> None:
        self.clear()
        for _cat, defs in sorted(library.categories.items()):
            for comp_def in defs:
                key = f"{comp_def.category}/{comp_def.name}"
                item = QListWidgetItem(_make_icon(comp_def), "")
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip(comp_def.name.replace("_", " ").title())
                self.addItem(item)

    def startDrag(self, supportedActions) -> None:
        item = self.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key is None:
            return
        mime = QMimeData()
        mime.setData(COMPONENT_MIME_TYPE, key.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = item.icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(DENSE_THUMB_SIZE))
        drag.exec(Qt.DropAction.CopyAction)


def _make_icon(comp_def: ComponentDef) -> QIcon:
    """Render the component SVG into a small QIcon thumbnail, preserving aspect ratio."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter

    from diagrammer.items.component_item import ComponentItem
    svg_bytes = ComponentItem._prepare_svg_bytes(comp_def.svg_path)
    renderer = QSvgRenderer(svg_bytes)
    pixmap = QPixmap(THUMB_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)

    vb = renderer.viewBox()
    if vb.isValid() and vb.width() > 0 and vb.height() > 0:
        svg_aspect = vb.width() / vb.height()
    else:
        svg_aspect = comp_def.width / comp_def.height if comp_def.height > 0 else 1.0

    tw, th = float(THUMB_SIZE.width()), float(THUMB_SIZE.height())
    thumb_aspect = tw / th

    if svg_aspect > thumb_aspect:
        draw_w = tw
        draw_h = tw / svg_aspect
    else:
        draw_h = th
        draw_w = th * svg_aspect

    target_rect = QRectF((tw - draw_w) / 2, (th - draw_h) / 2, draw_w, draw_h)

    painter = QPainter(pixmap)
    renderer.render(painter, target_rect)
    painter.end()
    return QIcon(pixmap)
