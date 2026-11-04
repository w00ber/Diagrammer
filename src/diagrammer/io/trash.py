"""Move a file to the Trash rather than unlinking it.

Why this exists rather than ``Path.unlink``: deleting a component is one of the
few genuinely destructive things the app does to a user's own data, and it
deliberately stays *out* of the QUndoCommand stack. A document's undo stack is
the wrong scope for a global library change — Cmd-Z after reopening a diagram
would either do nothing or revert a library edit made an hour ago in a different
drawing — and ``undo()`` has to be infallible, which restoring a file is not.

So reversibility comes from the platform instead. The OS owns the undo, which is
both more robust and where the user already looks for a file they deleted by
mistake.

No third-party dependency: on macOS the Finder's own behaviour is a rename into
``~/.Trash`` with a numeric suffix on collision, which is what this does.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def trash_dir() -> Path | None:
    """The user's trash folder, or None on a platform we do not handle."""
    if sys.platform == "darwin":
        return Path.home() / ".Trash"
    if sys.platform.startswith("linux"):
        # The XDG spec's trash also wants a .trashinfo sidecar; without one the
        # file is recoverable by hand but the desktop's Restore will not know
        # where it came from. Good enough, and better than unlinking.
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(base) / "Trash" / "files"
    return None


def unique_name(directory: Path, name: str) -> str:
    """``cap.svg`` -> ``cap 2.svg`` when taken, matching the Finder's style."""
    target = directory / name
    if not target.exists():
        return name
    stem, dot, ext = name.partition(".")
    n = 2
    while (directory / f"{stem} {n}{dot}{ext}").exists():
        n += 1
    return f"{stem} {n}{dot}{ext}"


def move_to_trash(path: Path) -> Path | None:
    """Move ``path`` to the Trash and return where it landed.

    Falls back to ``os.replace`` within the trash directory, then to a plain
    rename, and finally raises rather than silently unlinking: a caller that
    asked for a recoverable delete should be told when it could not have one.
    """
    path = Path(path)
    if not path.exists():
        return None
    directory = trash_dir()
    if directory is None:
        raise OSError(f"no Trash folder known for platform {sys.platform!r}")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / unique_name(directory, path.name)
        try:
            os.replace(path, target)
        except OSError:
            # Different filesystem — copy then remove, which is what a cross
            # volume move is anyway.
            shutil.move(str(path), str(target))
        return target
    except OSError:
        logger.exception("could not move %s to the Trash", path)
        raise
