"""A pasted or imported SVG behaves like any other canvas object.

Three things used to go wrong once an arbitrary SVG was on the canvas:

* **It couldn't be clicked.** ``QSvgRenderer.render(painter, target)`` does
  not clip to ``target``, so a document that draws outside its own viewBox —
  what a cropped artboard exported from another app looks like — painted its
  artwork outside the item's rect, where Qt neither hit-tests nor repaints.
  The user saw the graphic and clicked straight through it.
* **Rotate and flip were no-ops.** ``transform_ops`` collected components,
  junctions, annotations and shapes; ``SvgImageItem`` was in none of those
  lists, so the menu items and shortcuts did nothing and said nothing.
* **Resize wasn't undoable**, unlike every other canvas mutation.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt

from diagrammer.items.svg_image_item import SvgImageItem

from tests.test_transform_invariants import _make_transform_host

# An ordinary export: artwork fits the viewBox.
TIDY = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60" '
    b'viewBox="0 0 80 60"><rect width="80" height="60" fill="black"/></svg>'
)

# A cropped artboard: the viewBox is a 20x20 window, the artwork sits at
# (100, 100) and is 80x60. Everything visible is outside the declared box.
CROPPED = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" '
    b'viewBox="0 0 20 20">'
    b'<rect x="100" y="100" width="80" height="60" fill="red"/></svg>'
)


@pytest.fixture
def view(scene):
    from diagrammer.canvas.view import DiagramView

    v = DiagramView(scene)
    v.resize(800, 600)
    yield v
    import shiboken6
    v.setScene(None)
    shiboken6.delete(v)


class TestContentAwareSizing:
    def test_tidy_svg_keeps_its_native_size(self):
        """The common case must be untouched: viewBox in, viewBox out."""
        item = SvgImageItem(TIDY)
        assert (item.image_width, item.image_height) == (80.0, 60.0)
        assert item.source_rect.topLeft() == QPointF(0, 0)
        assert (item.source_rect.width(), item.source_rect.height()) == (80.0, 60.0)

    def test_cropped_artboard_sizes_to_its_artwork(self):
        """Content far outside the viewBox widens the source rect, so the
        item's rect is where the artwork actually paints."""
        item = SvgImageItem(CROPPED)
        src = item.source_rect
        # Union of the 20x20 viewBox and the artwork at (100,100,80,60).
        assert (src.x(), src.y()) == (0.0, 0.0)
        assert (src.width(), src.height()) == (180.0, 160.0)
        assert (item.image_width, item.image_height) == (180.0, 160.0)

    def test_artwork_is_inside_the_bounding_rect(self):
        """boundingRect is the default shape(), hence the hit area."""
        item = SvgImageItem(CROPPED)
        br = item.boundingRect()
        for corner in (QPointF(100, 100), QPointF(180, 160)):
            assert br.contains(corner), f"{corner} outside {br}"


class TestClickSelection:
    def _press(self, view, scene_pos):
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent

        vp = view.mapFromScene(scene_pos)
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(vp),
            view.viewport().mapToGlobal(vp),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        view.mousePressEvent(event)

    def test_clicking_a_tidy_svg_selects_it(self, scene, view):
        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.setPos(QPointF(0, 0))
        self._press(view, item.mapToScene(QPointF(40, 30)))
        assert item.isSelected()

    def test_clicking_a_cropped_artboard_selects_it(self, scene, view):
        """The regression: a click on the visible red rect used to miss."""
        item = SvgImageItem(CROPPED)
        scene.addItem(item)
        item.setPos(QPointF(0, 0))
        # Centre of the artwork, which lies outside the 20x20 viewBox.
        self._press(view, item.mapToScene(QPointF(140, 130)))
        assert item.isSelected()


