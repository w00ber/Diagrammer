"""The user's component overlay, and what it remembers about the built-ins.

The problem this solves: the shipped component tree lives inside the installed
package — on macOS inside a signed ``.app`` bundle, which is read-only — so a
user cannot edit a shipped part in place, and an app update would overwrite the
edit anyway. The answer is copy-on-write.

``~/.diagrammer/components/`` mirrors the built-in tree by relative path and is
scanned *first*, so it shadows the built-in on a key collision
(``ComponentLibrary.scan`` skips a duplicate key, which makes scan order the
precedence rule). Only the parts the user actually edits get copied, so every
untouched component keeps receiving updates.

That leaves one real question, which this module exists to answer: what happens
when an upgrade changes the shipped version of something the user has forked?
Each fork records the SHA-256 of the built-in it came from, so
:meth:`OverrideIndex.drift` can say "the shipped version of this changed since
you forked it" and let the user choose, rather than silently preferring either
copy forever.

Deletion follows the same grain. A built-in file cannot be removed, so "remove"
on one is a reversible tombstone. A component in the user's *own* library is
theirs: they get a real delete too, because tombstoning everything just hoards
junk. The ``deleted`` log and the ``hidden`` timestamps are what make a later
cleanup pass possible — dead tombstones whose target is gone upstream, and forks
whose content now matches the current built-in.

This is deliberately **not** a module-level singleton. ``app_settings`` already
is one, and the test suite needs an autouse fixture to isolate it; a second
global would need a second fixture. ``MainWindow`` owns the instance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: Bumped only for a change that an older app could not read correctly.
SCHEMA = 1

INDEX_NAME = ".overrides.json"
BACKUPS_DIRNAME = ".backups"


def default_overlay_root() -> Path:
    """``~/.diagrammer/components/`` — the user's copy-on-write overlay.

    A module function rather than a constant so tests can redirect it, the way
    ``tests/conftest.py`` already redirects the settings file.
    """
    return Path.home() / ".diagrammer" / "components"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str | None:
    """Hex digest of a file, or ``None`` if it cannot be read."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def rel_posix(path: Path, root: Path) -> str | None:
    """``path`` relative to ``root`` as a POSIX string, or None if outside.

    POSIX and relative because these are the index's keys: an absolute path
    would not survive a different machine or a different install location.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


@dataclass(frozen=True)
class Drift:
    """A fork whose upstream built-in has changed since it was taken."""

    rel: str
    overlay_path: Path
    builtin_path: Path
    forked_app_version: str
    forked_at: str


class OverrideIndex:
    """``~/.diagrammer/components/.overrides.json`` — reads and writes.

    Lives *inside* the overlay so that backing up or syncing the overlay carries
    its own provenance with it. The dot prefix means ``ComponentLibrary.scan``
    skips it, so it can never be mistaken for a component.
    """

    def __init__(self, overlay_root: Path, builtin_root: Path | None = None):
        self.overlay_root = overlay_root
        self.builtin_root = builtin_root
        self.overrides: dict[str, dict] = {}
        self.hidden: dict[str, dict] = {}
        self.deleted: list[dict] = []
        self.app_version: str = ""
        #: True when the file on disk came from a newer app. Then we never
        #: write, because saving our narrower understanding back would drop
        #: whatever that version recorded.
        self.read_only = False
        self._loaded_schema = SCHEMA

    # -- paths ------------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.overlay_root / INDEX_NAME

    @property
    def backups_dir(self) -> Path:
        return self.overlay_root / BACKUPS_DIRNAME

    def overlay_path(self, rel: str) -> Path:
        return self.overlay_root / rel

    def builtin_path(self, rel: str) -> Path | None:
        return (self.builtin_root / rel) if self.builtin_root else None

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, overlay_root: Path,
             builtin_root: Path | None = None) -> OverrideIndex:
        """Read the index, tolerating absence and damage.

        A missing file is the normal case for a user who has never edited a
        component. A damaged one is reported and treated as empty rather than
        raising: the overlay's *files* are the real data, and refusing to start
        because an index is corrupt would be the wrong trade.
        """
        idx = cls(overlay_root, builtin_root)
        path = idx.index_path
        if not path.is_file():
            return idx
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("%s is unreadable (%s) — treating the overlay as "
                           "having no recorded provenance", path, exc)
            return idx
        if not isinstance(data, dict):
            logger.warning("%s does not hold a JSON object — ignoring it", path)
            return idx

        idx._loaded_schema = int(data.get("schema", SCHEMA) or SCHEMA)
        if idx._loaded_schema > SCHEMA:
            idx.read_only = True
            logger.warning(
                "%s was written by a newer version of Diagrammer (schema %d > "
                "%d); loading it read-only so nothing is lost",
                path, idx._loaded_schema, SCHEMA)

        idx.app_version = str(data.get("app_version", ""))
        got = data.get("overrides")
        if isinstance(got, dict):
            idx.overrides = {str(k): dict(v) for k, v in got.items()
                             if isinstance(v, dict)}
        got = data.get("hidden")
        if isinstance(got, dict):
            idx.hidden = {str(k): dict(v) for k, v in got.items()
                          if isinstance(v, dict)}
        got = data.get("deleted")
        if isinstance(got, list):
            idx.deleted = [dict(d) for d in got if isinstance(d, dict)]
        return idx

    def save(self, app_version: str = "") -> bool:
        """Write the index atomically. Returns False if the write was refused.

        Atomic because a half-applied *move* is the dangerous case: moving a
        built-in is a fork plus a tombstone, and an index that recorded the fork
        but lost the tombstone would show the component twice.
        """
        if self.read_only:
            logger.warning("refusing to overwrite %s, which a newer version of "
                           "Diagrammer wrote", self.index_path)
            return False
        payload = {
            "schema": SCHEMA,
            "app_version": app_version or self.app_version,
            "updated": _now(),
            "overrides": self.overrides,
            "hidden": self.hidden,
            "deleted": self.deleted,
        }
        try:
            self.overlay_root.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False)
                           + "\n", encoding="utf-8")
            os.replace(tmp, self.index_path)
            return True
        except OSError as exc:
            logger.error("could not write %s: %s", self.index_path, exc)
            return False

    # -- forks ------------------------------------------------------------

    def record_fork(self, rel: str, *, origin: Path,
                    app_version: str = "", note: str = "") -> None:
        """Record that ``rel`` in the overlay was copied from a built-in.

        ``origin_sha256`` and ``last_seen_origin_sha256`` start equal but mean
        different things and must stay separate: the first is the immutable fork
        point, which a future three-way compare needs, while the second is only
        a watermark for "stop telling me about this change".
        """
        digest = sha256_file(origin)
        self.overrides[rel] = {
            "kind": "fork",
            "origin": "builtin",
            "origin_rel": rel_posix(origin, self.builtin_root)
            if self.builtin_root else None,
            "origin_sha256": digest,
            "forked_app_version": app_version,
            "forked_at": _now(),
            "last_seen_origin_sha256": digest,
            "upstream_decision": None,
            "note": note,
        }

    def record_new(self, rel: str, *, note: str = "") -> None:
        """Record a component that is the user's own, shadowing nothing."""
        self.overrides[rel] = {
            "kind": "new",
            "origin": None,
            "created_at": _now(),
            "note": note,
        }

    def forget(self, rel: str) -> None:
        """Drop the record for ``rel`` — used when reverting to the built-in."""
        self.overrides.pop(rel, None)

    def is_fork(self, rel: str) -> bool:
        return self.overrides.get(rel, {}).get("kind") == "fork"

    def drift(self) -> list[Drift]:
        """Forks whose built-in has changed since the fork, or since last seen.

        Cheap enough to run at startup: forks are user-scale, tens of files of a
        few KB. Compares against ``last_seen_origin_sha256`` rather than
        ``origin_sha256`` so that answering "keep mine" silences this until the
        built-in changes *again*, instead of forever or every launch.
        """
        if self.builtin_root is None:
            return []
        out: list[Drift] = []
        for rel, rec in sorted(self.overrides.items()):
            if rec.get("kind") != "fork":
                continue
            builtin = self.builtin_path(rel)
            if builtin is None or not builtin.is_file():
                continue  # upstream removed it; a cleanup pass handles that
            seen = rec.get("last_seen_origin_sha256")
            if seen is None:
                continue
            current = sha256_file(builtin)
            if current is None or current == seen:
                continue
            out.append(Drift(
                rel=rel,
                overlay_path=self.overlay_path(rel),
                builtin_path=builtin,
                forked_app_version=str(rec.get("forked_app_version", "")),
                forked_at=str(rec.get("forked_at", "")),
            ))
        return out

    def accept_upstream_change(self, rel: str) -> None:
        """"Keep mine": advance the watermark so this stops being reported."""
        rec = self.overrides.get(rel)
        if rec is None:
            return
        builtin = self.builtin_path(rel)
        if builtin is not None and builtin.is_file():
            rec["last_seen_origin_sha256"] = sha256_file(builtin)
        rec["upstream_decision"] = "keep_mine"

    def fork_matches_builtin(self, rel: str) -> bool:
        """True when a fork's content now equals the current built-in.

        Means the override is redundant — the edit was reverted, or upstream
        caught up — which is the main thing a future cleanup pass looks for.
        """
        builtin = self.builtin_path(rel)
        overlay = self.overlay_path(rel)
        if builtin is None or not builtin.is_file() or not overlay.is_file():
            return False
        return sha256_file(builtin) == sha256_file(overlay)

    # -- tombstones -------------------------------------------------------
    #
    # Keys are namespaced because a user library can live anywhere, so only the
    # built-in tree has a meaningful relative root:
    #   builtin:<rel-from-builtin-root>
    #   custom:<absolute path>

    @staticmethod
    def hidden_key(scope: str, key: str) -> str:
        return f"{scope}:{key}"

    def hide(self, scope: str, key: str, *, reason: str = "") -> None:
        self.hidden[self.hidden_key(scope, key)] = {
            "at": _now(), "reason": reason}

    def unhide(self, scope: str, key: str) -> bool:
        return self.hidden.pop(self.hidden_key(scope, key), None) is not None

    def is_hidden(self, scope: str, key: str) -> bool:
        return self.hidden_key(scope, key) in self.hidden

    def hidden_builtin_rels(self) -> set[str]:
        return {k.split(":", 1)[1] for k in self.hidden
                if k.startswith("builtin:")}

    def hidden_custom_paths(self) -> set[str]:
        return {k.split(":", 1)[1] for k in self.hidden
                if k.startswith("custom:")}

    def dead_tombstones(self) -> list[str]:
        """Tombstone keys whose target no longer exists.

        A built-in that an update removed, or a user file deleted outside the
        app. Harmless, but they accumulate, which is what the user asked not to
        happen.
        """
        dead: list[str] = []
        for key in sorted(self.hidden):
            scope, _, rest = key.partition(":")
            if scope == "builtin":
                target = self.builtin_path(rest)
                if target is None or not target.is_file():
                    dead.append(key)
            elif scope == "custom" and not Path(rest).is_file():
                dead.append(key)
        return dead

    # -- deletes ----------------------------------------------------------

    def record_delete(self, *, rel: str, trash: Path | None) -> None:
        self.deleted.append({
            "rel": rel,
            "at": _now(),
            "trash": str(trash) if trash else None,
        })

    # -- backups ----------------------------------------------------------

    def backup(self, path: Path) -> Path | None:
        """Copy ``path`` into the overlay's ``.backups/`` before overwriting it.

        The last-resort undo for a destructive file operation, which is why
        these edits stay out of the QUndoCommand stack: a document's undo stack
        is the wrong scope for a global library change, and ``undo()`` has to be
        infallible, which un-deleting a file is not.
        """
        rel = rel_posix(path, self.overlay_root) or path.name
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.backups_dir / f"{rel}.{stamp}.svg"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            return target
        except OSError as exc:
            logger.warning("could not back up %s: %s", path, exc)
            return None
