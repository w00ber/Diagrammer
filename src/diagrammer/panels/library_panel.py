"""LibraryPanel — component library with search, grouped grid/list views, and drag-and-drop."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diagrammer.models.component_def import ComponentDef
from diagrammer.models.library import ComponentLibrary
from diagrammer.panels.open_with import add_open_with_actions

logger = logging.getLogger(__name__)

COMPONENT_MIME_TYPE = "application/x-diagrammer-component"
THUMB_SIZE = QSize(48, 48)
DENSE_THUMB_SIZE = QSize(40, 40)
_PREFS_FILE = Path.home() / ".diagrammer" / "library_prefs.json"


class LibraryPanel(QDockWidget):
    """Dock widget showing the component library with search, grouped views, and reorderable categories."""

    table_view_requested = Signal(str)  # emits category path

    def __init__(self, library: ComponentLibrary, parent=None):
        super().__init__("Component Library", parent)
        self._library = library
        self._favorites: set[str] = set()
        self._recents: list[str] = []
        self._category_order: list[str] = []  # user-defined category order
        # Set by MainWindow. None leaves the context menus read-only-but-usable,
        # which is what lets the panel be constructed in a test without a window.
        self.component_actions = None
        self._load_prefs()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Search bar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search components\u2026")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # View toggle
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

        table_all_btn = QPushButton("View All as Table")
        table_all_btn.setFixedHeight(22)
        table_all_btn.setStyleSheet(
            "QPushButton { border: 1px solid #999; border-radius: 3px; "
            "padding: 2px 8px; font-size: 11px; }"
            "QPushButton:hover { background: #e0e0e0; }"
        )
        table_all_btn.clicked.connect(lambda: self.open_library_table("__all__"))
        toggle_row.addWidget(table_all_btn)

        self._view_group.idClicked.connect(
            lambda idx: self._set_view_mode("tree" if idx == 0 else "grid")
        )
        layout.addLayout(toggle_row)

        # Stacked views
        self._stack = QStackedWidget()

        self._tree = _DragTree(library, self._favorites)
        self._stack.addWidget(self._tree)

        self._grid = _GroupedGrid(library, self)
        self._stack.addWidget(self._grid)

        layout.addWidget(self._stack)

        self.setWidget(container)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

    def apply_light_surface(self, color) -> None:  # noqa: ANN001
        """Pin the library views to a light surface color.

        Called at startup and after a theme switch so the tree, grid,
        and scroll area stay on a light background regardless of the
        current app palette. The diagram artwork and component SVGs are
        authored for a light background, so previewing them on a dark
        surface would hurt legibility.

        ``color`` is a ``QColor``; we accept it untyped to avoid having
        to import ``QColor`` at module scope just for a type hint.
        """
        hex_color = color.name()
        is_dark = color.lightness() < 128
        text_color = "#ffffff" if is_dark else "#000000"
        alt_color = (
            color.lighter(115).name() if is_dark else color.darker(103).name()
        )
        # Stylesheet rules take precedence over QPalette, so setting
        # them on the dock widget reliably overrides any dark palette
        # that QApplication.setPalette() pushed down the widget tree.
        # The selector list covers:
        #   QTreeWidget        — the "List" tab (_DragTree)
        #   QListWidget        — favorites / recents list in the tree tab
        #   QScrollArea        — the "Grid" tab (_GroupedGrid) frame
        #   QScrollArea > QWidget > QWidget
        #                       — the grid's inner viewport widget
        self.setStyleSheet(
            f"QTreeWidget, QListWidget {{"
            f" background-color: {hex_color};"
            f" color: {text_color};"
            f" alternate-background-color: {alt_color}; }}"
            f"QScrollArea {{ background-color: {hex_color}; }}"
            f"QScrollArea > QWidget > QWidget {{"
            f" background-color: {hex_color}; color: {text_color}; }}"
        )

    def record_use(self, key: str) -> None:
        if key in self._recents:
            self._recents.remove(key)
        self._recents.insert(0, key)
        self._recents = self._recents[:20]
        self._tree.populate(self._library, self._favorites, self._recents)
        self._save_prefs()

    def is_favorite(self, key: str) -> bool:
        return key in self._favorites

    def toggle_favorite(self, key: str) -> None:
        if key in self._favorites:
            self._favorites.discard(key)
        else:
            self._favorites.add(key)
        self._save_prefs()
        # Refresh through _refresh_views so the rebuilt tree keeps the
        # current visibility filter and category order — repopulating from
        # self._library directly would resurrect hidden categories.
        self._refresh_views()

    def selected_key(self) -> str | None:
        """The library key of the highlighted component, if one is.

        Only the tree view has a selection model; the grid view selects by
        click-to-drag and keeps none, so this returns None there and the
        caller falls back to the canvas selection.
        """
        tree = getattr(self, "_tree", None)
        if tree is None:
            return None
        item = tree.currentItem()
        if item is None:
            return None
        key = item.data(0, Qt.ItemDataRole.UserRole)
        return key if isinstance(key, str) and key else None

    def _sorted_categories(self, library: ComponentLibrary) -> list[str]:
        """Return categories in user-defined order, with any new ones appended."""
        all_cats = sorted(library.categories.keys())
        ordered = [c for c in self._category_order if c in all_cats]
        for c in all_cats:
            if c not in ordered:
                ordered.append(c)
        return ordered

    def _visible_library(self) -> ComponentLibrary:
        """Return a filtered library with hidden categories removed."""
        from diagrammer.panels.settings_dialog import app_settings
        if not app_settings.hidden_libraries:
            return self._library
        filtered = ComponentLibrary()
        for cat, defs in self._library.categories.items():
            if cat not in app_settings.hidden_libraries:
                filtered._categories[cat] = defs
                for d in defs:
                    filtered._by_key[f"{cat}/{d.name}"] = d
        return filtered

    def _refresh_views(self) -> None:
        """Refresh both views with current visibility and ordering."""
        lib = self._visible_library()
        order = self._sorted_categories(lib)
        self._tree.populate(lib, self._favorites, self._recents, category_order=order)
        self._grid.populate(lib, category_order=order)

    def move_category(self, category: str, direction: int) -> None:
        """Move a category up (-1) or down (+1) in the display order."""
        order = self._sorted_categories(self._library)
        idx = order.index(category) if category in order else -1
        if idx < 0:
            return
        new_idx = max(0, min(len(order) - 1, idx + direction))
        if new_idx == idx:
            return
        order.insert(new_idx, order.pop(idx))
        self._category_order = order
        self._save_prefs()
        self._refresh_views()

    def _set_view_mode(self, mode: str) -> None:
        self._stack.setCurrentIndex(0 if mode == "tree" else 1)
        from diagrammer.panels.settings_dialog import app_settings
        app_settings.library_view_mode = mode
        app_settings.save()

    def _on_search(self, text: str) -> None:
        text = text.strip().lower()
        if not text:
            self._refresh_views()
            return
        base = self._visible_library()
        filtered = ComponentLibrary()
        for cat, defs in base.categories.items():
            matches = [d for d in defs if text in d.name.lower() or text in cat.lower()]
            if matches:
                filtered._categories[cat] = matches
                for d in matches:
                    filtered._by_key[f"{cat}/{d.name}"] = d
        self._tree.populate(filtered, self._favorites, self._recents)
        self._grid.populate(filtered)

    def _load_prefs(self) -> None:
        try:
            if _PREFS_FILE.exists():
                data = json.loads(_PREFS_FILE.read_text())
                self._favorites = set(data.get("favorites", []))
                self._recents = data.get("recents", [])
                self._category_order = data.get("category_order", [])
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            logger.debug("Failed to load library prefs: %s", exc)

    def _save_prefs(self) -> None:
        try:
            _PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PREFS_FILE.write_text(json.dumps({
                "favorites": sorted(self._favorites),
                "recents": self._recents,
                "category_order": self._category_order,
            }))
        except OSError as exc:
            logger.debug("Failed to save library prefs: %s", exc)

    def open_library_table(self, category: str) -> None:
        """Request the main window to open a library table tab for *category*."""
        self.table_view_requested.emit(category)


# =========================================================================
# Shared component context menu
# =========================================================================

class ComponentMenu:
    """The right-click menu for one library component, plus its dispatch.

    Returns an object rather than a tuple of actions because the entries now
    outnumber what a call site should have to unpack, and because all three
    views — the List tree, the Grid thumbnails and the Table view — must offer
    exactly the same menu. ``handle()`` does the work, so a new entry is added
    once here instead of three times at the call sites.
    """

    def __init__(self, parent, comp_def: ComponentDef, is_favorite: bool,
                 *, panel=None, key: str = ""):
        from PySide6.QtWidgets import QMenu

        self._parent = parent
        self._panel = panel
        self._comp = comp_def
        self._key = key or (f"{comp_def.category}/{comp_def.name}"
                            if comp_def is not None else "")
        actions = getattr(panel, "component_actions", None)
        self._actions = actions

        self.menu = QMenu(parent)
        self.inspect_act = None
        self.move_act = None
        self.remove_act = None
        self.revert_act = None
        self.compare_act = None

        has_file = (comp_def is not None and comp_def.svg_path
                    and not comp_def.is_embedded)

        if actions is not None and has_file:
            self.inspect_act = self.menu.addAction(
                "Inspect && Validate\u2026")
            self.inspect_act.setToolTip(
                "Check this component against its library's conventions")
            self.menu.addSeparator()

        self.fav_act = self.menu.addAction(
            "\u2605 Remove from Favorites" if is_favorite
            else "\u2606 Add to Favorites")

        if actions is not None and has_file:
            self.menu.addSeparator()
            self.move_act = self.menu.addAction("Move to Category\u2026")
            # Named for what it does to *this* component, which differs: a
            # shipped part can only be hidden, a user's own can be deleted.
            self.remove_act = self.menu.addAction(
                "Hide from Library\u2026" if comp_def.is_builtin
                else "Remove\u2026")
            if actions.can_revert(comp_def):
                self.compare_act = self.menu.addAction(
                    "Compare with Shipped Version\u2026")
                self.revert_act = self.menu.addAction(
                    "Revert to Shipped Version\u2026")

        self.show_act = None
        if has_file:
            self.menu.addSeparator()
            add_open_with_actions(self.menu, comp_def.svg_path, parent)
            self.show_act = self.menu.addAction("Show in File Browser")

    def exec(self, global_pos):
        return self.handle(self.menu.exec(global_pos))

    def handle(self, action) -> bool:
        """Run whichever entry was chosen. True if one was."""
        if action is None:
            return False
        if action is self.fav_act:
            if self._panel is not None:
                self._panel.toggle_favorite(self._key)
            return True
        if self.show_act is not None and action is self.show_act:
            _show_in_file_browser(self._comp.svg_path.parent)
            return True
        a = self._actions
        if a is None:
            return False
        if action is self.inspect_act:
            a.inspect(self._comp)
        elif action is self.move_act:
            a.move_to_category(self._comp, self._parent)
        elif action is self.remove_act:
            a.remove(self._comp, self._parent)
        elif action is self.revert_act:
            a.revert_to_builtin(self._comp, self._parent)
        elif action is self.compare_act:
            a.compare_with_builtin(self._comp)
        else:
            return False
        return True


def build_component_menu(parent, comp_def: ComponentDef, is_favorite: bool,
                         *, panel=None, key: str = ""):
    """Build the right-click menu for a single library component.

    Shared by the List tree, the Grid thumbnails and the Table view so all
    three offer the same entries. ``panel`` supplies the favorites and, through
    ``panel.component_actions``, the library-management entries; without it the
    menu degrades to the read-only entries rather than failing.
    """
    return ComponentMenu(parent, comp_def, is_favorite, panel=panel, key=key)


class CategoryMenu:
    """The right-click menu for a category node.

    Extracted from the tree's ``contextMenuEvent`` so that "Validate this
    category" has somewhere to live and so the grid view can grow the same menu
    without duplicating it.
    """

    def __init__(self, parent, category: str, library: ComponentLibrary | None,
                 *, panel=None):
        from PySide6.QtWidgets import QMenu

        self._parent = parent
        self._category = category
        self._panel = panel
        self._defs = (library.categories.get(category, []) if library else [])

        self.menu = QMenu(parent)
        self.table_act = self.menu.addAction("View as Table")
        self.validate_act = None
        if panel is not None and getattr(panel, "component_actions", None) \
                is not None and self._defs:
            self.validate_act = self.menu.addAction(
                "Validate This Category\u2026")
        self.show_act = None
        if self._defs and self._defs[0].svg_path:
            self.menu.addSeparator()
            self.show_act = self.menu.addAction("Show in File Browser")
        self.menu.addSeparator()
        self.up_act = self.menu.addAction("\u2191 Move Up")
        self.down_act = self.menu.addAction("\u2193 Move Down")

    def exec(self, global_pos):
        return self.handle(self.menu.exec(global_pos))

    def handle(self, action) -> bool:
        if action is None:
            return False
        panel = self._panel
        if action is self.show_act:
            _show_in_file_browser(self._defs[0].svg_path.parent)
            return True
        if panel is None:
            return False
        if action is self.table_act:
            panel.open_library_table(self._category)
        elif action is self.up_act:
            panel.move_category(self._category, -1)
        elif action is self.down_act:
            panel.move_category(self._category, 1)
        elif action is self.validate_act:
            panel.component_actions.validate_category(
                self._category, self._defs)
        else:
            return False
        return True


def build_category_menu(parent, category: str,
                        library: ComponentLibrary | None, *, panel=None):
    """Build the right-click menu for a category node."""
    return CategoryMenu(parent, category, library, panel=panel)


# =========================================================================
# Tree view (list mode)
# =========================================================================

class _DragTree(QTreeWidget):
    def __init__(self, library: ComponentLibrary, favorites: set[str], parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setIconSize(THUMB_SIZE)
        self.setDragEnabled(True)
        self._library = library
        self._favorites = favorites
        self.populate(library, favorites, [])

    def populate(self, library: ComponentLibrary, favorites: set[str] | None = None,
                 recents: list[str] | None = None,
                 category_order: list[str] | None = None) -> None:
        self.clear()
        self._library = library
        if favorites is not None:
            self._favorites = favorites
        fav_set = self._favorites
        recent_list = recents or []

        # Favorites
        fav_defs = [library.get(k) for k in sorted(fav_set) if library.get(k)]
        if fav_defs:
            fav_item = QTreeWidgetItem(self, ["\u2605 Favorites"])
            fav_item.setFlags(fav_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            for comp_def in fav_defs:
                self._add_comp_child(fav_item, comp_def)
            fav_item.setExpanded(True)

        # Recently used
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

        # Build hierarchical category tree.
        # Categories like "electrical/LC" produce a parent "electrical" node
        # containing a child "LC" node.  Leaf nodes hold components; intermediate
        # nodes are navigable and support "View as Table" for the whole subtree.
        if category_order:
            cats = [c for c in category_order if c in library.categories]
            for c in sorted(library.categories.keys()):
                if c not in cats:
                    cats.append(c)
        else:
            cats = sorted(library.categories.keys())

        # node_map: path prefix -> QTreeWidgetItem
        node_map: dict[str, QTreeWidgetItem] = {}

        def _get_or_create_node(path: str) -> QTreeWidgetItem:
            if path in node_map:
                return node_map[path]
            parts = path.split("/")
            if len(parts) == 1:
                # Top-level node
                label = parts[0].replace("_", " ")
                node = QTreeWidgetItem(self, [label])
            else:
                parent_path = "/".join(parts[:-1])
                parent = _get_or_create_node(parent_path)
                label = parts[-1].replace("_", " ")
                node = QTreeWidgetItem(parent, [label])
            node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            node.setData(0, Qt.ItemDataRole.UserRole + 1, path)
            node.setExpanded(True)
            node_map[path] = node
            return node

        for category in cats:
            defs = library.categories.get(category, [])
            if not defs:
                continue
            cat_node = _get_or_create_node(category)
            for comp_def in defs:
                self._add_comp_child(cat_node, comp_def)

    def _add_comp_child(self, parent_item: QTreeWidgetItem, comp_def: ComponentDef) -> None:
        key = f"{comp_def.category}/{comp_def.name}"
        label = comp_def.name.replace("_", " ")
        if key in self._favorites:
            label = f"\u2605 {label}"
        child = QTreeWidgetItem(parent_item, [label])
        child.setData(0, Qt.ItemDataRole.UserRole, key)
        child.setIcon(0, _make_icon(comp_def))
        tip = [key, f"Ports: {', '.join(p.name for p in comp_def.ports)}"]
        if comp_def.is_user_owned:
            tip.append("\u25cf Your copy — edits are kept across updates"
                       if comp_def.source_kind == "overlay"
                       else "\u25cf Your component")
        elif comp_def.is_builtin:
            tip.append("Ships with Diagrammer")
        child.setToolTip(0, "\n".join(tip))

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
        raw_cat = item.data(0, Qt.ItemDataRole.UserRole + 1)

        from PySide6.QtWidgets import QMenu

        # Component context menu
        if key is not None:
            comp_def = self._library.get(key) if self._library else None
            if comp_def is None:
                return
            panel = self.parent()
            while panel and not isinstance(panel, LibraryPanel):
                panel = panel.parent()
            build_component_menu(
                self, comp_def, key in self._favorites,
                panel=panel if isinstance(panel, LibraryPanel) else None,
                key=key).exec(event.globalPos())
            return

        # Category context menu (reorder + table view + validate)
        if raw_cat is not None:
            panel = self.parent()
            while panel and not isinstance(panel, LibraryPanel):
                panel = panel.parent()
            build_category_menu(
                self, raw_cat, self._library,
                panel=panel if isinstance(panel, LibraryPanel) else None,
            ).exec(event.globalPos())


# =========================================================================
# Grouped grid view
# =========================================================================

class _GroupedGrid(QScrollArea):
    """Grid view with components grouped by category, each with a collapsible header."""

    def __init__(self, library: ComponentLibrary, panel: LibraryPanel, parent=None):
        super().__init__(parent)
        self._panel = panel
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(1)
        self.setWidget(self._container)
        self.populate(library)

    def populate(self, library: ComponentLibrary, category_order: list[str] | None = None) -> None:
        # Clear existing
        while self._layout.count():
            child = self._layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if category_order:
            cats = [c for c in category_order if c in library.categories]
            for c in sorted(library.categories.keys()):
                if c not in cats:
                    cats.append(c)
        else:
            cats = sorted(library.categories.keys())

        for category in cats:
            defs = library.categories.get(category, [])
            if not defs:
                continue
            section = _CategorySection(category, defs, self._panel)
            self._layout.addWidget(section)

        self._layout.addStretch()


class _CategorySection(QWidget):
    """A collapsible category section with header and flow grid of thumbnails."""

    def __init__(self, category: str, defs: list[ComponentDef], panel: LibraryPanel, parent=None):
        super().__init__(parent)
        self._category = category
        self._panel = panel
        self._collapsed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header row
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 1, 4, 1)
        header_layout.setSpacing(4)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setArrowType(Qt.ArrowType.DownArrow)
        self._toggle_btn.setFixedSize(16, 16)
        self._toggle_btn.setStyleSheet("QToolButton { border: none; }")
        self._toggle_btn.clicked.connect(self._toggle_collapse)
        header_layout.addWidget(self._toggle_btn)

        label = QLabel(f"<b>{category.replace('/', ' / ').replace('_', ' ')}</b>")
        # Pin the header text to black. The header sits on a light-gray
        # pill (`#f0f0f0`, set below) regardless of the chrome theme, so
        # it needs explicit black text to stay legible in dark mode —
        # otherwise the label would inherit the dark-mode palette text
        # (near-white) and vanish on the light pill.
        label.setStyleSheet("color: #000000; background: transparent;")
        header_layout.addWidget(label)
        header_layout.addStretch()

        # Table view button
        table_btn = QToolButton()
        table_btn.setText("\u2637")  # trigram for heaven (grid-like icon)
        table_btn.setToolTip("View as Table")
        table_btn.setFixedSize(18, 18)
        table_btn.setStyleSheet("QToolButton { border: 1px solid #ccc; font-size: 10px; }")
        table_btn.clicked.connect(lambda: panel.open_library_table(category))
        header_layout.addWidget(table_btn)

        # Reorder buttons
        up_btn = QToolButton()
        up_btn.setText("\u2191")
        up_btn.setFixedSize(18, 18)
        up_btn.setStyleSheet("QToolButton { border: 1px solid #ccc; font-size: 10px; }")
        up_btn.clicked.connect(lambda: panel.move_category(category, -1))
        header_layout.addWidget(up_btn)

        down_btn = QToolButton()
        down_btn.setText("\u2193")
        down_btn.setFixedSize(18, 18)
        down_btn.setStyleSheet("QToolButton { border: 1px solid #ccc; font-size: 10px; }")
        down_btn.clicked.connect(lambda: panel.move_category(category, 1))
        header_layout.addWidget(down_btn)

        header.setStyleSheet("background: #f0f0f0; border-radius: 3px;")
        layout.addWidget(header)

        # Thumbnail grid (flow layout)
        self._grid_widget = QWidget()
        self._grid_layout = _FlowLayout(self._grid_widget, margin=2, spacing=2)
        for comp_def in defs:
            thumb = _DragThumbnail(comp_def, panel)
            self._grid_layout.addWidget(thumb)
        layout.addWidget(self._grid_widget)

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self._grid_widget.setVisible(not self._collapsed)
        self._toggle_btn.setArrowType(
            Qt.ArrowType.RightArrow if self._collapsed else Qt.ArrowType.DownArrow
        )


class _DragThumbnail(QLabel):
    """A single draggable component thumbnail in the grid view."""

    def __init__(self, comp_def: ComponentDef, panel: LibraryPanel | None = None,
                 parent=None):
        super().__init__(parent)
        self._comp_def = comp_def
        self._panel = panel
        self._key = f"{comp_def.category}/{comp_def.name}"
        icon = _make_icon(comp_def)
        self.setPixmap(icon.pixmap(DENSE_THUMB_SIZE))
        self.setFixedSize(DENSE_THUMB_SIZE.width() + 2, DENSE_THUMB_SIZE.height() + 2)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(comp_def.name.replace("_", " "))
        self.setStyleSheet("QLabel:hover { background: #ddeeff; border-radius: 3px; }")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            mime = QMimeData()
            mime.setData(COMPONENT_MIME_TYPE, self._key.encode("utf-8"))
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(self.pixmap())
            drag.exec(Qt.DropAction.CopyAction)

    def build_menu(self):
        """Return this tile's context menu (see ``build_component_menu``)."""
        is_fav = self._panel.is_favorite(self._key) if self._panel else False
        return build_component_menu(self, self._comp_def, is_fav,
                                    panel=self._panel, key=self._key)

    def contextMenuEvent(self, event) -> None:
        """Same component menu as the tree view, so either view can reach it."""
        self.build_menu().exec(event.globalPos())


