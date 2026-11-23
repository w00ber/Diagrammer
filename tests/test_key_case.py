"""Library folder case must not matter: git tracks ``flair/``, a Mac disk held
``FLAIR/``, and keys saved on one machine are read on the other.

Before this, the mismatch cost three things silently: a .dgm saved with
``FLAIR/...`` keys dropped those parts on a ``flair/`` tree (unless the SVG
happened to be embedded), every flair part on a fresh clone was validated under
the generic profile, and a fresh install hid the flair library.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF

from diagrammer import svglint
from diagrammer.models.library import ComponentLibrary

COMPONENTS = Path(__file__).parent.parent / "src" / "diagrammer" / "components"


def _flair_key(library) -> str:
    key = next(k for k in library._by_key if k.casefold().startswith("flair/"))
    return key


def _swap_case_of_top(key: str) -> str:
    top, rest = key.split("/", 1)
    return (top.upper() if top != top.upper() else top.lower()) + "/" + rest


def test_get_accepts_a_unique_case_insensitive_match(library) -> None:
    key = _flair_key(library)
    other = _swap_case_of_top(key)
    assert other != key
    assert library.get(other) is library.get(key)


def test_get_refuses_to_guess_between_keys_differing_only_in_case(
        tmp_path: Path) -> None:
    # Two folders differing only in case cannot coexist on a case-insensitive
    # disk (macOS by default), so this case only arises — and is only
    # testable — on a case-sensitive one.
    (tmp_path / "Parts").mkdir()
    if (tmp_path / "parts").exists():
        pytest.skip("case-insensitive filesystem: Parts/ and parts/ are one folder")
    (tmp_path / "parts").mkdir()
    src = next((COMPONENTS / "flowchart").glob("*.svg"))
    for folder in ("Parts", "parts"):
        (tmp_path / folder / src.name).write_bytes(src.read_bytes())
    lib = ComponentLibrary()
    lib.scan(tmp_path)
    assert len(lib._by_key) == 2
    stem = src.stem
    assert lib.get(f"Parts/{stem}") is not lib.get(f"parts/{stem}")
    assert lib.get(f"PARTS/{stem}") is None


def test_dgm_saved_with_other_folder_case_still_loads_its_parts(
        library, tmp_path: Path) -> None:
    from diagrammer.canvas.scene import DiagramScene
    from diagrammer.io.serializer import DiagramSerializer
    from diagrammer.items.component_item import ComponentItem

    key = _flair_key(library)
    scene = DiagramScene(library=library)
    item = ComponentItem(library.get(key))
    item.setPos(QPointF(100, 100))
    scene.addItem(item)
    path = tmp_path / "saved.dgm"
    DiagramSerializer.save(scene, path)

    # Rewrite as the other machine would have saved it, and make sure the
    # embedded-SVG fallback cannot rescue it: the key lookup must.
    data = json.loads(path.read_text())
    for cd in data["components"]:
        cd["def_key"] = _swap_case_of_top(cd["def_key"])
        cd.pop("embedded_svg", None)
    path.write_text(json.dumps(data))

    fresh = DiagramScene(library=library)
    DiagramSerializer.load(fresh, path, library=library)
    parts = [i for i in fresh.items() if isinstance(i, ComponentItem)]
    assert len(parts) == 1
    assert parts[0].component_def is library.get(key)


def test_flair_parts_get_the_flair_profile_whatever_the_folder_case() -> None:
    for top in ("flair", "FLAIR", "Flair"):
        assert svglint.builtin_profile_name(Path(top) / "edges" / "x.svg") == "FLAIR"
        assert svglint.profile_for(Path(top) / "edges" / "x.svg").name == "FLAIR"
    shipped = next((COMPONENTS / "flair").rglob("*.svg"))
    assert svglint.profile_for(shipped).name == "FLAIR"


def test_first_launch_keeps_the_flair_library_visible(library) -> None:
    from diagrammer.panels.settings_dialog import app_settings

    app_settings._first_launch = True
    app_settings.apply_first_launch_library_defaults(library)
    flair = [c for c in library.categories if c.casefold().startswith("flair")]
    assert flair
    assert not set(flair) & app_settings.hidden_libraries
