"""HelpWindow must find its docs through the *package* path alone.

A wheel ships only what is under ``src/diagrammer/``. ``_find_help_file``
also falls back to the repo-root ``docs/`` for dev checkouts, which masked
the fact that ``tutorial.md`` was never packaged: Help ▸ Tutorial worked in
an editable install and showed "Document not found" from a wheel. These
tests resolve the package's docs directory the way an installed copy would
and require that the doc (and every image it references) is found *there*.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

import pytest

from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument

from diagrammer.panels.help_window import HelpWindow

DOCS = ["help.md", "tutorial.md"]

# Markdown images, ![alt](target) — skip URLs and anchors.
_IMG = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")


def _package_docs_dir() -> Path:
    return Path(str(files("diagrammer") / "docs")).resolve()


def _local_images(md_text: str) -> list[str]:
    return [t for t in _IMG.findall(md_text)
            if "://" not in t and not t.startswith("#")]


@pytest.mark.parametrize("filename", DOCS)
def test_doc_found_in_package_docs(filename):
    pkg_doc = _package_docs_dir() / filename
    assert pkg_doc.is_file(), (
        f"{filename} is not under the package's docs/ directory, so a "
        f"non-editable install will not contain it")

    win = HelpWindow(filename=filename, show_shortcuts=False)
    try:
        found = win._find_help_file()
        assert found is not None
        # Must be the package copy, not the repo-root dev fall-back.
        assert found.resolve() == pkg_doc
        assert not win._browser.toPlainText().startswith("Document not found")
    finally:
        win.deleteLater()


@pytest.mark.parametrize("filename", DOCS)
def test_doc_images_resolve_from_package_docs(filename):
    pkg_doc = _package_docs_dir() / filename
    images = _local_images(pkg_doc.read_text(encoding="utf-8"))

    win = HelpWindow(filename=filename, show_shortcuts=False)
    try:
        for target in images:
            assert (pkg_doc.parent / target).is_file(), (
                f"{filename} references {target}, which is not next to it")
            # The same lookup QTextBrowser does when rendering ![](target).
            res = win._browser.loadResource(
                QTextDocument.ResourceType.ImageResource, QUrl(target))
            assert res is not None and not getattr(res, "isNull", lambda: False)(), (
                f"HelpWindow could not load image {target} for {filename}")
    finally:
        win.deleteLater()


def test_tutorial_references_images():
    # Guard the guard: if the regex stopped matching, the image test above
    # would pass vacuously.
    text = (_package_docs_dir() / "tutorial.md").read_text(encoding="utf-8")
    assert len(_local_images(text)) >= 4
