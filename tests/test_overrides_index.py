"""The override index: forks, tombstones, deletes, drift.

This is the bookkeeping that makes copy-on-write safe across an app update. The
question it exists to answer is the one editing a shipped component raises: when
the next release changes the built-in you forked, which copy wins? Neither,
silently — the index remembers the SHA-256 of the built-in at fork time so the
user gets asked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagrammer.models.overrides import (
    INDEX_NAME,
    SCHEMA,
    OverrideIndex,
    rel_posix,
    sha256_file,
)

REL = "PAPER_2pt/switches/SP6T.svg"


@pytest.fixture
def roots(tmp_path: Path):
    """A built-in tree and an empty overlay beside it."""
    builtin = tmp_path / "builtin"
    overlay = tmp_path / "overlay"
    (builtin / "PAPER_2pt" / "switches").mkdir(parents=True)
    (builtin / REL).write_text("<svg>shipped v1</svg>", encoding="utf-8")
    overlay.mkdir()
    return overlay, builtin


def fork(index: OverrideIndex, rel: str = REL, *, body: str = "<svg>mine</svg>"):
    """Write an overlay copy and record it, as saving an edit would."""
    target = index.overlay_path(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    index.record_fork(rel, origin=index.builtin_path(rel), app_version="0.12.1")
    return target


# ==========================================================================
# persistence
# ==========================================================================

def test_missing_index_is_the_normal_empty_case(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    assert idx.overrides == {} and idx.hidden == {} and idx.deleted == []
    assert not idx.read_only


def test_round_trip(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    idx.hide("builtin", REL, reason="superseded by my fork")
    idx.record_delete(rel="old.svg", trash=Path("/tmp/old.svg"))
    assert idx.save("0.12.1")

    again = OverrideIndex.load(overlay, builtin)
    assert again.is_fork(REL)
    assert again.is_hidden("builtin", REL)
    assert again.deleted[0]["rel"] == "old.svg"
    assert again.app_version == "0.12.1"


def test_save_is_atomic_and_leaves_no_temp_file(roots) -> None:
    """A half-applied move — a fork recorded but its tombstone lost — would
    show the component twice, so the whole index is written or none of it."""
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    assert idx.save()
    assert (overlay / INDEX_NAME).is_file()
    assert list(overlay.glob("*.tmp")) == []


def test_index_is_dot_prefixed_so_the_scanner_skips_it(roots) -> None:
    """ComponentLibrary.scan skips dot-prefixed parts, so the index can never
    be mistaken for a component."""
    assert INDEX_NAME.startswith(".")


def test_a_damaged_index_is_reported_and_treated_as_empty(roots, caplog) -> None:
    """The overlay's *files* are the real data. Refusing to start because the
    bookkeeping is corrupt would be the wrong trade."""
    overlay, builtin = roots
    (overlay / INDEX_NAME).write_text("{ not json", encoding="utf-8")
    idx = OverrideIndex.load(overlay, builtin)
    assert idx.overrides == {}
    assert not idx.read_only, "damaged is not the same as newer"


def test_a_newer_schema_loads_read_only_and_refuses_to_save(roots) -> None:
    """Saving our narrower understanding back would drop whatever that version
    recorded."""
    overlay, builtin = roots
    (overlay / INDEX_NAME).write_text(json.dumps({
        "schema": SCHEMA + 1,
        "overrides": {REL: {"kind": "fork", "something_new": 1}},
    }), encoding="utf-8")
    before = (overlay / INDEX_NAME).read_text(encoding="utf-8")

    idx = OverrideIndex.load(overlay, builtin)
    assert idx.read_only
    assert idx.is_fork(REL), "it still reads what it understands"
    assert idx.save() is False
    assert (overlay / INDEX_NAME).read_text(encoding="utf-8") == before


# ==========================================================================
# forks and drift
# ==========================================================================

def test_fork_records_the_builtin_it_came_from(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    rec = idx.overrides[REL]
    assert rec["kind"] == "fork"
    assert rec["origin_rel"] == REL
    assert rec["origin_sha256"] == sha256_file(builtin / REL)
    assert rec["forked_app_version"] == "0.12.1"


def test_no_drift_while_the_builtin_is_unchanged(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    assert idx.drift() == []


def test_drift_is_reported_when_the_builtin_changes(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    (builtin / REL).write_text("<svg>shipped v2</svg>", encoding="utf-8")

    drifted = idx.drift()
    assert [d.rel for d in drifted] == [REL]
    assert drifted[0].overlay_path == overlay / REL
    assert drifted[0].builtin_path == builtin / REL
    assert drifted[0].forked_app_version == "0.12.1"


def test_keep_mine_silences_drift_until_the_builtin_changes_again(roots) -> None:
    """The reason origin_sha256 and last_seen_origin_sha256 are separate: the
    first is the immutable fork point, the second is only a nag watermark."""
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    fork_point = idx.overrides[REL]["origin_sha256"]

    (builtin / REL).write_text("<svg>shipped v2</svg>", encoding="utf-8")
    assert idx.drift()

    idx.accept_upstream_change(REL)
    assert idx.drift() == []
    assert idx.overrides[REL]["upstream_decision"] == "keep_mine"
    assert idx.overrides[REL]["origin_sha256"] == fork_point, (
        "the fork point must not move; a future three-way compare needs it")

    (builtin / REL).write_text("<svg>shipped v3</svg>", encoding="utf-8")
    assert [d.rel for d in idx.drift()] == [REL]


def test_drift_survives_a_save_and_reload(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    (builtin / REL).write_text("<svg>v2</svg>", encoding="utf-8")
    idx.accept_upstream_change(REL)
    idx.save()
    assert OverrideIndex.load(overlay, builtin).drift() == []


def test_a_builtin_removed_upstream_is_not_reported_as_drift(roots) -> None:
    """It is not a conflict — there is nothing to compare against. A cleanup
    pass is the right place for it."""
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    (builtin / REL).unlink()
    assert idx.drift() == []


def test_fork_matches_builtin_detects_a_redundant_override(roots) -> None:
    """The main thing a junk scan looks for: an edit that was reverted, or an
    upstream that caught up."""
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx, body="<svg>mine</svg>")
    assert not idx.fork_matches_builtin(REL)

    idx.overlay_path(REL).write_text((builtin / REL).read_text(encoding="utf-8"),
                                     encoding="utf-8")
    assert idx.fork_matches_builtin(REL)


def test_forget_drops_the_record(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    fork(idx)
    idx.forget(REL)
    assert not idx.is_fork(REL)


# ==========================================================================
# tombstones
# ==========================================================================

def test_hide_unhide_is_reversible(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    assert not idx.is_hidden("builtin", REL)
    idx.hide("builtin", REL)
    assert idx.is_hidden("builtin", REL)
    assert idx.unhide("builtin", REL) is True
    assert not idx.is_hidden("builtin", REL)
    assert idx.unhide("builtin", REL) is False


def test_tombstone_scopes_do_not_collide(roots) -> None:
    """A user library lives anywhere, so only the built-in tree has a
    meaningful relative root — hence the namespaced keys."""
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    idx.hide("builtin", REL)
    idx.hide("custom", "/Users/joe/mylib/SP6T.svg")
    assert idx.hidden_builtin_rels() == {REL}
    assert idx.hidden_custom_paths() == {"/Users/joe/mylib/SP6T.svg"}
    assert not idx.is_hidden("custom", REL)


def test_hiding_a_builtin_never_touches_the_shipped_file(roots) -> None:
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    before = (builtin / REL).read_bytes()
    idx.hide("builtin", REL)
    idx.save()
    assert (builtin / REL).read_bytes() == before


def test_dead_tombstones_are_findable(roots) -> None:
    """They accumulate otherwise, which is the hoarding the user asked to
    avoid."""
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    idx.hide("builtin", REL)
    idx.hide("builtin", "PAPER_2pt/gone.svg")
    idx.hide("custom", str(overlay / "nope.svg"))
    assert idx.dead_tombstones() == [
        "builtin:PAPER_2pt/gone.svg", f"custom:{overlay / 'nope.svg'}"]


# ==========================================================================
# backups
# ==========================================================================

def test_backup_lands_in_a_dot_prefixed_folder(roots) -> None:
    """Dot-prefixed so the scanner ignores it — a backup must never show up in
    the Library panel as a second copy of the component."""
    overlay, builtin = roots
    idx = OverrideIndex.load(overlay, builtin)
    target = fork(idx)
    backup = idx.backup(target)
    assert backup is not None and backup.is_file()
    assert backup.read_text(encoding="utf-8") == "<svg>mine</svg>"
    assert ".backups" in backup.parts
    assert any(part.startswith(".") for part in backup.relative_to(overlay).parts)


# ==========================================================================
# helpers
# ==========================================================================

def test_rel_posix_is_machine_independent(tmp_path: Path) -> None:
    root = tmp_path / "components"
    (root / "a" / "b").mkdir(parents=True)
    f = root / "a" / "b" / "c.svg"
    f.write_text("x", encoding="utf-8")
    assert rel_posix(f, root) == "a/b/c.svg"
    assert rel_posix(tmp_path / "elsewhere.svg", root) is None
