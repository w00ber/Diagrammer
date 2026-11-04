"""The fixes: each one's effect, plus the invariants the preview loop relies on.

The invariants are the point of this file. The inspector recomputes the whole
candidate from the pristine original on every checkbox toggle, so:

* no fixes selected must give back the original text, byte for byte;
* applying the same *set* in two different orders must give identical bytes;
* applying a fix twice must be the same as applying it once.

If any of those break, unticking a box stops being exact and the preview stops
describing what Save would write.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from diagrammer import svglint
from diagrammer.svglint import profiles
from diagrammer.svglint.fixes import FIXES_BY_ID

PAPER = profiles.BUILTIN_PROFILES["PAPER_2pt"]
SLIDES = profiles.BUILTIN_PROFILES["SLIDES_3pt"]


def svg(body: str, *, viewbox: str = "0 0 60 100", style: str | None = None) -> str:
    css = style if style is not None else (
        "    <style>\n"
        "      .st0 { fill: #c66; }\n"
        "      .st1 { fill: none; stroke: #000; stroke-width: 2px; }\n"
        "    </style>\n"
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">\n'
            "  <defs>\n" + css + "  </defs>\n" + body + "</svg>\n")


SOUND = svg(
    '  <g id="artwork">\n'
    '    <path class="st2" d="M54.6,54.6H5.4"/>\n'
    "  </g>\n"
    '  <g id="leads">\n'
    '    <line class="st1" x1="30" y1="0" x2="30" y2="45"/>\n'
    "  </g>\n"
    '  <g id="ports">\n'
    '    <circle id="port:top" class="st0" cx="30" cy="0" r="3"/>\n'
    "  </g>\n",
    style=("    <style>\n"
           "      .st0 { fill: #c66; }\n"
           "      .st1 { fill: none; stroke: #000; stroke-width: 2px; }\n"
           "      .st2 { fill: none; stroke: #000; stroke-width: 2px; }\n"
           "    </style>\n"),
)


def run(text: str, fix_ids, path: Path, profile=PAPER):
    findings = svglint.check_text(text, path=path, profile=profile)
    chosen = [FIXES_BY_ID[i] for i in fix_ids]
    return svglint.apply_fixes(text, chosen, findings,
                               profile=profile, path=path)


# ==========================================================================
# invariants
# ==========================================================================

def test_no_fixes_selected_returns_the_original_bytes(tmp_path: Path) -> None:
    """Unticking everything must be a true no-op, not a reformat."""
    path = tmp_path / "p.svg"
    out, notes = run(SOUND, [], path)
    assert out == SOUND
    assert notes == []


def test_click_order_does_not_affect_the_result(tmp_path: Path) -> None:
    """apply_fixes sorts by (order, fix_id), which is a total sort — so the
    candidate is a function of the selected *set*, not of the click sequence."""
    path = tmp_path / "mangled.svg"
    text = svg(
        '  <g id="Layer_1">\n'
        '    <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '    <g id="leads"><line class="st1" x1="30" y1="0" x2="30" y2="45"/></g>\n'
        '    <g id="ports">\n'
        '      <circle id="port_top" data-name="port:top" cx="30" cy="0" r="3"/>\n'
        "    </g>\n"
        '    <path class="st1" d="M40.1,24.1"/>\n'
        "  </g>\n")
    ids = ["unwrap-layer-group", "restore-named-ids", "strip-stray-anchors"]
    results = {run(text, list(perm), path)[0] for perm in itertools.permutations(ids)}
    assert len(results) == 1, "fix application is order-dependent"


@pytest.mark.parametrize("fix_id", sorted(FIXES_BY_ID))
def test_every_fix_is_idempotent(fix_id: str, tmp_path: Path) -> None:
    """Applying a fix to its own output must change nothing further.

    A fix that is not idempotent would make the preview drift on re-check and
    would corrupt the batch report's "fix in all N files" pass.
    """
    path = tmp_path / "f.svg"
    text = svg(
        '  <g id="Layer_1">\n'
        '    <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
        '    <g id="leads"><line class="st1" x1="30" y1="40" x2="30" y2="20"/></g>\n'
        '    <g id="ports">\n'
        '      <circle id="port_top" data-name="port:top" cx="30" cy="0" r="3"/>\n'
        "    </g>\n"
        '    <path class="st1" d="M40.1,24.1"/>\n'
        "  </g>\n",
        viewbox="-3 -3 60 100")
    once, _ = run(text, [fix_id], path)
    twice, _ = run(once, [fix_id], path)
    assert twice == once


# ==========================================================================
# individual fixes
# ==========================================================================

def test_unwrap_layer_group(tmp_path: Path) -> None:
    text = svg('  <g id="Layer_1">\n'
               '    <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
               '    <g id="ports"><circle id="port:a" cx="0" cy="50" r="3"/></g>\n'
               "  </g>\n")
    out, notes = run(text, ["unwrap-layer-group"], tmp_path / "w.svg")
    assert 'id="Layer_1"' not in out
    assert '  <g id="artwork">' in out, "layers should be dedented one level"
    assert notes == ['unwrapped <g id="Layer_1">']


def test_unwrap_leaves_a_genuine_artwork_layer_alone(tmp_path: Path) -> None:
    """A Layer_1 that holds no named layers is the author's own group."""
    text = svg('  <g id="Layer_1"><path class="st1" d="M0,50H60"/></g>\n'
               '  <g id="ports"><circle id="port:a" cx="0" cy="50" r="3"/></g>\n')
    out, _ = run(text, ["unwrap-layer-group"], tmp_path / "w.svg")
    assert out == text


