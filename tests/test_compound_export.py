"""Regression tests for compound SVG export.

Exporting a compound writes an SVG file that the library scanner must
then re-load via ``ComponentDef.from_svg``. The round-trip is the only
way users add their own compounds, so any malformed-XML bug in the
export silently breaks the user library workflow.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF

from diagrammer.canvas.scene import DiagramScene
from diagrammer.io.compound_export import export_compound_component
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.shape_item import RectangleItem
from diagrammer.models.component_def import ComponentDef
from diagrammer.models.library import ComponentLibrary


def _two_port_def(library):
    cdef = library.get("TWO_PORTS/blank_box_100pt")
    if cdef is None:
        for d in library.all_defs():
            if len(d.ports) >= 2:
                return d
        pytest.skip("No 2-port component available in bundled library")
    return cdef


class TestCompoundExportRoundtrip:
    def test_exported_shape_is_well_formed_xml(self, scene, tmp_path: Path):
        rect = RectangleItem(width=100, height=60)
        rect.setPos(QPointF(10, 10))
        scene.addItem(rect)
        out = tmp_path / "shape.svg"
        ok = export_compound_component(scene, [rect], out, component_name="shape")
        assert ok
        # Must parse — duplicate xmlns regression would raise ParseError.
        ET.parse(out)

    def test_exported_component_is_well_formed_xml(
        self, scene, library, tmp_path: Path,
    ):
        """The compound export embeds the source SVG via ET.parse, which
        attaches namespaced children to the compound's root. With a
        plain ``svg`` root + an explicit ``xmlns`` attribute,
        ElementTree would auto-emit a SECOND ``xmlns`` because of the
        namespaced children — producing a duplicate-attribute parse
        error on later scan. This test guards that path."""
        cdef = _two_port_def(library)
        comp = ComponentItem(cdef)
        comp.setPos(QPointF(50, 50))
        scene.addItem(comp)
        out = tmp_path / "comp.svg"
        ok = export_compound_component(scene, [comp], out, component_name="comp")
        assert ok
        ET.parse(out)
        # And the compound must round-trip back through the library scan.
        loaded = ComponentDef.from_svg(out, category="test")
        assert loaded.name == "comp"
        # Original component had ports — the compound preserves them.
        assert len(loaded.ports) == len(cdef.ports)

    def test_unnamed_artwork_group_is_preserved(
        self, scene, library, tmp_path: Path,
    ):
        """User-reported regression: the QPS1 SVG keeps its visible
        artwork in an unnamed ``<g>`` (rather than ``<g id="artwork">``),
        and the compound exporter only copied the named group — so
        QPS1's diamond + bar were silently dropped on export and the
        re-placed compound appeared as just the leads. Falling back
        to "any non-hidden top-level child of the root" matches
        ``ComponentDef.from_svg``'s own fallback for these SVGs."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer

        # QPS1 (or any component whose artwork is in an unnamed <g>)
        # — try both bundled paths so the test isn't tied to a single
        # category folder.
        qps1 = (library.get("SLIDES_3pt/circuits/quantum/QPS1")
                or library.get("PAPER_2pt/circuits/quantum/QPS1"))
        if qps1 is None:
            pytest.skip("QPS1 fixture not present in bundled library")

        comp = ComponentItem(qps1)
        comp.setPos(QPointF(0, 0))
        scene.addItem(comp)
        out = tmp_path / "qps1_compound.svg"
        export_compound_component(scene, [comp], out, component_name="qps1c")

        # Render the exported compound. With the bug present, only the
        # two leads render (~tens of non-white pixels). With the fix,
        # the diamond + bar render too — many more non-white pixels.
        r = QSvgRenderer(str(out))
        assert r.isValid()
        img = QImage(200, 200, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.white)
        p = QPainter(img)
        r.render(p)
        p.end()
        non_white = sum(
            1 for y in range(img.height()) for x in range(img.width())
            if img.pixel(x, y) != 0xffffffff
        )
        assert non_white >= 200, (
            f"Compound SVG renders only {non_white} non-white pixels; "
            f"expected the artwork (diamond + bar) to add several "
            f"hundred more on top of the leads."
        )

    def test_user_compound_folder_scans_cleanly(
        self, scene, library, tmp_path: Path,
    ):
        """End-to-end: export several compounds into a folder, then
        scan the folder as a user library. Reproduces the exact
        workflow the user reported as broken."""
        cdef = _two_port_def(library)
        folder = tmp_path / "test components"
        folder.mkdir()
        names = ("testX", "testY", "testY_DGM")
        for name in names:
            c = ComponentItem(cdef)
            c.setPos(QPointF(0, 0))
            scene.addItem(c)
            export_compound_component(
                scene, [c], folder / f"{name}.svg", component_name=name,
            )
            scene.removeItem(c)
        user_lib = ComponentLibrary()
        user_lib.scan(folder)
        loaded_names = {d.name for d in user_lib.all_defs()}
        assert loaded_names == set(names)
