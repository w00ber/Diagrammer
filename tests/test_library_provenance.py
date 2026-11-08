"""Overlay precedence, provenance, and the newly-visible scan failures.

The load-bearing fact here is that **scan order is precedence**:
``ComponentLibrary.scan`` skips a key it has already loaded, so scanning the
user's overlay before the built-in tree is the entire copy-on-write mechanism.
No separate precedence logic exists, which is why these tests guard the ordering
rather than some resolver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from diagrammer.models.library import ComponentLibrary

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 100">\n'
       '  <g id="artwork"><path d="M0,50H60"/></g>\n'
       '  <g id="ports"><circle id="port:left" cx="0" cy="50" r="3"/></g>\n'
       "</svg>\n")


def part(root: Path, rel: str, marker: str = "") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = SVG.replace("M0,50H60", f"M0,50H60{marker}") if marker else SVG
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def trees(tmp_path: Path):
    builtin = tmp_path / "builtin"
    overlay = tmp_path / "overlay"
    part(builtin, "PAPER_2pt/sw/SP6T.svg")
    part(builtin, "PAPER_2pt/sw/SPDT.svg")
    return overlay, builtin


def test_overlay_scanned_first_shadows_the_builtin(trees) -> None:
    overlay, builtin = trees
    mine = part(overlay, "PAPER_2pt/sw/SP6T.svg", marker="V")

    lib = ComponentLibrary()
    lib.scan(overlay, source_kind="overlay")
    lib.scan(builtin, source_kind="builtin")

    key = "PAPER_2pt/sw/SP6T"
    assert lib.source_of(key) == mine
    assert lib.get(key).source_kind == "overlay"
    # one component in the panel, not two
    assert len(lib.categories["PAPER_2pt/sw"]) == 2
    # and the shipped file it hid is still reachable, for "compare with built-in"
    assert lib.shadowed_paths(key) == [builtin / "PAPER_2pt/sw/SP6T.svg"]


def test_scanning_the_builtin_first_would_lose_the_edit(trees) -> None:
    """The negative of the above, stated so the ordering is not refactored away
    by someone who reads the two scans as interchangeable."""
    overlay, builtin = trees
    part(overlay, "PAPER_2pt/sw/SP6T.svg", marker="V")

    lib = ComponentLibrary()
    lib.scan(builtin, source_kind="builtin")
    lib.scan(overlay, source_kind="overlay")

    assert lib.get("PAPER_2pt/sw/SP6T").source_kind == "builtin"


def test_untouched_components_keep_coming_from_the_builtin(trees) -> None:
    """The point of copy-on-write: only what the user edited is overridden, so
    everything else still receives app updates."""
    overlay, builtin = trees
    part(overlay, "PAPER_2pt/sw/SP6T.svg", marker="V")

    lib = ComponentLibrary()
    lib.scan(overlay, source_kind="overlay")
    lib.scan(builtin, source_kind="builtin")

    assert lib.get("PAPER_2pt/sw/SPDT").is_builtin
    assert lib.get("PAPER_2pt/sw/SP6T").is_user_owned


def test_provenance_flags(trees) -> None:
    overlay, builtin = trees
    lib = ComponentLibrary()
    lib.scan(builtin, source_kind="builtin")
    comp = lib.get("PAPER_2pt/sw/SP6T")
    assert comp.is_builtin and not comp.is_user_owned
    assert comp.source_root == builtin

    for kind in ("overlay", "custom", "compound"):
        other = ComponentLibrary()
        other.scan(builtin, source_kind=kind)
        assert other.get("PAPER_2pt/sw/SP6T").is_user_owned


def test_source_kind_defaults_to_empty_for_old_callers(trees) -> None:
    """Both new scan arguments are keyword-only with behaviour-preserving
    defaults, so the pre-existing call sites are untouched."""
    _, builtin = trees
    lib = ComponentLibrary()
    lib.scan(builtin)  # the old one-argument signature
    comp = lib.get("PAPER_2pt/sw/SP6T")
    assert comp.source_kind == ""
    assert not comp.is_builtin and not comp.is_user_owned


def test_flat_layout_category_default_is_unchanged(tmp_path: Path) -> None:
    """A top-level SVG takes the root folder's name, so a user library with a
    flat layout still appears in the panel."""
    root = tmp_path / "MyLib"
    part(root, "widget.svg")
    lib = ComponentLibrary()
    lib.scan(root)
    assert "MyLib" in lib.categories
    assert lib.get("MyLib/widget") is not None


def test_flat_category_can_be_overridden(tmp_path: Path) -> None:
    """The overlay root is called "components", which would be a meaningless
    category name in the panel."""
    root = tmp_path / "components"
    part(root, "widget.svg")
    lib = ComponentLibrary()
    lib.scan(root, flat_category="My Components")
    assert "My Components" in lib.categories


# ==========================================================================
# scan failures become visible
# ==========================================================================

def test_a_broken_file_is_recorded_instead_of_vanishing_silently(
        tmp_path: Path) -> None:
    """Until now scan() logged and dropped it, so the user watched a component
    disappear from the panel with no explanation."""
    root = tmp_path / "lib"
    part(root, "good.svg")
    bad = root / "bad.svg"
    bad.write_text("<notsvg/>", encoding="utf-8")

    lib = ComponentLibrary()
    lib.scan(root, source_kind="custom")

    assert lib.get("lib/good") is not None
    assert lib.get("lib/bad") is None, "a broken file must still not load"
    assert bad in lib.failures
    assert "svg" in lib.failures[bad].lower()


def test_clear_resets_provenance_and_failures(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    part(root, "good.svg")
    (root / "bad.svg").write_text("<notsvg/>", encoding="utf-8")
    lib = ComponentLibrary()
    lib.scan(root)
    assert lib.failures
    lib.clear()
    assert lib.failures == {}
    assert lib.shadowed_paths("lib/good") == []


# ==========================================================================
# remove_key and reload_one
# ==========================================================================

def test_remove_key_unloads_without_touching_the_file(trees) -> None:
    overlay, builtin = trees
    lib = ComponentLibrary()
    lib.scan(builtin, source_kind="builtin")
    path = lib.source_of("PAPER_2pt/sw/SP6T")

    assert lib.remove_key("PAPER_2pt/sw/SP6T") is True
    assert lib.get("PAPER_2pt/sw/SP6T") is None
    assert path.is_file(), "hiding is about what the library reports, not disk"
    assert lib.remove_key("PAPER_2pt/sw/SP6T") is False


def test_remove_key_drops_an_emptied_category(trees) -> None:
    overlay, builtin = trees
    lib = ComponentLibrary()
    lib.scan(builtin, source_kind="builtin")
    lib.remove_key("PAPER_2pt/sw/SP6T")
    lib.remove_key("PAPER_2pt/sw/SPDT")
    assert "PAPER_2pt/sw" not in lib.categories


def test_reload_one_keeps_the_components_position(trees) -> None:
    """add_file appends, which would make a component the user just edited jump
    to the end of its category in the panel."""
    overlay, builtin = trees
    part(builtin, "PAPER_2pt/sw/AAA.svg")
    lib = ComponentLibrary()
    lib.scan(builtin, source_kind="builtin")

    bucket = lib.categories["PAPER_2pt/sw"]
    names_before = [d.name for d in bucket]
    index_before = names_before.index("SP6T")

    target = lib.source_of("PAPER_2pt/sw/SP6T")
    target.write_text(
        SVG.replace('cx="0" cy="50"', 'cx="60" cy="50"'), encoding="utf-8")

    fresh = lib.reload_one("PAPER_2pt/sw/SP6T")
    assert fresh is not None
    assert fresh.ports[0].x == 60.0, "the reloaded def reflects the new file"
    assert fresh.source_kind == "builtin", "provenance is carried over"

    bucket = lib.categories["PAPER_2pt/sw"]
    assert [d.name for d in bucket] == names_before
    assert bucket[index_before] is fresh
    assert lib.get("PAPER_2pt/sw/SP6T") is fresh


def test_reload_one_on_a_now_broken_file_records_the_failure(trees) -> None:
    overlay, builtin = trees
    lib = ComponentLibrary()
    lib.scan(builtin, source_kind="builtin")
    target = lib.source_of("PAPER_2pt/sw/SP6T")
    target.write_text("<notsvg/>", encoding="utf-8")

    assert lib.reload_one("PAPER_2pt/sw/SP6T") is None
    assert target in lib.failures


def test_reload_one_on_an_unknown_key_is_a_noop(trees) -> None:
    overlay, builtin = trees
    lib = ComponentLibrary()
    lib.scan(builtin, source_kind="builtin")
    assert lib.reload_one("nope/missing") is None


# ==========================================================================
# MainWindow wiring
# ==========================================================================

def test_main_window_scans_the_overlay_and_shadows_a_builtin(
        isolate_component_overlay, _ensure_qapp) -> None:
    """End to end: a fork in the overlay replaces the shipped component, and
    the panel shows one entry, not two.

    ``isolate_component_overlay`` is the autouse fixture that redirects
    ``default_overlay_root`` to a tmp dir, so this never reads the developer's
    own overlay.
    """
    from diagrammer.main_window import MainWindow, _find_builtin_components

    builtin = _find_builtin_components()
    shipped = next(iter(sorted((builtin / "flowchart").glob("*.svg"))))
    rel = shipped.relative_to(builtin)

    mine = isolate_component_overlay / rel
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text(shipped.read_text(encoding="utf-8")
                    .replace("</svg>", '<g id="mine"/></svg>'), encoding="utf-8")

    win = MainWindow()
    try:
        key = f"{rel.parent.as_posix()}/{shipped.stem}"
        comp = win._library.get(key)
        assert comp is not None, f"{key} not loaded"
        assert comp.svg_path == mine
        assert comp.source_kind == "overlay"
        assert win._library.shadowed_paths(key) == [shipped]
        assert [d.name for d in win._library.categories[rel.parent.as_posix()]
                ].count(shipped.stem) == 1
    finally:
        win.close()
        win.deleteLater()


def test_main_window_hides_a_tombstoned_builtin(
        isolate_component_overlay, _ensure_qapp) -> None:
    """A built-in file cannot be deleted — it may sit inside a signed .app — so
    Remove on one is a reversible tombstone, applied at load."""
    from diagrammer.main_window import MainWindow, _find_builtin_components

    builtin = _find_builtin_components()
    shipped = next(iter(sorted((builtin / "flowchart").glob("*.svg"))))
    rel = shipped.relative_to(builtin).as_posix()
    key = f"{Path(rel).parent.as_posix()}/{shipped.stem}"

    win = MainWindow()
    try:
        assert win._library.get(key) is not None, "present before hiding"

        win._overrides.hide("builtin", rel, reason="not needed")
        win._rebuild_library()
        assert win._library.get(key) is None

        win._overrides.unhide("builtin", rel)
        win._rebuild_library()
        assert win._library.get(key) is not None, "hiding must be reversible"
        assert shipped.is_file(), "the shipped file is never touched"
    finally:
        win.close()
        win.deleteLater()