def test_restore_named_ids(tmp_path: Path) -> None:
    text = svg('  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
               '  <g id="ports">\n'
               '    <circle id="port_left" data-name="port:left" cx="0" cy="50" r="3"/>\n'
               "  </g>\n")
    out, notes = run(text, ["restore-named-ids"], tmp_path / "i.svg")
    assert 'id="port:left"' in out
    assert "data-name" not in out
    assert notes == ['restored id="port:left"']


def test_restore_named_ids_refuses_an_illustrator_dedup(tmp_path: Path) -> None:
    """The guard that matters: Illustrator renames a duplicate id and records
    the original in data-name. Restoring it would collide and lose a port."""
    text = svg('  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
               '  <g id="ports">\n'
               '    <circle id="port:2" cx="0" cy="50" r="3"/>\n'
               '    <circle id="port:21" data-name="port:2" cx="60" cy="50" r="3"/>\n'
               "  </g>\n")
    out, _ = run(text, ["restore-named-ids"], tmp_path / "d.svg")
    assert out == text, "an id de-dup must not be treated as sanitization"


def test_restore_primitives_converts_a_path_circle_back(tmp_path: Path) -> None:
    circle = "M33,0c0,1.7-1.3,3-3,3s-3-1.3-3-3,1.3-3,3-3,3,1.3,3,3Z"
    text = svg('  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
               f'  <g id="ports"><path id="port:top" class="st0" d="{circle}"/></g>\n')
    out, notes = run(text, ["restore-primitives"], tmp_path / "p.svg")
    assert '<circle id="port:top" class="st0" cx="30" cy="0" r="3"/>' in out
    assert any("path -> <circle>" in n for n in notes)
    # and the parser now sees the port, which is the whole point
    from diagrammer.models.component_def import ComponentDef
    comp = ComponentDef.from_svg_text(out, path=tmp_path / "p.svg")
    assert [p.name for p in comp.ports] == ["top"]


def test_set_lead_width_rewrites_the_lead_class_only(tmp_path: Path) -> None:
    out, notes = run(SOUND, ["set-lead-width"], tmp_path / "l.svg",
                     profile=SLIDES)
    assert "stroke-width: 3px" in out
    assert any("st1" in n for n in notes)
    # .st2 is the artwork's class and must still resolve to 2px
    from diagrammer.svglint import text_svg as T
    widths = T.css_widths(out)
    assert widths["st1"] == 3.0
    assert widths["st2"] == 2.0


def test_set_lead_width_splits_a_class_shared_with_the_artwork(
        tmp_path: Path) -> None:
    """Many shipped parts use one class for both body and leads, so the width
    fix has to split it just as the cap fix does — the user should not have to
    learn which fixes can cope with a shared class and which give up."""
    from diagrammer.svglint import text_svg as T

    shared = svg('  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
                 '  <g id="leads"><line class="st1" x1="30" y1="0" x2="30" y2="45"/></g>\n'
                 '  <g id="ports"><circle id="port:top" cx="30" cy="0" r="3"/></g>\n')
    out, notes = run(shared, ["set-lead-width"], tmp_path / "s.svg",
                     profile=SLIDES)
    assert out != shared
    widths = T.css_widths(out)
    assert widths["st1"] == 2.0, "the artwork keeps its weight"
    assert widths["st1lead"] == 3.0, "the leads get the library's weight"
    assert any("own class" in n for n in notes)


