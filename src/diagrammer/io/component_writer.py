"""Writing a component SVG back to disk.

**Never route a component save through ``io/compound_export.py``.** That writer
emits only ``<g id="artwork">`` and ``<g id="ports">`` — it exists to flatten a
canvas selection into a new part — so using it to save an *edited* component
would silently destroy the leads, labels, stretch axes, tile group, snap point,
metadata and the whole ``<style>`` block.

What this module writes instead is candidate *text*: the original file with
surgical substitutions applied, so every byte nobody touched survives exactly as
it was. That keeps a component file reviewable as a diff, which matters because
these files are hand-tuned artwork — Illustrator's CSS classes, the attribute
order, the indentation — and a parse-and-reserialize round trip reformats all of
it even where nothing changed.

The other job here is the fork. Overwriting a built-in does not fail and does
not ask permission: it writes to the user's overlay and records where the copy
came from. Refusing would be both unhelpful and unimplementable, since the
shipped tree may sit inside a signed, read-only ``.app`` bundle.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from diagrammer.models.overrides import OverrideIndex, rel_posix

logger = logging.getLogger(__name__)

#: A candidate this much smaller than the original is a bug in a fix, not an
#: edit. Only waived when a geometry-moving fix ran, since rebasing a viewBox
#: can legitimately shorten coordinates.
SHRINK_FLOOR = 0.5


class WriteRefused(Exception):
    """The candidate text failed a sanity check, so nothing was written."""


@dataclass(frozen=True)
class SaveResult:
    path: Path
    #: True when the target was a built-in and the save became a fork
    forked: bool = False
    backup: Path | None = None
    #: overlay-relative key recorded in the index, when one was
    rel: str | None = None


def _sanity_check(text: str, original_text: str, *,
                  allow_shrink: bool = False) -> None:
    """Refuse text that cannot be a component, before it reaches the disk.

    Two tripwires, both for the case where a fix has a bug: a regex that ate
    the document would otherwise overwrite good artwork with nothing, and the
    backup is the only thing standing behind it.
    """
    if not re.search(r"<svg\b", text):
        raise WriteRefused("the candidate has no <svg> root element")
    if not allow_shrink and original_text and (
            len(text) < len(original_text) * SHRINK_FLOOR):
        raise WriteRefused(
            f"the candidate is {len(text)} bytes against the original's "
            f"{len(original_text)} — more than half the file would be lost, "
            "which is a bug in a fix rather than an edit")


def write_text_atomic(text: str, target: Path) -> None:
    """Write via a temp file and ``os.replace``, so a crash cannot truncate.

    A component is read by the library scan on every refresh; a half-written
    file would make it vanish from the panel with a parse error.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def overlay_path_for(source: Path, overlay_root: Path,
                     builtin_root: Path) -> tuple[Path, str] | None:
    """Where a built-in's fork belongs in the overlay, and its index key.

    The overlay mirrors the built-in tree by relative path, which is what makes
    the forked component collide keys with the shipped one and therefore shadow
    it on scan.
    """
    rel = rel_posix(source, builtin_root)
    if rel is None:
        return None
    return overlay_root / rel, rel


def save_component_text(
    text: str,
    target: Path,
    *,
    original_text: str,
    index: OverrideIndex,
    builtin_root: Path | None = None,
    app_version: str = "",
    allow_shrink: bool = False,
    backup: bool = True,
) -> SaveResult:
    """Save ``text`` over ``target``, forking into the overlay if need be.

    When ``target`` is inside ``builtin_root`` this transparently becomes a
    fork: the text goes to the mirrored path in the overlay and the index
    records the built-in it came from, including that built-in's SHA-256 so a
    later update can report that the shipped version has moved on. The shipped
    file itself is never touched.
    """
    _sanity_check(text, original_text, allow_shrink=allow_shrink)

    forked = False
    rel: str | None = None
    if builtin_root is not None:
        mapped = overlay_path_for(target, index.overlay_root, builtin_root)
        if mapped is not None:
            overlay_target, rel = mapped
            index.record_fork(rel, origin=target, app_version=app_version)
            write_text_atomic(text, overlay_target)
            index.save(app_version)
            return SaveResult(path=overlay_target, forked=True, rel=rel)

    made_backup = index.backup(target) if (backup and target.is_file()) else None
    write_text_atomic(text, target)
    rel = rel_posix(target, index.overlay_root)
    if rel is not None and rel not in index.overrides:
        # An overlay file with no record: a compound saved before the index
        # existed, or one copied in by hand. Claim it as the user's own so the
        # UI can offer a real delete rather than only a tombstone.
        index.record_new(rel)
        index.save(app_version)
    return SaveResult(path=target, forked=forked, backup=made_backup, rel=rel)


def save_as_new(
    text: str,
    target_dir: Path,
    name: str,
    *,
    original_text: str = "",
    index: OverrideIndex | None = None,
    app_version: str = "",
    allow_shrink: bool = False,
) -> SaveResult:
    """Write ``text`` as a new component called ``name``.

    A new name is a new library key, so this shadows nothing and needs no fork
    record — only a note that the file is the user's own.
    """
    _sanity_check(text, original_text, allow_shrink=allow_shrink)
    target = target_dir / f"{name}.svg" if not name.endswith(".svg") \
        else target_dir / name
    write_text_atomic(text, target)
    rel = None
    if index is not None:
        rel = rel_posix(target, index.overlay_root)
        if rel is not None:
            index.record_new(rel)
            index.save(app_version)
    return SaveResult(path=target, rel=rel)


def suggest_new_name(source: Path, existing_names: set[str]) -> str:
    """``cap`` -> ``cap_v2``, then ``cap_v3``, avoiding what already exists."""
    stem = source.stem
    base = re.sub(r"_v\d+$", "", stem)
    n = 2
    while f"{base}_v{n}" in existing_names:
        n += 1
    return f"{base}_v{n}"
