"""Several open diagrams, one per tab, beside the library table tabs.

The window used to own exactly one scene/view pair as ``self._scene`` and
``self._view``, and about two hundred call sites across the window and its
mixins read those names. Rather than thread a document argument through every
one of them, ``_scene``, ``_view`` and ``_current_file`` are properties here
that resolve to the **active diagram document**. Code that was correct for one
diagram stays correct for the one in front of the user.

Two rules keep that safe:

* **A library table tab is not a document.** While one is showing, ``_scene``
  and ``_view`` keep resolving to the diagram that was active last, so an Edit
  or Draw action can never land in a table. ``_active_scene()`` and
  ``_active_view()`` still return the *table's* pair, which is what zoom and
  export want, exactly as before.
* **There is always at least one diagram.** Closing the last one opens a fresh
  Untitled in its place, so ``_scene`` is never ``None``.

Window-wide toggles (grid, snapping, grid spacing, routing default, canvas
background) are applied to every open document and to each new one in
``_configure_document``. Per-document state — undo history, layers, selection,
the interaction mode — follows the active tab: the undo actions come from a
``QUndoGroup``, and the panels are re-pointed in ``_activate_document``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QUndoGroup
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget
from shiboken6 import isValid

from diagrammer.canvas.scene import DiagramScene
from diagrammer.canvas.view import DiagramView

logger = logging.getLogger(__name__)

#: Appended to a tab's label while its diagram has unsaved changes.
DIRTY_MARK = " •"


@dataclass(eq=False)
class DiagramDocument:
    """One open diagram: its scene, its view, and the chrome around the view."""

    scene: DiagramScene
    view: DiagramView
    container: QWidget
    mode_label: QLabel
    hint_label: QLabel
    shortcut_overlay: object
    file_path: str | None = None
    untitled_number: int = 1

    @property
    def display_name(self) -> str:
        if self.file_path:
            return Path(self.file_path).name
        if self.untitled_number <= 1:
            return "Untitled"
        return f"Untitled {self.untitled_number}"

    @property
    def is_dirty(self) -> bool:
        return not self.scene.undo_stack.isClean()

    @property
    def needs_save(self) -> bool:
        """Unsaved work worth a prompt. An empty canvas never is, even with
        history behind it (draw a line, delete it)."""
        return self.is_dirty and bool(self.scene.items())

    @property
    def is_pristine(self) -> bool:
        """Untitled, empty and untouched: safe to load a file into in place,
        rather than leaving a blank tab behind the one just opened."""
        return (self.file_path is None and not self.is_dirty
                and not self.scene.items())


class DocumentTabsMixin:
    """Tab and document management for ``MainWindow``.

    Expects the host to provide ``_library``, ``_update_pos_label``,
    ``_update_mode_label``, ``_on_selection_changed`` and the panels; each use
    is guarded, because the first document is created before the panels and
    menus exist.
    """

    # ------------------------------------------------------ active document

    @property
    def _scene(self) -> DiagramScene:
        return self._active_doc.scene

    @property
    def _view(self) -> DiagramView:
        return self._active_doc.view

    @property
    def _current_file(self) -> str | None:
        return self._active_doc.file_path

    @_current_file.setter
    def _current_file(self, path: str | None) -> None:
        self._active_doc.file_path = path
        self._refresh_document_tab(self._active_doc)

    @property
    def _mode_label(self) -> QLabel:
        return self._active_doc.mode_label

    @property
    def _overlay_hint_label(self) -> QLabel:
        return self._active_doc.hint_label

    @property
    def _shortcut_overlay(self):
        return self._active_doc.shortcut_overlay

    def _documents(self) -> list[DiagramDocument]:
        """Open diagrams in tab order (library tables excluded)."""
        tabs = self._tab_widget
        docs = []
        for i in range(tabs.count()):
            doc = getattr(tabs.widget(i), "_diagram_doc", None)
            if doc is not None:
                docs.append(doc)
        return docs

    # --------------------------------------------------------------- set-up

    def _init_document_tabs(self) -> None:
        """Create the tab widget, the undo group and the first document."""
        self._active_doc: DiagramDocument | None = None
        # Most recently active first, so closing a tab falls back to the
        # diagram the user was in before it rather than to tab order.
        self._doc_history: list[DiagramDocument] = []
        self._untitled_counter = 0
        self._undo_group = QUndoGroup(self)

        self._tab_widget = QTabWidget(self)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.setMovable(True)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tab_widget.currentChanged.connect(self._on_current_tab_changed)
        self._tab_widget.tabBar().setVisible(False)
        self._new_document()

    def _build_document(self) -> DiagramDocument:
        """A scene and view inside their canvas chrome, not yet in a tab."""
        from diagrammer.app import hint_text_color
        from diagrammer.panels.shortcut_overlay import ShortcutOverlay

        scene = DiagramScene(library=self._library, parent=self)
        view = DiagramView(scene, self)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Mode readout, right-aligned above the canvas.
        mode_label = QLabel(f"Mode: {scene.mode.name.capitalize()}")
        mode_label.setStyleSheet("font-weight: bold;")
        mode_bar = QHBoxLayout()
        mode_bar.setContentsMargins(0, 2, 6, 2)
        mode_bar.addStretch()
        mode_bar.addWidget(mode_label)
        layout.addLayout(mode_bar)
        layout.addWidget(view)
        # Persistent discoverability hint under the lower-right of the canvas;
        # its text tracks the live toggle key (_refresh_overlay_hint_label).
        hint_label = QLabel("")
        hint_label.setStyleSheet(
            f"color: {hint_text_color()}; font-size: 11px;")
        hint_bar = QHBoxLayout()
        hint_bar.setContentsMargins(0, 2, 6, 2)
        hint_bar.addStretch()
        hint_bar.addWidget(hint_label)
        layout.addLayout(hint_bar)

        self._untitled_counter += 1
        doc = DiagramDocument(
            scene=scene, view=view, container=container,
            mode_label=mode_label, hint_label=hint_label,
            # Parented to the viewport, so it never reaches the scene or an
            # export.
            shortcut_overlay=ShortcutOverlay(view.viewport()),
            untitled_number=self._untitled_counter,
        )
        container._diagram_doc = doc
        return doc

    def _new_document(self, *, activate: bool = True) -> DiagramDocument:
        """Open an empty Untitled diagram in a new tab."""
        doc = self._build_document()
        self._configure_document(doc)
        self._undo_group.addStack(doc.scene.undo_stack)

        scene = doc.scene
        scene.cursor_scene_pos_changed.connect(self._update_pos_label)
        scene.mode_changed.connect(
            lambda mode, d=doc: self._from_document(d, self._update_mode_label, mode))
        scene.selectionChanged.connect(
            lambda d=doc: self._from_document(d, self._on_selection_changed))
        scene.undo_stack.cleanChanged.connect(
            lambda _clean, d=doc: self._from_document(
                d, self._refresh_document_tab, d, active_only=False))

        self._tab_widget.addTab(doc.container, doc.display_name)
        self._update_tab_bar_visibility()
        if activate or self._active_doc is None:
            self._tab_widget.setCurrentWidget(doc.container)
            # The first tab is current as soon as it is added, so the
            # setCurrentWidget above may not fire currentChanged; be explicit.
            self._activate_document(doc)
        self._refresh_document_tab(doc)
        return doc

    def _configure_document(self, doc: DiagramDocument) -> None:
        """Give a new document the window-wide canvas state."""
        from PySide6.QtGui import QBrush

        from diagrammer.panels.settings_dialog import app_settings

        view = doc.view
        grid_act = getattr(self, "_toggle_grid_act", None)
        if grid_act is not None:
            view.grid_visible = grid_act.isChecked()
        snap_act = getattr(self, "_snap_grid_act", None)
        view.snap_enabled = (snap_act.isChecked() if snap_act is not None
                             else app_settings.snap_to_grid)
        spin = getattr(self, "_grid_spin", None)
        if spin is not None:
            view.grid_spacing = spin.value()
        self._sync_angle_snap(view)
        view.setBackgroundBrush(QBrush(app_settings.current_background_color()))
        doc.scene.default_routing_mode = app_settings.default_routing_mode

    @staticmethod
    def _sync_angle_snap(view: DiagramView) -> None:
        """Angle snap stands in for grid snap: on only while grid snap is off."""
        from diagrammer.panels.settings_dialog import app_settings

        if not view.snap_enabled and app_settings.snap_to_angle:
            view._angle_snap = True
            view._angle_snap_increment = app_settings.angle_snap_increment
        else:
            view._angle_snap = False

    # ----------------------------------------------------------- activation

    def _is_active(self, doc: DiagramDocument) -> bool:
        return doc is self._active_doc

    def _from_document(self, doc: DiagramDocument, handler, *args,
                       active_only: bool = True) -> None:
        """Forward a document's signal to the window's shared chrome.

        Only the active document drives the panels and labels; background
        scenes still emit. The connections are lambdas, to carry the document,
        so Qt cannot tie them to the window's lifetime as it would a bound
        method — and during teardown a scene emits from its own destructor,
        after it or the tab widget is already gone. Hence the liveness check.
        """
        if not (isValid(self) and isValid(self._tab_widget)
                and isValid(doc.scene)):
            return
        if active_only and doc is not self._active_doc:
            return
        handler(*args)

    def _on_current_tab_changed(self, index: int) -> None:
        doc = getattr(self._tab_widget.widget(index), "_diagram_doc", None)
        if doc is not None:
            self._activate_document(doc)

    def _activate_document(self, doc: DiagramDocument) -> None:
        """Point the undo actions, panels and status chrome at ``doc``."""
        previous = self._active_doc
        if previous is not None and previous is not doc:
            # Zoom-window is a transient mode of one view; leaving it armed on
            # a hidden tab would ambush the user on return.
            previous.view.zoom_window_mode = False
        self._active_doc = doc
        if doc in self._doc_history:
            self._doc_history.remove(doc)
        self._doc_history.insert(0, doc)
        self._undo_group.setActiveStack(doc.scene.undo_stack)

        for name in ("_zoom_window_act", "_trace_mode_act"):
            act = getattr(self, name, None)
            if act is None:
                continue
            act.blockSignals(True)
            act.setChecked(name == "_trace_mode_act" and doc.view.trace_routing)
            act.blockSignals(False)
        doc.mode_label.setText(f"Mode: {doc.scene.mode.name.capitalize()}")

        self._sync_panels_to_document()
        if hasattr(self, "_shortcut_overlay_on"):
            self._update_shortcut_overlay()
        self._update_title()
        doc.view.setFocus()

    def _sync_panels_to_document(self) -> None:
        """Re-point the dock panels at the active scene.

        Also needed after loading a file into the active document, because the
        serializer replaces the scene's ``LayerManager``.
        """
        scene = self._active_doc.scene
        layers = getattr(self, "_layers_panel", None)
        if layers is not None:
            layers._manager = scene.layer_manager
            layers.set_undo_stack(scene.undo_stack)
            layers.refresh()
        props = getattr(self, "_props_panel", None)
        if props is not None:
            props.update_for_selection(scene)
        if hasattr(self, "_layer_warning_label"):
            self._refresh_layer_warning()

    def _refresh_document_tab(self, doc: DiagramDocument) -> None:
        index = self._tab_widget.indexOf(doc.container)
        if index >= 0:
            self._tab_widget.setTabText(
                index, doc.display_name + (DIRTY_MARK if doc.is_dirty else ""))
            self._tab_widget.setTabToolTip(index, doc.file_path or "")
        if self._is_active(doc):
            self._update_title()

    def _update_title(self) -> None:
        doc = self._active_doc
        # "[*]" is where Qt draws the modified marker (on macOS, the dot in
        # the close button instead).
        self.setWindowTitle(f"{doc.display_name}[*] — Diagrammer")
        self.setWindowModified(doc.is_dirty)

    # ------------------------------------------------------------- tab ops

    def _active_scene(self):
        """The scene of the current tab, library table or diagram."""
        widget = self._tab_widget.currentWidget()
        doc = getattr(widget, "_diagram_doc", None)
        if doc is not None:
            return doc.scene
        if hasattr(widget, "scene") and callable(widget.scene):
            return widget.scene()
        return self._scene

    def _active_view(self):
        """The view of the current tab, library table or diagram."""
        from PySide6.QtWidgets import QGraphicsView

        widget = self._tab_widget.currentWidget()
        doc = getattr(widget, "_diagram_doc", None)
        if doc is not None:
            return doc.view
        if isinstance(widget, QGraphicsView):
            return widget
        return self._view

    def _update_tab_bar_visibility(self) -> None:
        """Show the tab bar only once there is more than one tab."""
        self._tab_widget.tabBar().setVisible(self._tab_widget.count() > 1)

    def _cycle_tabs(self, step: int) -> None:
        """Show the next (``step=1``) or previous (``-1``) tab, wrapping."""
        tabs = self._tab_widget
        n = tabs.count()
        if n > 1:
            tabs.setCurrentIndex((tabs.currentIndex() + step) % n)

    def _show_next_tab(self) -> None:
        self._cycle_tabs(1)

    def _show_previous_tab(self) -> None:
        self._cycle_tabs(-1)

    def _on_tab_close_requested(self, index: int) -> None:
        widget = self._tab_widget.widget(index)
        doc = getattr(widget, "_diagram_doc", None)
        if doc is not None:
            self._close_document(doc)
            return
        # A library table tab.
        self._tab_widget.removeTab(index)
        self._update_tab_bar_visibility()
        if hasattr(widget, "scene") and callable(widget.scene):
            scene = widget.scene()
            if scene:
                scene.clear()
        widget.deleteLater()

    def close_active_tab(self) -> None:
        """Close the current tab, prompting first if it is an unsaved diagram."""
        index = self._tab_widget.currentIndex()
        if index >= 0:
            self._on_tab_close_requested(index)

    def _close_document(self, doc: DiagramDocument) -> bool:
        """Close one diagram. Returns False if the user cancelled."""
        if doc.needs_save:
            self._tab_widget.setCurrentWidget(doc.container)
            if not self._check_unsaved_changes():
                return False
        if doc in self._doc_history:
            self._doc_history.remove(doc)
        # Removing the tab fires currentChanged, which activates the next tab
        # if that is a diagram.
        self._tab_widget.removeTab(self._tab_widget.indexOf(doc.container))
        self._undo_group.removeStack(doc.scene.undo_stack)
        if self._is_active(doc):
            # The tab that took over is a library table, or there is none.
            # Keep a table showing, but point Edit/Draw at the diagram the
            # user was in before; with no diagram left, open a fresh one.
            if self._doc_history:
                self._activate_document(self._doc_history[0])
            else:
                self._activate_document(self._new_document(activate=False))
        self._update_tab_bar_visibility()
        # Nothing should hear this scene again; clear() alone would emit
        # selectionChanged and cleanChanged into the chrome of another tab.
        doc.scene.blockSignals(True)
        doc.scene.undo_stack.blockSignals(True)
        doc.scene.clear()
        doc.container.deleteLater()
        doc.scene.deleteLater()
        return True

    def _open_library_table(self, category: str) -> None:
        """Open (or focus) a library table tab for the given category."""
        from PySide6.QtCore import QTimer

        from diagrammer.panels.settings_dialog import app_settings

        for i in range(self._tab_widget.count()):
            widget = self._tab_widget.widget(i)
            if getattr(widget, "_library_category", None) == category:
                self._tab_widget.setCurrentIndex(i)
                return

        # Component defs for this category and its subcategories, skipping
        # any categories the user has hidden in Settings.
        hidden = app_settings.hidden_libraries
        defs_by_subcat: dict[str, list] = {}
        for cat, cat_defs in self._library.categories.items():
            if cat in hidden:
                continue
            if (category == "__all__" or cat == category
                    or cat.startswith(category + "/")):
                defs_by_subcat[cat] = cat_defs
        if not defs_by_subcat:
            return

        from diagrammer.panels.library_table_view import build_library_table
        title = "All Libraries" if category == "__all__" else category
        _scene, view = build_library_table(title, defs_by_subcat)
        view._library_category = category  # tag for deduplication
        # Lets the table's right-click menu toggle favorites and open a
        # component's SVG externally, same as the library panel's menus.
        view.set_library_panel(self._library_panel)

        label = ("All Libraries" if category == "__all__"
                 else category.replace("/", " / ").replace("_", " "))
        index = self._tab_widget.addTab(view, f"Table: {label}")
        self._tab_widget.setCurrentIndex(index)
        self._update_tab_bar_visibility()
        # Deferred so the view has its final geometry.
        QTimer.singleShot(0, view.fit_all)

    def _validation_report_tab(self):
        """The validation report tab, if one is open (there is at most one)."""
        for i in range(self._tab_widget.count()):
            widget = self._tab_widget.widget(i)
            if getattr(widget, "_is_validation_report", False):
                return widget
        return None

    def _show_validation_report(self, roots) -> None:
        """Open the report tab on ``roots``, or re-scope the open one.

        One tab, not one per invocation: a second report would go stale
        against the first the moment either one's bulk fix ran.
        """
        from diagrammer.panels.settings_dialog import app_settings
        from diagrammer.panels.validation_report import ValidationReportView

        report = self._validation_report_tab()
        if report is None:
            report = ValidationReportView(
                roots=roots,
                profile_overrides=getattr(app_settings, "library_profiles",
                                          None),
                on_open=self.open_component_inspector,
                shadowed_by=self._shadowing_copy)
            report._is_validation_report = True
            self._tab_widget.addTab(report, "Validation")
        else:
            report.set_roots(roots)
        self._tab_widget.setCurrentWidget(report)
        self._update_tab_bar_visibility()

    # --------------------------------------------------------- file targets

    def _find_document(self, path: str) -> DiagramDocument | None:
        """The open document backed by ``path``, if any."""
        try:
            target = Path(path).resolve()
        except OSError:
            return None
        for doc in self._documents():
            if doc.file_path:
                try:
                    if Path(doc.file_path).resolve() == target:
                        return doc
                except OSError:
                    continue
        return None

    def _document_for_load(self) -> tuple[DiagramDocument, bool]:
        """Where a file being opened should go: the active document when it
        is a blank Untitled, otherwise a new tab. Returns ``(doc, created)``
        so a failed load can close the tab it made."""
        doc = self._active_doc
        if doc is not None and doc.is_pristine:
            self._tab_widget.setCurrentWidget(doc.container)
            return doc, False
        return self._new_document(), True

    def _discard_failed_load(self, doc: DiagramDocument, created: bool) -> None:
        """Undo ``_document_for_load`` after the load raised."""
        if created:
            doc.scene.undo_stack.clear()
            doc.scene.clear()
            self._close_document(doc)
        else:
            doc.scene.clear()
            doc.scene.undo_stack.clear()
            self._sync_panels_to_document()

    def _confirm_close_all(self) -> bool:
        """Prompt for every diagram with unsaved work. False on Cancel."""
        for doc in self._documents():
            if doc.needs_save:
                self._tab_widget.setCurrentWidget(doc.container)
                if not self._check_unsaved_changes():
                    return False
        return True