def test_set_lead_linecap_butt(tmp_path: Path) -> None:
    text = SOUND.replace("stroke-width: 2px; }\n      .st2",
                         "stroke-width: 2px; stroke-linecap: round; }\n      .st2")
    out, notes = run(text, ["set-lead-linecap-butt"], tmp_path / "c.svg")
    from diagrammer.svglint import text_svg as T
    assert T.css_property(out, "stroke-linecap")["st1"] == "butt"
    assert any("stroke-linecap round -> butt" in n for n in notes)


GROUPED = svg(
    '  <g id="artwork">\n'
    '    <circle class="st2" cx="30" cy="50" r="20"/>\n'
    '    <path class="st3" d="M10,10L20,20"/>\n'
    "  </g>\n"
    '  <g id="leads">\n'
    '    <line class="st7" x1="30" y1="0" x2="30" y2="30"/>\n'
    "  </g>\n"
    '  <g id="ports"><circle id="port:top" cx="30" cy="0" r="3"/></g>\n',
    # Illustrator's real output shape: one grouped rule governing the lead
    # class *and* half the artwork.
    style=("    <style>\n"
           "      .st2, .st3, .st7 {\n"
           "        stroke: #000;\n"
           "        stroke-linecap: round;\n"
           "        stroke-width: 2px;\n"
           "      }\n"
           "    </style>\n"))


def test_lead_cap_fix_does_not_restyle_artwork_sharing_the_rule(
        tmp_path: Path) -> None:
    """The bug this guards: Illustrator writes grouped selectors, so the rule
    that sets the lead's cap usually sets half the symbol's too. Editing it in
    place silently rounded-off the whole drawing."""
    from diagrammer.svglint import text_svg as T

    out, notes = run(GROUPED, ["set-lead-linecap-butt"], tmp_path / "g.svg")
    caps = T.css_property(out, "stroke-linecap")
    assert caps["st7"] == "butt", "the lead must change"
    assert caps["st2"] == "round", "the circle must not"
    assert caps["st3"] == "round", "nor the path"
    assert any("shared with" in n for n in notes), (
        "the user should be told why a rule was added rather than edited")


def test_lead_width_fix_does_not_restyle_artwork_sharing_the_rule(
        tmp_path: Path) -> None:
    from diagrammer.svglint import text_svg as T

    out, _ = run(GROUPED, ["set-lead-width"], tmp_path / "g.svg",
                 profile=SLIDES)
    widths = T.css_widths(out)
    assert widths["st7"] == 3.0
    assert widths["st2"] == 2.0 and widths["st3"] == 2.0


def test_artwork_cap_is_a_separate_opt_in_fix(tmp_path: Path) -> None:
    """Taste, not correctness — so it is its own checkbox and never pre-ticked."""
    from diagrammer import svglint
    from diagrammer.svglint import text_svg as T

    path = tmp_path / "g.svg"
    findings = svglint.check_text(GROUPED, path=path, profile=PAPER)
    ids = {f.check_id for f in findings}
    assert "lead-linecap-not-butt" in ids
    assert "artwork-linecap-not-house-style" in ids

    art = next(f for f in findings
               if f.check_id == "artwork-linecap-not-house-style")
    assert art.severity == "info", "artwork caps are reported, not flagged"
    assert "set-artwork-linecap-butt" not in {
        f.fix_id for f in svglint.default_fixes(findings)}

    out, _ = run(GROUPED, ["set-lead-linecap-butt",
                           "set-artwork-linecap-butt"], path)
    caps = T.css_property(out, "stroke-linecap")
    assert caps["st7"] == caps["st2"] == caps["st3"] == "butt"


def test_rebase_viewbox_moves_content_and_zeroes_the_origin(tmp_path: Path) -> None:
    """The rendered symbol must be unchanged: frame and content move together."""
    text = svg('  <g id="artwork"><path class="st1" d="M-3,47H57"/></g>\n'
               '  <g id="ports"><circle id="port:left" cx="-3" cy="47" r="3"/></g>\n',
               viewbox="-3 -3 60 100")
    path = tmp_path / "v.svg"
    out, notes = run(text, ["rebase-viewbox-to-origin"], path)
    assert 'viewBox="0 0 60 100"' in out
    assert 'cx="0" cy="50"' in out
    assert any("-> (0,0)" in n for n in notes)
    # the port is now on the left edge, so approach detection works again
    from diagrammer.models.component_def import ComponentDef
    port = ComponentDef.from_svg_text(out, path=path).ports[0]
    assert (port.x, port.y) == (0.0, 50.0)
    assert (port.approach_dx, port.approach_dy) == (-1.0, 0.0)


