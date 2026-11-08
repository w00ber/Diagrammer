"""The Settings editor for per-library conventions.

The design point under test is that only *changed* fields are stored. Writing a
whole profile back would freeze the shipped defaults, so a later correction to
one — a measured corner radius, say — would never reach anyone who had opened
this dialog once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagrammer import svglint
from diagrammer.models.library import ComponentLibrary
from diagrammer.svglint import profiles
from diagrammer.panels.settings_dialog import SettingsDialog, app_settings


@pytest.fixture
def dlg(_ensure_qapp, isolate_app_settings):
    d = SettingsDialog(app_settings, library=ComponentLibrary())
    yield d
    d.deleteLater()


def test_every_shipped_library_has_a_row(dlg) -> None:
    assert set(dlg._profile_edits) == set(profiles.BUILTIN_PROFILES)


def test_rows_open_on_the_measured_defaults(dlg) -> None:
    paper = dlg._profile_edits["PAPER_2pt"]
    assert paper["grid"].value() == 20.0
    assert paper["lead_width"].text() == "2"
    assert paper["corner_radius"].text() == "4"
    assert paper["linecap"].currentText() == "butt"
    cryo = dlg._profile_edits["CRYO_WIRING_6pt"]
    assert cryo["lead_width"].text() == "6"
    assert cryo["corner_radius"].text() == "1.9", (
        "measured from the shipped artwork, not guessed")


def test_a_library_with_no_convention_shows_a_blank(dlg) -> None:
    """Blank means "no convention", which disables the check — not zero."""
    for name in ("FLAIR", "flowchart"):
        assert dlg._profile_edits[name]["lead_width"].text() == ""
        assert dlg._profile_edits[name]["corner_radius"].text() == ""


def test_untouched_editor_stores_nothing(dlg) -> None:
    assert dlg._collect_library_profiles() == {}


def test_only_the_changed_fields_are_stored(dlg) -> None:
    w = dlg._profile_edits["SLIDES_3pt"]
    w["lead_width"].setText("4")
    assert dlg._collect_library_profiles() == {"SLIDES_3pt": {"lead_width": 4.0}}


def test_blanking_a_field_is_stored_as_an_explicit_none(dlg) -> None:
    """That is how a user says "this library has no fixed lead weight"."""
    dlg._profile_edits["PAPER_2pt"]["lead_width"].setText("")
    assert dlg._collect_library_profiles() == {"PAPER_2pt": {"lead_width": None}}


def test_rubbish_in_a_field_keeps_the_default(dlg) -> None:
    dlg._profile_edits["PAPER_2pt"]["lead_width"].setText("two and a bit")
    assert dlg._collect_library_profiles() == {}


def test_apply_round_trips_through_settings_and_reaches_the_validator(
        dlg) -> None:
    dlg._profile_edits["PAPER_2pt"]["grid"].setValue(10.0)
    dlg._profile_edits["PAPER_2pt"]["lead_width"].setText("2.5")
    dlg.apply()
    assert app_settings.library_profiles == {
        "PAPER_2pt": {"grid": 10.0, "lead_width": 2.5}}

    prof = svglint.profile_for(Path("PAPER_2pt") / "x.svg",
                               overrides=app_settings.library_profiles)
    assert prof.grid == 10.0
    assert prof.lead_width == 2.5
    assert prof.source == "settings"
    assert prof.linecap == "butt", "untouched fields keep the shipped default"


def test_settings_survive_a_save_and_load(dlg, tmp_path, monkeypatch) -> None:
    from diagrammer.panels import settings_dialog as sd

    dlg._profile_edits["FLAIR"]["linecap"].setCurrentText("round")
    dlg.apply()
    app_settings.save()
    saved = json.loads(sd._SETTINGS_FILE.read_text(encoding="utf-8"))
    assert saved["library_profiles"] == {"FLAIR": {"linecap": "round"}}

    app_settings.library_profiles = {}
    app_settings.load()
    assert app_settings.library_profiles == {"FLAIR": {"linecap": "round"}}


def test_reset_clears_the_overrides(dlg) -> None:
    dlg._profile_edits["PAPER_2pt"]["grid"].setValue(5.0)
    assert dlg._collect_library_profiles()
    dlg._reset_library_profiles()
    assert dlg._collect_library_profiles() == {}


def test_a_malformed_stored_profile_does_not_break_loading(
        isolate_app_settings, tmp_path) -> None:
    from diagrammer.panels import settings_dialog as sd

    sd._SETTINGS_FILE.write_text(json.dumps({
        "library_profiles": {"PAPER_2pt": "not a dict", "FLAIR": {"grid": 7}}
    }), encoding="utf-8")
    app_settings.load()
    assert app_settings.library_profiles == {"FLAIR": {"grid": 7}}


def test_a_sidecar_still_beats_the_settings_editor(dlg, tmp_path) -> None:
    """The sidecar travels with the folder; this editor is per-machine."""
    dlg._profile_edits["PAPER_2pt"]["lead_width"].setText("5")
    dlg.apply()
    lib = tmp_path / "PAPER_2pt"
    lib.mkdir()
    (lib / profiles.SIDECAR_NAME).write_text(
        json.dumps({"lead_width": 9}), encoding="utf-8")
    prof = svglint.profile_for(lib / "p.svg",
                               overrides=app_settings.library_profiles)
    assert prof.lead_width == 9.0
    assert prof.source == "library.json"