class TestRotateAndFlip:
    def test_rotate_selected_turns_the_image(self, scene, library):
        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.setSelected(True)
        host = _make_transform_host(scene, library)
        host._rotate_selected(90)
        assert item.rotation_angle == pytest.approx(90)

    def test_fine_rotate_selected_turns_the_image(self, scene, library):
        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.setSelected(True)
        host = _make_transform_host(scene, library)
        host._fine_rotate_selected(15)
        assert item.rotation_angle == pytest.approx(15)

    def test_rotation_is_undoable(self, scene, library):
        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.setSelected(True)
        host = _make_transform_host(scene, library)
        host._rotate_selected(90)
        scene.undo_stack.undo()
        assert item.rotation_angle == pytest.approx(0)

    @pytest.mark.parametrize("horizontal", [True, False])
    def test_flip_selected_mirrors_the_image(self, scene, library, horizontal):
        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.setSelected(True)
        host = _make_transform_host(scene, library)
        host._flip_selected(horizontal)
        assert item.flip_h is horizontal
        assert item.flip_v is (not horizontal)

    def test_rotation_pivots_on_the_centre(self, scene, library):
        """A lone image turns about its own centre, which therefore stays put."""
        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.setPos(QPointF(200, 200))
        before = item.mapToScene(item.intrinsic_anchor())
        item.setSelected(True)
        host = _make_transform_host(scene, library)
        host._rotate_selected(90)
        after = item.mapToScene(item.intrinsic_anchor())
        assert after.x() == pytest.approx(before.x(), abs=1e-6)
        assert after.y() == pytest.approx(before.y(), abs=1e-6)


class TestPersistence:
    def test_rotation_and_flip_survive_a_save_load(self, scene, library, tmp_path):
        from diagrammer.canvas.scene import DiagramScene
        from diagrammer.io.serializer import DiagramSerializer

        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.rotate_by(90)
        item.set_flip_h(True)

        path = tmp_path / "rot.dgm"
        DiagramSerializer.save(scene, path)

        reloaded = DiagramScene(library=library)
        DiagramSerializer.load(reloaded, path, library)
        images = [i for i in reloaded.items() if isinstance(i, SvgImageItem)]
        assert len(images) == 1
        assert images[0].rotation_angle == pytest.approx(90)
        assert images[0].flip_h is True
        assert images[0].flip_v is False

    def test_copy_paste_carries_the_transform(self, scene, library):
        from tests.test_transform_invariants import _make_clipboard_host

        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.rotate_by(180)
        item.set_flip_v(True)
        item.setSelected(True)

        host = _make_clipboard_host(scene, library)
        host._copy()
        # _paste consults the system pasteboard first; an in-app copy wins.
        host._paste()
        images = [i for i in scene.items() if isinstance(i, SvgImageItem)]
        assert len(images) == 2
        pasted = [i for i in images if i is not item][0]
        assert pasted.rotation_angle == pytest.approx(180)
        assert pasted.flip_v is True


class TestResizeUndo:
    def test_handle_drag_pushes_an_undo_command(self, scene, view):
        """Every canvas mutation goes through a command — resize included."""
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        item = SvgImageItem(TIDY)
        scene.addItem(item)
        item.setPos(QPointF(0, 0))
        item.setSelected(True)

        def _ev(kind, local, scene_pos):
            e = QGraphicsSceneMouseEvent(kind)
            e.setPos(local)
            e.setScenePos(scene_pos)
            e.setButton(Qt.MouseButton.LeftButton)
            e.setButtons(Qt.MouseButton.LeftButton)
            e.setModifiers(Qt.KeyboardModifier.NoModifier)
            return e

        before = scene.undo_stack.count()
        # Grab the bottom-right handle and drag it out by (20, 10).
        item.mousePressEvent(
            _ev(QEvent.Type.GraphicsSceneMousePress, QPointF(80, 60), QPointF(80, 60)))
        item.mouseMoveEvent(
            _ev(QEvent.Type.GraphicsSceneMouseMove, QPointF(100, 70), QPointF(100, 70)))
        item.mouseReleaseEvent(
            _ev(QEvent.Type.GraphicsSceneMouseRelease, QPointF(100, 70), QPointF(100, 70)))

        assert (item.image_width, item.image_height) == (100.0, 70.0)
        assert scene.undo_stack.count() == before + 1
        scene.undo_stack.undo()
        assert (item.image_width, item.image_height) == (80.0, 60.0)
