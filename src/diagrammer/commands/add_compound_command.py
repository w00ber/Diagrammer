"""AddCompoundCommand — undoable placement of a compound from a manifest.

Re-instantiates every sub-item described by the manifest, wraps them all
in a fresh shared group id (so they move/select as a unit), and supports
undo by removing the items.  The user can ungroup the placement after
the fact to edit the pieces individually.
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsScene


class AddCompoundCommand(QUndoCommand):
    def __init__(
        self,
        scene: QGraphicsScene,
        manifest: dict,
        pos: QPointF,
        library,
        name: str = "compound",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._manifest = manifest
        self._pos = pos
        self._library = library
        self._items: list = []
        self._group_id = uuid.uuid4().hex[:12]
        self.setText(f"Add {name}")

    @property
    def items(self) -> list:
        return list(self._items)

    def redo(self) -> None:
        from diagrammer.io.compound_manifest import instantiate_compound

        if not self._items:
            self._items = instantiate_compound(
                self._scene, self._manifest, self._pos, self._library
            )
            # Wrap every newly-created item in a shared outer group so the
            # whole compound moves/selects together. Sub-items that already
            # had nested groups inside the manifest keep that nesting; the
            # new outer group is appended on top.
            for it in self._items:
                stack = list(getattr(it, "_group_ids", []) or [])
                stack.append(self._group_id)
                it._group_ids = stack
                it._group_id = stack[-1]
        else:
            for it in self._items:
                self._scene.addItem(it)

    def undo(self) -> None:
        for it in self._items:
            self._scene.removeItem(it)