# =========================================================================
# Flow layout (since Qt doesn't provide one natively in Python)
# =========================================================================

class _FlowLayout(QLayout):
    """A flow layout that arranges widgets left-to-right, wrapping to next row."""

    def __init__(self, parent=None, margin: int = 0, spacing: int = -1):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items: list = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def spacing(self) -> int:
        return self._spacing if self._spacing >= 0 else 4

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        from PySide6.QtCore import QRect
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        from PySide6.QtCore import QSize as QS
        return self.minimumSize()

    def minimumSize(self):
        from PySide6.QtCore import QSize as QS
        size = QS()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QS(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only: bool) -> int:
        from PySide6.QtCore import QRect
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        right_edge = rect.right() - m.right()
        row_height = 0
        space = self.spacing()
        start_x = x

        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            if x + w > right_edge and row_height > 0:
                x = start_x
                y += row_height + space
                row_height = 0
            if not test_only:
                item.setGeometry(QRect(int(x), int(y), w, h))
            x += w + space
            row_height = max(row_height, h)

        return y + row_height - rect.y()


# =========================================================================
# Thumbnail rendering
# =========================================================================

def _make_icon(comp_def: ComponentDef) -> QIcon:
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
    # A corner dot for a component the user owns, so "mine" versus "shipped" is
    # visible without opening a menu — which matters because a fork shadows the
    # built-in and the panel therefore shows only one entry for the two.
    if comp_def.is_user_owned:
        from PySide6.QtGui import QColor, QPen
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setBrush(QColor(0, 120, 215))
        painter.drawEllipse(QRectF(tw - 9, 2, 6, 6))
    painter.end()
    return QIcon(pixmap)


# =========================================================================
# Platform file browser
# =========================================================================

def _show_in_file_browser(path: Path) -> None:
    """Open *path* in the platform's file browser (Finder / Explorer / xdg-open)."""
    import subprocess
    import sys
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        logger.debug("Failed to open file browser: %s", exc)