def test_snap_lead_end_extends_the_lead_without_moving_its_far_end(tmp_path: Path) -> None:
    text = svg('  <g id="artwork"><path class="st1" d="M0,50H60"/></g>\n'
               '  <g id="leads"><line class="st1" x1="30" y1="40" x2="30" y2="20"/></g>\n'
               '  <g id="ports"><circle id="port:top" cx="30" cy="0" r="3"/></g>\n')
    path = tmp_path / "n.svg"
    out, notes = run(text, ["snap-lead-end-to-port"], path)
    assert 'y1="40"' in out, "the far end must not move"
    assert 'y2="0"' in out, "the near end should reach the port"
    assert any("-> (30,0)" in n for n in notes)
    assert svglint.check_text(out, path=path, profile=PAPER) == []


def test_strip_stray_anchors_spares_anything_with_an_id(tmp_path: Path) -> None:
    text = svg('  <g id="artwork">\n'
               '    <path class="st1" d="M0,50H60"/>\n'
               '    <path class="st1" d="M40.1,24.1"/>\n'
               "  </g>\n"
               '  <g id="ports"><path id="port:x" d="M10,10"/></g>\n')
    out, notes = run(text, ["strip-stray-anchors"], tmp_path / "a.svg")
    assert 'd="M40.1,24.1"' not in out
    assert 'id="port:x"' in out, "an id-bearing path is addressed by id"
    assert notes == ["removed 1 stray anchor point(s)"]


def test_strip_editor_metadata_drops_the_blob_and_its_namespace(tmp_path: Path) -> None:
    text = ('<?xml version="1.0"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:i="http://ns.adobe.com/AI"'
            ' viewBox="0 0 60 100">\n'
            "  <metadata>\n    <i:aipgf>AAAA</i:aipgf>\n  </metadata>\n"
            '  <g id="ports"><circle id="port:a" cx="0" cy="50" r="3"/></g>\n'
            "</svg>\n")
    out, notes = run(text, ["strip-editor-metadata"], tmp_path / "m.svg")
    assert "aipgf" not in out and "<metadata>" not in out
    assert "xmlns:i" not in out, "the i: prefix has no other user"
    assert any("PGF metadata" in n for n in notes)


# ==========================================================================
# the loop the inspector runs
# ==========================================================================

def test_default_fixes_clear_a_mangled_file(tmp_path: Path) -> None:
    """End to end: the pre-ticked fixes alone should make an editor-mangled
    component parse and validate clean."""
    path = tmp_path / "mangled.svg"
    text = svg(
        '  <g id="Layer_1">\n'
        '    <g id="artwork"><path class="st2" d="M0,50H60"/></g>\n'
        '    <g id="leads"><line class="st1" x1="30" y1="0" x2="30" y2="45"/></g>\n'
        '    <g id="ports">\n'
        '      <circle id="port_top" data-name="port:top" cx="30" cy="0" r="3"/>\n'
        "    </g>\n"
        '    <path class="st2" d="M40.1,24.1"/>\n'
        "  </g>\n",
        style=("    <style>\n"
               "      .st1 { fill: none; stroke: #000; stroke-width: 2px; }\n"
               "      .st2 { fill: none; stroke: #000; stroke-width: 2px; }\n"
               "    </style>\n"))
    path.write_text(text, encoding="utf-8")

    findings = svglint.check_file(path, profile=PAPER)
    assert {f.check_id for f in findings} >= {
        "layer-wrapper", "mangled-named-id", "stray-anchor-point"}

    chosen = svglint.default_fixes(findings)
    candidate, _ = svglint.apply_fixes(text, chosen, findings,
                                       profile=PAPER, path=path)
    assert svglint.check_text(candidate, path=path, profile=PAPER) == []
    assert path.read_text(encoding="utf-8") == text, "the file must not be written"


# ==========================================================================
# a class shared between artwork and leads
# ==========================================================================

