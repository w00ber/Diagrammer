"""ComponentLibrary — scan a directory tree for SVG component definitions."""

from __future__ import annotations

import logging
from pathlib import Path

from diagrammer.models.component_def import ComponentDef

logger = logging.getLogger(__name__)


class ComponentLibrary:
    """A library of reusable SVG component definitions organized by category.

    Directory layout:
        library_root/
            category_a/
                component1.svg
                component2.svg
            category_b/
                component3.svg
    """

    def __init__(self) -> None:
        self._categories: dict[str, list[ComponentDef]] = {}
        self._by_key: dict[str, ComponentDef] = {}  # "category/name" -> def
        # Paths that lost a key collision. The duplicate-skip in scan() used to
        # throw this away, but it is exactly what the UI needs: when a user's
        # overlay shadows a shipped part, the panel has to be able to say so and
        # to offer "compare with built-in".
        self._shadowed: dict[str, list[Path]] = {}
        # Files that failed to parse, and why. Scanning still swallows the
        # failure so a bad file cannot stop startup, but recording it means the
        # Library panel can report "3 components failed to load" instead of the
        # component silently vanishing.
        self._failures: dict[Path, str] = {}

    @property
    def categories(self) -> dict[str, list[ComponentDef]]:
        return self._categories

    @property
    def failures(self) -> dict[Path, str]:
        return self._failures

    def clear(self) -> None:
        """Remove all loaded categories and definitions."""
        self._categories.clear()
        self._by_key.clear()
        self._shadowed.clear()
        self._failures.clear()

    def source_of(self, key: str) -> Path | None:
        """The file a loaded component came from."""
        comp = self._by_key.get(key)
        return comp.svg_path if comp is not None else None

    def shadowed_paths(self, key: str) -> list[Path]:
        """Files that define ``key`` but lost to the one that won.

        Normally one entry: the shipped component behind a user's fork.
        """
        return list(self._shadowed.get(key, ()))

    def get(self, key: str) -> ComponentDef | None:
        """Look up a component by its library key (e.g. 'flowchart/process')."""
        return self._by_key.get(key)

    def scan(self, root: Path, *, source_kind: str = "",
             flat_category: str | None = None) -> None:
        """Scan a directory tree recursively and load all SVG component definitions.

        Supports nested directories: electrical/simple/resistor.svg gets
        category 'electrical/simple' and key 'electrical/simple/resistor'.

        **Scan order is precedence.** A duplicate key is skipped, so whichever
        root is scanned first wins. That is what makes the user's overlay shadow
        the built-in tree without any extra precedence logic — the overlay
        mirrors the built-in layout, so the keys collide by construction.

        ``source_kind`` tags every def loaded from this root ("builtin",
        "overlay", "custom", ...) so the UI can tell the user's own components
        from the shipped ones. Both new arguments are keyword-only with
        behaviour-preserving defaults, so existing callers are unaffected.
        """
        if not root.is_dir():
            return

        for svg_file in sorted(root.rglob("*.svg")):
            # Skip hidden directories
            rel = svg_file.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue

            # Category is the parent directory path relative to root.
            # Top-level SVGs (no subdirectory) get the root folder's name
            # as their category, so user libraries with a flat layout
            # still appear in the panel.
            if len(rel.parts) < 2:
                category = flat_category or root.name or "user"
            else:
                category = str(rel.parent).replace("\\", "/")

            try:
                comp_def = ComponentDef.from_svg(svg_file, category=category)
                comp_def.source_root = root
                comp_def.source_kind = source_kind
                key = f"{category}/{comp_def.name}"
                if key in self._by_key:
                    # Record what lost rather than dropping it: this is how the
                    # panel knows a fork is shadowing a shipped component.
                    self._shadowed.setdefault(key, []).append(svg_file)
                    continue
                self._by_key[key] = comp_def
                if category not in self._categories:
                    self._categories[category] = []
                self._categories[category].append(comp_def)
            except Exception as exc:
                self._failures[svg_file] = str(exc)
                logger.warning("Failed to load %s", svg_file, exc_info=True)

    def remove_key(self, key: str) -> bool:
        """Unload one component, leaving its file alone.

        Used for a per-component tombstone: the shipped file stays on disk
        (inside a read-only app bundle, it has to), so hiding is purely a
        question of what the library reports. That also makes it reversible,
        which is the right semantics for data the user did not author.
        """
        comp = self._by_key.pop(key, None)
        if comp is None:
            return False
        bucket = self._categories.get(comp.category)
        if bucket is not None:
            self._categories[comp.category] = [d for d in bucket if d is not comp]
            if not self._categories[comp.category]:
                del self._categories[comp.category]
        return True

    def reload_one(self, key: str) -> ComponentDef | None:
        """Re-parse a loaded component in place, keeping its panel position.

        ``add_file`` appends, which would make a component the user just edited
        jump to the end of its category. After an in-place save the only thing
        that changed is the file's content, so the def is swapped where it sits.
        """
        old = self._by_key.get(key)
        if old is None:
            return None
        try:
            fresh = ComponentDef.from_svg(old.svg_path, category=old.category)
        except Exception as exc:
            self._failures[old.svg_path] = str(exc)
            logger.warning("Failed to reload %s", old.svg_path, exc_info=True)
            return None
        fresh.source_root = old.source_root
        fresh.source_kind = old.source_kind
        self._by_key[key] = fresh
        bucket = self._categories.get(old.category, [])
        for i, d in enumerate(bucket):
            if d is old:
                bucket[i] = fresh
                break
        else:
            bucket.append(fresh)
        self._failures.pop(old.svg_path, None)
        return fresh

    def add_file(self, svg_file: Path, category: str | None = None) -> ComponentDef | None:
        """Load a single SVG file into the library.

        Used for user-saved compounds that live outside the builtin tree.
        ``category`` defaults to the file's parent directory name.
        Returns the loaded ComponentDef, or None on failure / duplicate.
        """
        if not svg_file.is_file():
            return None
        if category is None:
            category = svg_file.parent.name or "user"
        try:
            comp_def = ComponentDef.from_svg(svg_file, category=category)
        except Exception:
            logger.warning("Failed to load %s", svg_file, exc_info=True)
            return None
        key = f"{category}/{comp_def.name}"
        if key in self._by_key:
            # Replace existing entry so re-saving an edited compound updates it.
            old = self._by_key[key]
            bucket = self._categories.get(category, [])
            self._categories[category] = [d for d in bucket if d is not old]
        self._by_key[key] = comp_def
        self._categories.setdefault(category, []).append(comp_def)
        return comp_def

    def all_defs(self) -> list[ComponentDef]:
        """Return a flat list of all component definitions."""
        result: list[ComponentDef] = []
        for defs in self._categories.values():
            result.extend(defs)
        return result
