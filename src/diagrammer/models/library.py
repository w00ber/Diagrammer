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

            # Category is the parent directory path relative to root
            if len(rel.parts) < 2:
                continue  # SVGs must be in at least one subdirectory
            category = str(rel.parent).replace("\\", "/")

            try:
                comp_def = ComponentDef.from_svg(svg_file, category=category)
                key = f"{category}/{comp_def.name}"
                self._by_key[key] = comp_def
                if category not in self._categories:
                    self._categories[category] = []
                self._categories[category].append(comp_def)
            except Exception as e:
                print(f"Warning: failed to load {svg_file}: {e}")

    def all_defs(self) -> list[ComponentDef]:
        """Return a flat list of all component definitions."""
        result: list[ComponentDef] = []
        for defs in self._categories.values():
            result.extend(defs)
        return result