#: The commonest shape in the shipped library (CAP1 is exactly this): one class
#: styles both the body and the leads, so there is no way to change the lead's
#: cap without changing the body's.
SHARED_CLASS = svg(
    '  <g id="artwork">\n'
    '    <path class="st1" d="M54.6,54.6H5.4"/>\n'
    '    <path class="st1" d="M54.6,45.4H5.4"/>\n'
    "  </g>\n"
    '  <g id="leads">\n'
    '    <path class="st1" d="M30,0v45.4"/>\n'
    '    <path class="st1" d="M30,100v-44.9"/>\n'
    "  </g>\n"
    '  <g id="ports">\n'
    '    <circle id="port:top" class="st0" cx="30" cy="0" r="3"/>\n'
    '    <circle id="port:bottom" class="st0" cx="30" cy="100" r="3"/>\n'
    "  </g>\n",
    style=("    <style>\n"
           "      .st0 { fill: #c66; }\n"
           "      .st1 {\n"
           "        fill: none;\n"
           "        stroke: #000;\n"
           "        stroke-linecap: round;\n"
           "        stroke-linejoin: bevel;\n"
           "        stroke-width: 2px;\n"
           "      }\n"
           "    </style>\n"))


def test_a_shared_class_is_split_rather_than_refused(tmp_path: Path) -> None:
    """Refusing was correct but useless: it left the fix inapplicable to half
    the library and told the user to go and split the class by hand."""
    from diagrammer.svglint import text_svg as T

    out, notes = run(SHARED_CLASS, ["set-lead-linecap-butt"], tmp_path / "c.svg")
    assert out != SHARED_CLASS, "the fix must do something"
    caps = T.css_property(out, "stroke-linecap")
    assert caps["st1"] == "round", "the artwork keeps its caps"
    assert caps["st1lead"] == "butt", "the leads get their own class"
    assert any("own class" in n for n in notes)


def test_the_split_class_carries_everything_the_original_gave(
        tmp_path: Path) -> None:
    """Otherwise the lead loses its stroke colour and width."""
    from diagrammer.svglint import text_svg as T

    out, _ = run(SHARED_CLASS, ["set-lead-linecap-butt"], tmp_path / "c.svg")
    decls = T.class_declarations(out, "st1lead")
    assert decls == {
        "fill": "none", "stroke": "#000", "stroke-linecap": "butt",
        "stroke-linejoin": "bevel", "stroke-width": "2px",
    }
    assert T.lead_width(out) == 2.0, "the attached wire can still size itself"


def test_only_the_lead_elements_are_repointed(tmp_path: Path) -> None:
    import re

    out, _ = run(SHARED_CLASS, ["set-lead-linecap-butt"], tmp_path / "c.svg")
    artwork = re.search(r'<g id="artwork">.*?</g>', out, re.S).group(0)
    leads = re.search(r'<g id="leads">.*?</g>', out, re.S).group(0)
    assert artwork.count('class="st1"') == 2
    assert "st1lead" not in artwork
    assert leads.count('class="st1lead"') == 2
    assert 'class="st1"' not in leads


def test_the_generated_class_name_has_no_hyphen(tmp_path: Path) -> None:
    """A hyphen is legal CSS, but several of the app's own readers match
    ``\\.(\\w+)``, which stops at it — ".st1-lead" would be recorded as "st1",
    clobbering the real class and then failing to resolve for the element."""
    import re

    out, _ = run(SHARED_CLASS, ["set-lead-linecap-butt"], tmp_path / "c.svg")
    new = [c for c in re.findall(r"\.([\w-]+)\s*\{", out) if c not in
           ("st0", "st1")]
    assert new and all("-" not in c for c in new), new
    # and the app's narrower regex resolves it to the same set
    narrow = {m.group(1) for m in re.finditer(r"\.(\w+)\s*\{", out)}
    wide = {m.group(1) for m in re.finditer(r"\.([\w-]+)\s*\{", out)}
    assert narrow == wide


def test_splitting_is_idempotent(tmp_path: Path) -> None:
    """A second pass must not invent .st1leadlead."""
    once, _ = run(SHARED_CLASS, ["set-lead-linecap-butt"], tmp_path / "c.svg")
    twice, _ = run(once, ["set-lead-linecap-butt"], tmp_path / "c.svg")
    assert twice == once


def test_the_split_resolves_the_finding(tmp_path: Path) -> None:
    from diagrammer import svglint

    path = tmp_path / "c.svg"
    out, _ = run(SHARED_CLASS, ["set-lead-linecap-butt"], path)
    remaining = {f.check_id for f in svglint.check_text(out, path=path,
                                                        profile=PAPER)}
    assert "lead-linecap-not-butt" not in remaining
    # the artwork's own caps are still reported, as the separate info finding
    assert "artwork-linecap-not-house-style" in remaining
