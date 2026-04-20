"""Tests for io/serializer.py — save/load round-trip fidelity."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF

from diagrammer.canvas.scene import DiagramScene
from diagrammer.io.serializer import DiagramSerializer
from diagrammer.items.annotation_item import AnnotationItem
from diagrammer.items.component_item import ComponentItem
from diagrammer.items.junction_item import JunctionItem
from diagrammer.items.shape_item import EllipseItem, LineItem, RectangleItem
from diagrammer.models.library import ComponentLibrary


@pytest.fixture
def populated_scene(library):
    """Create a scene with one of each item type for round-trip testing."""
    scene = DiagramScene(library=library)

    # Add shapes
    rect = RectangleItem(width=100, height=60)
    rect.setPos(QPointF(50, 50))
    scene.addItem(rect)

    ellipse = EllipseItem(width=80, height=80)
    ellipse.setPos(QPointF(200, 50))
    scene.addItem(ellipse)

    line = LineItem(start=QPointF(0, 0), end=QPointF(100, 100))
    line.setPos(QPointF(300, 50))
    scene.addItem(line)

    # Add annotation
    annot = AnnotationItem("Hello $x^2$")
    annot.setPos(QPointF(50, 200))
    scene.addItem(annot)

    # Add junction
    junc = JunctionItem()
    junc.setPos(QPointF(100, 100))
    scene.addItem(junc)

    return scene


class TestSerializerRoundTrip:
    def test_save_creates_file(self, populated_scene, tmp_path):
        out_file = tmp_path / "test.dgm"
        DiagramSerializer.save(populated_scene, out_file)
        assert out_file.exists()
        assert out_file.stat().st_size > 0

    def test_save_produces_valid_json(self, populated_scene, tmp_path):
        out_file = tmp_path / "test.dgm"
        DiagramSerializer.save(populated_scene, out_file)
        data = json.loads(out_file.read_text())
        assert "version" in data
        assert "shapes" in data
        assert "annotations" in data
        assert "junctions" in data

    def test_load_restores_shapes(self, populated_scene, tmp_path, library):
        out_file = tmp_path / "test.dgm"
        DiagramSerializer.save(populated_scene, out_file)

        new_scene = DiagramScene(library=library)
        DiagramSerializer.load(new_scene, out_file, library=library)

        rects = [i for i in new_scene.items() if isinstance(i, RectangleItem)]
        ellipses = [i for i in new_scene.items() if isinstance(i, EllipseItem)]
        lines = [i for i in new_scene.items() if isinstance(i, LineItem)]
        assert len(rects) == 1
        assert len(ellipses) == 1
        assert len(lines) == 1

    def test_load_restores_annotations(self, populated_scene, tmp_path, library):
        out_file = tmp_path / "test.dgm"
        DiagramSerializer.save(populated_scene, out_file)

        new_scene = DiagramScene(library=library)
        DiagramSerializer.load(new_scene, out_file, library=library)

        annots = [i for i in new_scene.items() if isinstance(i, AnnotationItem)]
        assert len(annots) == 1
        assert "Hello" in annots[0].source_text

    def test_load_restores_junctions(self, populated_scene, tmp_path, library):
        out_file = tmp_path / "test.dgm"
        DiagramSerializer.save(populated_scene, out_file)

        new_scene = DiagramScene(library=library)
        DiagramSerializer.load(new_scene, out_file, library=library)

        juncs = [i for i in new_scene.items() if isinstance(i, JunctionItem)]
        assert len(juncs) == 1

    def test_position_preserved(self, populated_scene, tmp_path, library):
        out_file = tmp_path / "test.dgm"
        DiagramSerializer.save(populated_scene, out_file)

        new_scene = DiagramScene(library=library)
        DiagramSerializer.load(new_scene, out_file, library=library)

        annots = [i for i in new_scene.items() if isinstance(i, AnnotationItem)]
        assert len(annots) == 1
        assert annots[0].pos().x() == pytest.approx(50, abs=1)
        assert annots[0].pos().y() == pytest.approx(200, abs=1)


class TestSerializerVersioning:
    def test_format_version_in_output(self, populated_scene, tmp_path):
        out_file = tmp_path / "test.dgm"
        DiagramSerializer.save(populated_scene, out_file)
        data = json.loads(out_file.read_text())
        assert "version" in data
        # Version should be a string like "1.1"
        parts = data["version"].split(".")
        assert len(parts) == 2
        assert all(p.isdigit() for p in parts)

    def test_reject_future_version(self, tmp_path, library):
        out_file = tmp_path / "future.dgm"
        out_file.write_text(json.dumps({"version": "999.0"}))
        scene = DiagramScene(library=library)
        with pytest.raises(Exception, match="newer"):
            DiagramSerializer.load(scene, out_file, library=library)


class TestSerializerEmptyScene:
    def test_empty_scene_roundtrip(self, tmp_path, library):
        scene = DiagramScene(library=library)
        out_file = tmp_path / "empty.dgm"
        DiagramSerializer.save(scene, out_file)

        new_scene = DiagramScene(library=library)
        DiagramSerializer.load(new_scene, out_file, library=library)
        # Should have no user items (only internal Qt items)
        user_items = [
            i for i in new_scene.items()
            if isinstance(i, (RectangleItem, EllipseItem, LineItem,
                              AnnotationItem, JunctionItem, ComponentItem))
        ]
        assert len(user_items) == 0
