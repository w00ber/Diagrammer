"""ComponentLibrary — scan a directory tree for SVG component definitions."""

from __future__ import annotations

from pathlib import Path

from diagrammer.models.component_def import ComponentDef


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

    @property
    def categories(self) -> dict[str, list[ComponentDef]]:
        return self._categories

    def clear(self) -> None:
        """Remove all loaded categories and definitions."""
        self._categories.clear()
        self._by_key.clear()

    def get(self, key: str) -> ComponentDef | None:
        """Look up a component by its library key (e.g. 'flowchart/process')."""
        return self._by_key.get(key)

    def scan(self, root: Path) -> None:
        """Scan a directory tree recursively and load all SVG component definitions.

        Supports nested directories: electrical/simple/resistor.svg gets
        category 'electrical/simple' and key 'electrical/simple/resistor'.
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
                category = root.name or "user"
            else:
                category = str(rel.parent).replace("\\", "/")

            try:
                comp_def = ComponentDef.from_svg(svg_file, category=category)
                key = f"{category}/{comp_def.name}"
                if key in self._by_key:
                    continue  # already loaded — skip duplicate
                self._by_key[key] = comp_def
                if category not in self._categories:
                    self._categories[category] = []
                self._categories[category].append(comp_def)
            except Exception as e:
                print(f"Warning: failed to load {svg_file}: {e}")

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
        except Exception as e:
            print(f"Warning: failed to load {svg_file}: {e}")
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
