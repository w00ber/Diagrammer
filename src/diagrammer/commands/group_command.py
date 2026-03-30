"""Group / Ungroup commands — undoable group membership changes.

Groups are nested via a stack: each item has ``_group_ids`` (a list of
group-id strings).  The last entry is the outermost (active) group.
Grouping pushes a new id; ungrouping pops the top level.
"""

from __future__ import annotations

import uuid

from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsItem


def get_top_group(item) -> str | None:
    """Return the outermost (active) group id, or None."""
    ids = getattr(item, '_group_ids', None)
    if ids:
        return ids[-1]
    # Legacy single _group_id
    return getattr(item, '_group_id', None)


def set_group_ids(item, ids: list[str]) -> None:
    """Set the full group stack on an item."""
    item._group_ids = list(ids)
    # Keep legacy _group_id in sync for serialization / paint checks
    item._group_id = ids[-1] if ids else None


class GroupCommand(QUndoCommand):
    """Push a new shared group_id onto all items in the selection."""

    def __init__(
        self,
        items: list[QGraphicsItem],
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._new_group_id = uuid.uuid4().hex[:12]
        # Snapshot each item's current stack for undo
        self._old_stacks = [list(getattr(i, '_group_ids', []) or
                                  ([i._group_id] if getattr(i, '_group_id', None) else []))
                            for i in items]
        self.setText(f"Group {len(items)} items")

    def redo(self) -> None:
        for item, old_stack in zip(self._items, self._old_stacks):
            set_group_ids(item, old_stack + [self._new_group_id])

    def undo(self) -> None:
        for item, old_stack in zip(self._items, self._old_stacks):
            set_group_ids(item, old_stack)


class UngroupCommand(QUndoCommand):
    """Pop the top group level from items that share a given group_id."""

    def __init__(
        self,
        items: list[QGraphicsItem],
        top_group_id: str,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._top_group_id = top_group_id
        self._old_stacks = [list(getattr(i, '_group_ids', []) or
                                  ([i._group_id] if getattr(i, '_group_id', None) else []))
                            for i in items]
        self.setText(f"Ungroup {len(items)} items")

    def redo(self) -> None:
        for item, old_stack in zip(self._items, self._old_stacks):
            # Pop the top group id if it matches
            new_stack = list(old_stack)
            if new_stack and new_stack[-1] == self._top_group_id:
                new_stack.pop()
            set_group_ids(item, new_stack)

    def undo(self) -> None:
        for item, old_stack in zip(self._items, self._old_stacks):
            set_group_ids(item, old_stack)
