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
        """Scan a directory tree and load all SVG component definitions."""
        if not root.is_dir():
            return

        for category_dir in sorted(root.iterdir()):
            if not category_dir.is_dir() or category_dir.name.startswith("."):
                continue

            category = category_dir.name
            defs: list[ComponentDef] = []

            for svg_file in sorted(category_dir.glob("*.svg")):
                try:
                    comp_def = ComponentDef.from_svg(svg_file, category=category)
                    defs.append(comp_def)
                    self._by_key[f"{category}/{comp_def.name}"] = comp_def
                except Exception as e:
                    print(f"Warning: failed to load {svg_file}: {e}")

            if defs:
                self._categories[category] = defs

    def all_defs(self) -> list[ComponentDef]:
        """Return a flat list of all component definitions."""
        result: list[ComponentDef] = []
        for defs in self._categories.values():
            result.extend(defs)
        return result
