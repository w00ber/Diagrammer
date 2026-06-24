"""Tests for tolerant SVG parsing / repair (diagrammer.io.svg_parse)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from diagrammer.io.svg_parse import normalize_svg_text, parse_svg, repair_svg_file

# A root <svg> with a duplicate xmlns plus Inkscape-style metadata namespaces,
# matching the real-world offending files.
MALFORMED = (
    "<?xml version='1.0' encoding='utf-8'?>\n"
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:ns2="http://creativecommons.org/ns#" '
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">\n'
    '  <g id="artwork"><rect width="10" height="10"/></g>\n'
    "</svg>\n"
)

WELL_FORMED = (
    "<?xml version='1.0' encoding='utf-8'?>\n"
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">\n'
    '  <g id="artwork"><rect width="10" height="10"/></g>\n'
    "</svg>\n"
)


def test_strict_parser_rejects_malformed():
    """Sanity check: the bug we're fixing really does break stock ET."""
    with pytest.raises(ET.ParseError):
        ET.fromstring(MALFORMED)


def test_normalize_removes_duplicate_xmlns():
    clean, changed = normalize_svg_text(MALFORMED)
    assert changed is True
    # Exactly one default svg xmlns survives.
    assert clean.count('xmlns="http://www.w3.org/2000/svg"') == 1
    # Metadata namespaces are preserved.
    assert "xmlns:dc=" in clean
    assert "xmlns:ns2=" in clean
    assert "xmlns:rdf=" in clean
    # And the result parses.
    ET.fromstring(clean)


def test_normalize_leaves_clean_file_untouched():
    clean, changed = normalize_svg_text(WELL_FORMED)
    assert changed is False
    assert clean == WELL_FORMED


def test_parse_svg_repairs_in_place(tmp_path):
    p = tmp_path / "bad.svg"
    p.write_text(MALFORMED, encoding="utf-8")

    tree = parse_svg(p)  # must not raise
    root = tree.getroot()
    assert root.get("viewBox") == "0 0 100 50"

    # File was rewritten clean on disk.
    after = p.read_text(encoding="utf-8")
    assert after.count('xmlns="http://www.w3.org/2000/svg"') == 1
    ET.fromstring(after)


def test_parse_svg_leaves_good_file_untouched(tmp_path):
    p = tmp_path / "good.svg"
    p.write_text(WELL_FORMED, encoding="utf-8")
    parse_svg(p)
    assert p.read_text(encoding="utf-8") == WELL_FORMED


def test_repair_svg_file_returns_changed_flag(tmp_path):
    bad = tmp_path / "bad.svg"
    bad.write_text(MALFORMED, encoding="utf-8")
    assert repair_svg_file(bad) is True
    # Idempotent: second pass changes nothing.
    assert repair_svg_file(bad) is False


def test_parse_svg_reraises_genuinely_broken_xml(tmp_path):
    """A file broken in a way repair can't fix must still raise (not be masked)."""
    p = tmp_path / "broken.svg"
    p.write_text("<svg><g></svg>", encoding="utf-8")  # mismatched tags
    with pytest.raises(ET.ParseError):
        parse_svg(p)
