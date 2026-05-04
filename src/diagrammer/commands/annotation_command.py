"""Annotation commands — undoable edits to AnnotationItem state."""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand


class EditAnnotationTextCommand(QUndoCommand):
    """Record an annotation text edit for undo/redo.

    Pushed by ``AnnotationItem.finish_editing()`` when the source text
    changes between the start and end of an inline edit. Replays the
    same render path on redo and restores the previous text on undo,
    including the LaTeX math re-render and intrinsic-anchor recompute
    so a rotated annotation visually settles back where it was.
    """

    def __init__(
        self,
        annot,
        old_text: str,
        new_text: str,
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(parent)
        self._annot = annot
        self._old = old_text
        self._new = new_text
        self.setText("Edit annotation text")

    def _apply(self, text: str) -> None:
        # Use the public ``text_content`` setter — it sets _source_text,
        # calls setPlainText, and triggers _try_render_math which in
        # turn recomputes the intrinsic anchor (Phase C).
        self._annot.text_content = text

    def redo(self) -> None:
        self._apply(self._new)

    def undo(self) -> None:
        self._apply(self._old)
