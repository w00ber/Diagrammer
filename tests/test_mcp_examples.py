"""The worked examples in docs/mcp.md must still open.

docs/mcp-examples/ ships .dgm files that readers are told to open. If the file
format or a component key changes underneath them, the docs break silently;
this catches it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from diagrammer.headless import Diagram

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "mcp-examples"
DGMS = sorted(EXAMPLES.glob("*.dgm"))


def test_examples_exist():
    assert len(DGMS) >= 2


@pytest.mark.parametrize("path", DGMS, ids=lambda p: p.name)
def test_example_opens_and_renders(path):
    d = Diagram()
    d.load(path)
    s = d.summary()
    assert s["components"] and s["wires"]
    assert d.render_png(dpi=48)[:8] == b"\x89PNG\r\n\x1a\n"


def test_docs_reference_every_example_asset():
    text = (EXAMPLES.parent / "mcp.md").read_text(encoding="utf-8")
    for asset in EXAMPLES.iterdir():
        assert f"mcp-examples/{asset.name}" in text, asset.name
