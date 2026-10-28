"""AddComponentCommand — undoable command for placing a component on the scene."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsScene

from diagrammer.items.component_item import ComponentItem
from diagrammer.models.component_def import ComponentDef


class AddComponentCommand(QUndoCommand):
    """Add a component to the scene at a given position."""

    def __init__(
        self,
        scene: QGraphicsScene,
        component_def: ComponentDef,
        pos: QPointF,
        parent: QUndoCommand | None = None,
        *,
        snap_port_to_grid: bool = False,
    ) -> None:
        """*snap_port_to_grid*: a library drop lands a *port* on the grid,
        not the item's origin. Paste and duplicate place at exact
        positions and leave this off."""
        super().__init__(parent)
        self._scene = scene
        self._component_def = component_def
        self._pos = pos
        self._snap_port_to_grid = snap_port_to_grid
        self._item: ComponentItem | None = None
        self._z: float | None = None
        self.setText(f"Add {component_def.name}")

    @property
    def item(self) -> ComponentItem | None:
        return self._item

    def redo(self) -> None:
        if self._item is None:
            self._item = ComponentItem(self._component_def)
            # Assign to active layer
            if hasattr(self._scene, 'assign_active_layer'):
                self._scene.assign_active_layer(self._item)
            # Decided once, on first creation; redo after undo keeps it.
            views = self._scene.views()
            spacing = getattr(views[0], 'grid_spacing', None) if views else None
            if (self._snap_port_to_grid and spacing
                    and getattr(views[0], '_snap_enabled', True)):
                self._pos = self._item.grid_aligned_pos(self._pos, spacing)
        self._item.setPos(self._pos)
        self._scene.addItem(self._item)
        # Place on top of its (layer, type) band. Cache so undo/redo is stable.
        if self._z is None:
            self._z = self._scene.assign_item_z(self._item)
        else:
            self._item.setZValue(self._z)

    def undo(self) -> None:
        if self._item is not None:
            self._scene.removeItem(self._item)


class AddItemsCommand(QUndoCommand):
    """Add already-constructed items to the scene (undoable).

    ``AddComponentCommand`` builds its own item from a ``ComponentDef``;
    this one takes items the caller has already configured — pasted
    junctions, annotations, shapes, SVG images — and only owns their
    scene membership. Without it those paste paths called
    ``scene.addItem`` directly, so undoing a paste left them behind.

    Z values are assigned on the first ``redo`` and replayed afterwards,
    so repeated undo/redo keeps the stacking order stable.
    """

    def __init__(
        self,
        scene: QGraphicsScene,
        items: list,
        text: str = "Add items",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._items = list(items)
        self._z: list[float] | None = None
        self.setText(text)

    @property
    def items(self) -> list:
        return list(self._items)

    def redo(self) -> None:
        from diagrammer.items.connection_item import ConnectionItem
        first = self._z is None
        if first:
            self._z = []
        for i, item in enumerate(self._items):
            self._scene.addItem(item)
            if isinstance(item, ConnectionItem):
                self._scene.register_connection(item)
            if first:
                self._z.append(self._scene.assign_item_z(item))
            else:
                item.setZValue(self._z[i])

    def undo(self) -> None:
        from diagrammer.items.connection_item import ConnectionItem
        for item in self._items:
            if isinstance(item, ConnectionItem):
                self._scene.unregister_connection(item)
            self._scene.removeItem(item)


class MoveComponentCommand(QUndoCommand):
    """Record a component, junction, annotation, or shape move for undo/redo."""

    def __init__(
        self,
        item,
        old_pos: QPointF,
        new_pos: QPointF,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._old_pos = old_pos
        self._new_pos = new_pos
        if hasattr(item, 'component_def'):
            label = item.component_def.name
        else:
            label = type(item).__name__
        self.setText(f"Move {label}")

    def redo(self) -> None:
        self._item.setPos(self._new_pos)

    def undo(self) -> None:
        self._item.setPos(self._old_pos)
