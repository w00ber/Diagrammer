"""LayersPanel — manage diagram layers with visibility, lock, and ordering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from diagrammer.canvas.scene import DiagramScene


@dataclass
class Layer:
    """A diagram layer."""
    name: str
    visible: bool = True
    locked: bool = False
    color: QColor = field(default_factory=lambda: QColor(0, 0, 0))

    def to_dict(self) -> dict:
        """Serialize the layer to a JSON-compatible dict."""
        return {
            "name": self.name,
            "visible": self.visible,
            "locked": self.locked,
            "color": self.color.name(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Layer:
        """Deserialize a Layer from a dict."""
        return cls(
            name=d["name"],
            visible=d.get("visible", True),
            locked=d.get("locked", False),
            color=QColor(d.get("color", "#000000")),
        )


class LayerManager:
    """Manages the list of layers and the active layer."""

    def __init__(self) -> None:
        self._layers: list[Layer] = [Layer(name="Default")]
        self._active_index: int = 0

    @property
    def layers(self) -> list[Layer]:
        return self._layers

    @property
    def active_layer(self) -> Layer:
        return self._layers[self._active_index]

    @property
    def active_index(self) -> int:
        return self._active_index

    @active_index.setter
    def active_index(self, idx: int) -> None:
        if 0 <= idx < len(self._layers):
            self._active_index = idx

    def add_layer(self, name: str = "") -> Layer:
        """Add a new layer with the given name and return it."""
        if not name:
            name = f"Layer {len(self._layers) + 1}"
        layer = Layer(name=name)
        self._layers.append(layer)
        return layer

    def remove_layer(self, index: int) -> None:
        """Remove the layer at *index*, unless it is the last remaining layer."""
        if len(self._layers) <= 1:
            return  # can't remove the last layer
        if 0 <= index < len(self._layers):
            self._layers.pop(index)
            if self._active_index >= len(self._layers):
                self._active_index = len(self._layers) - 1

    def move_layer(self, index: int, direction: int) -> None:
        """Swap the layer at *index* with its neighbor in *direction* (-1 or +1)."""
        new_idx = index + direction
        if 0 <= new_idx < len(self._layers):
            self._layers[index], self._layers[new_idx] = self._layers[new_idx], self._layers[index]
            if self._active_index == index:
                self._active_index = new_idx
            elif self._active_index == new_idx:
                self._active_index = index

    def layer_index(self, layer: Layer) -> int:
        """Return the index of *layer*, or -1 if not found."""
        return self._layers.index(layer) if layer in self._layers else -1

    def to_list(self) -> list[dict]:
        """Serialize all layers to a list of dicts."""
        return [layer.to_dict() for layer in self._layers]

    def from_list(self, data: list[dict]) -> None:
        """Replace all layers from a list of serialized dicts."""
        self._layers = [Layer.from_dict(d) for d in data]
        if not self._layers:
            self._layers = [Layer(name="Default")]
        self._active_index = min(self._active_index, len(self._layers) - 1)


# Eye / lock icon characters
_EYE_OPEN = "\U0001F441"    # 👁
_EYE_CLOSED = "\u2014"      # —
_LOCK_OPEN = "\U0001F513"   # 🔓
_LOCK_CLOSED = "\U0001F512" # 🔒


class LayersPanel(QDockWidget):
    """Dock widget for managing diagram layers."""

    layers_changed = Signal()           # emitted when visibility/lock/order changes
    active_layer_switched = Signal(int)  # emitted with new active index when user clicks a layer
    layers_reordered = Signal(int, int)  # (old_index, new_index) emitted before layers_changed on a reorder
    layer_removed = Signal(int)          # emitted with the removed index before layers_changed

    def __init__(self, layer_manager: LayerManager, parent=None):
        super().__init__("Layers", parent)
        self._manager = layer_manager

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Layer list
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(2)

        add_btn = QToolButton()
        add_btn.setText("+")
        add_btn.setFixedSize(24, 24)
        add_btn.clicked.connect(self._add_layer)
        btn_row.addWidget(add_btn)

        remove_btn = QToolButton()
        remove_btn.setText("\u2212")  # minus
        remove_btn.setFixedSize(24, 24)
        remove_btn.clicked.connect(self._remove_layer)
        btn_row.addWidget(remove_btn)

        up_btn = QToolButton()
        up_btn.setText("\u2191")
        up_btn.setFixedSize(24, 24)
        up_btn.clicked.connect(lambda: self._move_layer(-1))
        btn_row.addWidget(up_btn)

        down_btn = QToolButton()
        down_btn.setText("\u2193")
        down_btn.setFixedSize(24, 24)
        down_btn.clicked.connect(lambda: self._move_layer(1))
        btn_row.addWidget(down_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.setWidget(container)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list from the layer manager."""
        self._list.blockSignals(True)
        self._list.clear()
        for i, layer in enumerate(self._manager.layers):
            vis = _EYE_OPEN if layer.visible else _EYE_CLOSED
            lock = _LOCK_CLOSED if layer.locked else ""
            active = "\u25B6 " if i == self._manager.active_index else "   "
            text = f"{active}{vis}  {lock}{layer.name}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            if i == self._manager.active_index:
                item.setBackground(QColor(220, 235, 255))
            if not layer.visible:
                item.setForeground(QColor(160, 160, 160))
            self._list.addItem(item)
        self._list.setCurrentRow(self._manager.active_index)
        self._list.blockSignals(False)

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._manager.layers):
            self._manager.active_index = row
            self.refresh()
            self.layers_changed.emit()
            self.active_layer_switched.emit(row)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        """Double-click toggles visibility."""
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < len(self._manager.layers):
            layer = self._manager.layers[idx]
            layer.visible = not layer.visible
            self.refresh()
            self.layers_changed.emit()

    def _add_layer(self) -> None:
        self._manager.add_layer()
        self._manager.active_index = len(self._manager.layers) - 1
        self.refresh()
        self.layers_changed.emit()

    def _remove_layer(self) -> None:
        idx = self._manager.active_index
        if len(self._manager.layers) <= 1:
            return
        self._manager.remove_layer(idx)
        # Notify listeners so they can shift any item._layer_index above `idx` down by 1.
        self.layer_removed.emit(idx)
        self.refresh()
        self.layers_changed.emit()

    def _move_layer(self, direction: int) -> None:
        idx = self._manager.active_index
        new_idx = idx + direction
        if not (0 <= new_idx < len(self._manager.layers)):
            return
        self._manager.move_layer(idx, direction)
        # Tell listeners which two slots were swapped so they can remap items
        # before we re-render / re-stack.
        self.layers_reordered.emit(idx, new_idx)
        self.refresh()
        self.layers_changed.emit()

    def contextMenuEvent(self, event) -> None:
        """Show a context menu with visibility, lock, and rename actions."""
        item = self._list.itemAt(self._list.mapFrom(self, event.pos()))
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        layer = self._manager.layers[idx]

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)

        vis_act = menu.addAction("Hide" if layer.visible else "Show")
        lock_act = menu.addAction("Unlock" if layer.locked else "Lock")
        menu.addSeparator()
        rename_act = menu.addAction("Rename\u2026")

        action = menu.exec(event.globalPos())
        if action is vis_act:
            layer.visible = not layer.visible
            self.refresh()
            self.layers_changed.emit()
        elif action is lock_act:
            layer.locked = not layer.locked
            self.refresh()
            self.layers_changed.emit()
        elif action is rename_act:
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "Rename Layer", "Name:", text=layer.name)
            if ok and name.strip():
                layer.name = name.strip()
                self.refresh()
                self.layers_changed.emit()
