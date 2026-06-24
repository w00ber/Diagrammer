"""Tolerant SVG parsing.

Python's ``xml.etree.ElementTree`` (expat) is strict and rejects any element
carrying two attributes of the same name, e.g. a root ``<svg>`` with a duplicate
``xmlns="http://www.w3.org/2000/svg"``. Such files are produced by some external
editors and by Diagrammer's own pre-2026-05 compound exporter (the writer bug was
fixed in commit 3db082c). libxml2-based tools (Inkscape, Illustrator, browsers)
accept them, so they look fine everywhere except here.

``parse_svg`` is a drop-in replacement for ``ET.parse`` that, on a duplicate-
attribute ``ParseError``, repairs the file in place (de-duplicating attributes
while preserving everything else) and re-parses. Use it for every SVG read so one
malformed file can't break a library scan.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches the root start tag: ``<svg ...>`` (or ``<svg.../>``). Non-greedy up to
# the first ``>``; attribute values can't contain ``>`` unquoted in valid-ish SVG.
_ROOT_SVG_RE = re.compile(r"<svg\b[^>]*?/?>", re.DOTALL)
# Matches one ``name="value"`` / ``name='value'`` / bare ``name`` attribute.
_ATTR_RE = re.compile(r"""([^\s=/>]+)\s*(=\s*("[^"]*"|'[^']*'))?""")


def _dedupe_attrs_in_tag(tag: str) -> tuple[str, list[str]]:
    """Drop duplicate attributes from a single start tag, keeping the first of
    each name. Returns ``(rebuilt_tag, removed_attr_names)``."""
    m = re.match(r"(<\s*[^\s/>]+)(.*?)(/?>)\s*$", tag, re.DOTALL)
    if not m:
        return tag, []
    head, body, close = m.group(1), m.group(2), m.group(3)

    seen: set[str] = set()
    removed: list[str] = []
    kept: list[str] = []
    for am in _ATTR_RE.finditer(body):
        name = am.group(1)
        if not name:
            continue
        if name in seen:
            removed.append(name)
            continue
        seen.add(name)
        kept.append(am.group(0).strip())

    if not removed:
        return tag, []
    rebuilt = head + (" " + " ".join(kept) if kept else "") + close
    return rebuilt, removed


def normalize_svg_text(text: str) -> tuple[str, bool]:
    """Remove duplicate attributes from the root ``<svg>`` start tag.

    Keeps the first occurrence of each attribute name and preserves everything
    else verbatim (metadata namespaces such as ``dc``/``cc``/``rdf``, child
    elements, formatting). Returns ``(clean_text, changed)``.
    """
    m = _ROOT_SVG_RE.search(text)
    if not m:
        return text, False
    rebuilt, removed = _dedupe_attrs_in_tag(m.group(0))
    if not removed:
        return text, False
    clean = text[: m.start()] + rebuilt + text[m.end():]
    return clean, True


def repair_svg_file(path: Path | str) -> bool:
    """Repair a malformed SVG on disk in place. Returns whether it was changed.

    Only rewrites the file when an actual duplicate attribute is removed, so
    well-formed files are never touched.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    clean, changed = normalize_svg_text(text)
    if changed:
        path.write_text(clean, encoding="utf-8")
    return changed


def parse_svg(path: Path | str) -> ET.ElementTree:
    """Parse an SVG file, auto-repairing a duplicate-attribute file in place.

    Drop-in replacement for ``ET.parse``. On a ``ParseError`` we attempt a
    repair; if it removed a duplicate attribute we re-parse and return the tree
    (logging a warning so the rewrite isn't silent). If the repair changed
    nothing, the original error is re-raised — we don't mask genuinely broken XML.
    """
    path = Path(path)
    try:
        return ET.parse(str(path))
    except ET.ParseError:
        if repair_svg_file(path):
            logger.warning("Auto-repaired malformed SVG (removed duplicate attributes): %s", path)
            return ET.parse(str(path))
        raise
