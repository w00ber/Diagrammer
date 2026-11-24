"""SwapComponentCommand — replace a placed part with another definition.

Convert to Library (issue #47, stage 4) swaps a part for its twin in the
other weight library: the same drawing at another line weight, with the same
port names at the same positions (``svglint --twins`` keeps the shipped pairs
that way). The placed part keeps everything that is the instance's rather
than the drawing's — position, rotation, flip, stretch, layer, stacking,
groups, instance id — and each wire moves to the new part's port of the same
name.

The new item is built once and the two items trade places on undo/redo, so
anything that holds the swapped-in item (a later command, the selection)
stays valid across undo/redo, as with :class:`AddComponentCommand`.
"""

from __future__ import annotations

import copy

from PySide6.QtGui import QUndoCommand

from diagrammer.items.component_item import ComponentItem


class SwapComponentCommand(QUndoCommand):
    """Swap *old* for a new item drawn from *new_def*.

    Refuses (``ok`` is False, nothing happens) when the new definition lacks
    a port that a wire is attached to: such a wire would have nowhere to go.
    """

    def __init__(self, scene, old: ComponentItem, new_def,
                 parent: QUndoCommand | None = None) -> None:
        super().__init__(parent)
        self._scene = scene
        self._old = old
        self._new_def = new_def
        self._new: ComponentItem | None = None
        new_names = {p.name for p in new_def.ports}
        #: (wire, old port, port name) for every wire end on the part
        self._ends: list[tuple] = []
        for port in old.ports:
            for conn in scene.connections_on_port(port):
                self._ends.append((conn, port.port_name))
        self.ok = all(name in new_names for _c, name in self._ends)
        lib = new_def.category.split("/", 1)[0]
        self.setText(f"Swap {old.component_def.name} to {lib}")

    @property
    def old_item(self) -> ComponentItem:
        return self._old

    @property
    def new_item(self) -> ComponentItem | None:
        return self._new

    def _build(self) -> ComponentItem:
        old = self._old
        new = ComponentItem(self._new_def, instance_id=old.instance_id)
        if old.rotation_angle:
            new.rotate_by(old.rotation_angle)
        if old.flip_h:
            new.set_flip_h(True)
        if old.flip_v:
            new.set_flip_v(True)
        if old.stretch_dx or old.stretch_dy:
            new.set_stretch(old.stretch_dx, old.stretch_dy)
        new._layer_index = getattr(old, "_layer_index", 0)
        new._group_ids = list(getattr(old, "_group_ids", []) or [])
        new._group_id = getattr(old, "_group_id", None)
        new.set_style_overrides(_carried_overrides(old, new))
        new.setPos(old.pos())
        # Twins can differ in viewBox (a heavier stroke needs more margin),
        # which moves the rotation origin. Land the ports where the old ones
        # were, so no wire moves; a port of the same name is the reference.
        ref = next((p for p in old.ports if new.port_by_name(p.port_name)),
                   None)
        if ref is not None:
            d = (ref.scene_center()
                 - new.port_by_name(ref.port_name).scene_center())
            new.setPos(new.pos() + d)
        return new

    def _trade(self, out: ComponentItem, into: ComponentItem) -> None:
        scene = self._scene
        selected = out.isSelected()
        z = out.zValue()
        visible = out.isVisible()
        into_by_name = {p.port_name: p for p in into.ports}
        out_by_name = {p.port_name: p for p in out.ports}
        for conn, name in self._ends:
            scene.unregister_connection(conn)
            conn.rebind_port(out_by_name[name], into_by_name[name])
        scene.removeItem(out)
        into.setZValue(z)
        into.setVisible(visible)
        scene.addItem(into)
        for conn, _name in self._ends:
            scene.register_connection(conn)
        into.setSelected(selected)
        if hasattr(scene, "update_connections"):
            scene.update_connections()

    def redo(self) -> None:
        if not self.ok:
            return
        if self._new is None:
            self._new = self._build()
        self._trade(self._old, self._new)

    def undo(self) -> None:
        if not self.ok or self._new is None:
            return
        self._trade(self._new, self._old)


def _carried_overrides(old: ComponentItem, new: ComponentItem):
    """The old part's per-element style overrides that still mean the same
    thing on the new one.

    An override is keyed by element path (``artwork/3``), so it carries only
    where the new drawing has an element of the same kind at that path. A
    lead's width is dropped: matching the other library's weight is what
    the swap is for. Its colour stays.
    """
    from diagrammer.models.svg_style_override import ComponentStyleOverrides
    out = ComponentStyleOverrides()
    if old.style_overrides.is_empty():
        return out
    try:
        tags = {i.element_path: i.tag for i in new.get_element_infos()}
        old_tags = {i.element_path: i.tag for i in old.get_element_infos()}
    except Exception:
        return out
    for path, ovr in old.style_overrides.overrides.items():
        if tags.get(path) is None or tags.get(path) != old_tags.get(path):
            continue
        ovr = copy.deepcopy(ovr)
        if not path.startswith("artwork"):
            ovr.stroke_width = None
        out.set(path, ovr)
    return out
