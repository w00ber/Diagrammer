"""Re-save every bundled example in the current .dgm file format.

Run this after bumping ``FORMAT_MAJOR`` or ``FORMAT_MINOR`` in
``diagrammer/io/serializer.py`` so the shipped examples stay in sync with
the current schema (and any migrations have been baked in).

Usage:
    python tools/resave_examples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sure the in-tree package is importable when running from the repo.
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    # A QApplication is required because DiagramScene is a QGraphicsScene.
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    from diagrammer._resources import resource_path
    from diagrammer.canvas.scene import DiagramScene
    from diagrammer.io.serializer import FORMAT_VERSION, DiagramSerializer
    from diagrammer.models.library import ComponentLibrary

    examples = sorted(resource_path("examples").glob("*.dgm"))
    if not examples:
        print("No examples found.")
        return 0

    library = ComponentLibrary()
    builtin = resource_path("components")
    if builtin.is_dir():
        library.scan(builtin)

    print(f"Re-saving {len(examples)} example(s) in format {FORMAT_VERSION}...")
    failed: list[tuple[Path, str]] = []
    for path in examples:
        try:
            scene = DiagramScene(library=library)
            DiagramSerializer.load(scene, path, library=library)
            DiagramSerializer.save(scene, path)
            print(f"  ok   {path.name}")
        except Exception as exc:
            print(f"  FAIL {path.name}: {exc}")
            failed.append((path, str(exc)))

    if failed:
        print(f"\n{len(failed)} example(s) failed to re-save.")
        return 1
    print("\nAll examples re-saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
