"""Build diagrams from code, with no window: the API behind the MCP server.

A ``Diagram`` owns an offscreen ``DiagramScene`` and an undo stack, and edits
the scene **only through the classes in ``commands/``** — the same code paths
the mouse drives — so a diagram built here is indistinguishable from one drawn
by hand: it saves to ``.dgm``, opens in the app, and every step is undoable.

The vocabulary is chosen for a caller that cannot see the canvas (a script, or
Claude through ``diagrammer.mcp_server``):

* Components are addressed by short, caller-chosen ids (``"J1"``, ``"C_c"``),
  not by the 12-hex instance ids the serializer uses.
* Placement is **port-anchored**: "put this part's ``in`` port at (x, y)", or
  relative to another part — "put it 40 px out from J1's ``top`` port, facing
  it" (:meth:`Diagram.attach`). The caller never computes an SVG offset.
* Port directions are reported from **geometry** (which way the lead points
  on screen after rotation and flip), never from the port's name. Port names
  in the shipped library are not reliable direction hints.

Coordinates are scene pixels, y pointing **down**; the default grid is 20 px.
Rotation is in degrees **clockwise on screen**.

Qt rule: every method here touches Qt objects and must run on the thread that
owns the ``QApplication``. ``mcp_server`` relies on this — see the note there.
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF
from PySide6.QtGui import QColor, QUndoStack
from PySide6.QtWidgets import QApplication

from diagrammer.models.component_def import ComponentDef
from diagrammer.models.library import ComponentLibrary

GRID = 20.0

_DIRS = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}


def ensure_qapp() -> QApplication:
    """Return the running QApplication, creating an offscreen one if needed.

    Must be called on the thread that will own all Qt objects.
    """
    import os
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([])
    return app


def builtin_components_root() -> Path:
    return Path(__file__).resolve().parent / "components"


def load_library(include_overlay: bool = True,
                 extra_roots: list[Path] | None = None) -> ComponentLibrary:
    """Scan components in the app's precedence order.

    Overlay first (the user's forks shadow built-ins), then the shipped tree,
    then any extra roots — mirroring ``MainWindow._rebuild_library``.
    """
    from diagrammer.models import overrides
    lib = ComponentLibrary()
    overlay = overrides.default_overlay_root()
    if include_overlay and overlay.is_dir():
        lib.scan(overlay, source_kind="overlay", flat_category="My Components")
    lib.scan(builtin_components_root(), source_kind="builtin")
    for root in extra_roots or ():
        if Path(root).is_dir():
            lib.scan(Path(root), source_kind="custom")
    return lib


def _direction_name(dx: float, dy: float) -> str:
    """Compass name for a unit-ish vector, or an angle if it is diagonal."""
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return "none"
    ux, uy = round(dx / n, 3), round(dy / n, 3)
    for (ax, ay), name in _DIRS.items():
        if abs(ux - ax) < 0.02 and abs(uy - ay) < 0.02:
            return name
    return f"{math.degrees(math.atan2(-uy, ux)):.0f}deg"


def _r(v: float) -> float:
    return round(v, 2)


class DiagramError(ValueError):
    """A caller mistake (unknown id, port or component). Message is for humans."""


class Diagram:
    """One diagram, edited through undoable commands, rendered offscreen."""

    def __init__(self, library: ComponentLibrary | None = None) -> None:
        ensure_qapp()
        from diagrammer.canvas.scene import DiagramScene
        self.library = library if library is not None else load_library()
        self.scene = DiagramScene(self.library)
        self.undo_stack = QUndoStack()
        self._items: dict[str, object] = {}   # caller id -> item
        self._counter = 0

    # ------------------------------------------------------------------
    # Catalogue
    # ------------------------------------------------------------------

    def search(self, query: str = "", library: str | None = None,
               limit: int = 40) -> list[dict]:
        """Components whose key contains every word of *query* (case-blind).

        *library* restricts to a top-level tree such as ``"PAPER_2pt"``.
        """
        words = query.lower().split()
        out = []
        for key in sorted(self.library._by_key):
            if library and not key.startswith(library.rstrip("/") + "/"):
                continue
            if all(w in key.lower() for w in words):
                d = self.library.get(key)
                if d.decorative:
                    continue
                out.append({"key": key, "ports": [p.name for p in d.ports],
                            "size": [_r(d.width), _r(d.height)]})
                if len(out) >= limit:
                    break
        return out

    def describe(self, key: str) -> dict:
        """Ports of an unrotated component, with geometric lead directions."""
        d = self._def(key)
        return {
            "key": key,
            "size": [_r(d.width), _r(d.height)],
            "stretch": {"h": d.stretch_h, "v": d.stretch_v},
            "ports": [
                {"name": p.name, "x": _r(p.x), "y": _r(p.y),
                 "points": _direction_name(p.approach_dx, p.approach_dy)}
                for p in d.ports
            ],
        }

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def add(self, key: str, at: tuple[float, float] = (0.0, 0.0), *,
            anchor: str | None = None, rotation: float = 0.0,
            flip_h: bool = False, flip_v: bool = False,
            id: str | None = None) -> str:
        """Place component *key* so that *anchor* lands exactly on *at*.

        *anchor* is a port name, or ``None`` for the component's centre.
        Returns the component's id (``id`` if given, else ``"c<N>"``).
        """
        from diagrammer.commands.add_command import (
            AddComponentCommand, MoveComponentCommand)
        from diagrammer.commands.transform_command import (
            FlipItemCommand, RotateItemCommand)

        cdef = self._def(key)
        cid = self._new_id(id)
        if anchor is not None and anchor not in [p.name for p in cdef.ports]:
            raise DiagramError(
                f"{key} has no port {anchor!r}; ports are "
                f"{[p.name for p in cdef.ports]}")

        self.undo_stack.beginMacro(f"Add {cid}")
        try:
            add = AddComponentCommand(self.scene, cdef, QPointF(0, 0))
            self.undo_stack.push(add)
            item = add.item
            if flip_h:
                self.undo_stack.push(FlipItemCommand(item, horizontal=True))
            if flip_v:
                self.undo_stack.push(FlipItemCommand(item, horizontal=False))
            if rotation % 360:
                self.undo_stack.push(RotateItemCommand(item, rotation % 360))
            here = self._anchor_point(item, anchor)
            target = QPointF(*at)
            self.undo_stack.push(MoveComponentCommand(
                item, item.pos(), item.pos() + (target - here)))
        finally:
            self.undo_stack.endMacro()
        self._items[cid] = item
        return cid

    def attach(self, key: str, port: str, to: tuple[str, str], *,
               gap: float = 2 * GRID, rotation: float | None = None,
               flip_h: bool = False, flip_v: bool = False,
               wire: bool = True, direction: str | None = None,
               id: str | None = None) -> str:
        """Place *key* so its *port* faces ``to=(other_id, other_port)``.

        The new port lands *gap* px outward from the other port, along the
        direction that port's lead points. With *rotation* ``None`` the part is
        turned (0/90/180/270) so its *port* points back at the other one — the
        natural "next element in the chain" placement. A wire is drawn between
        the two ports when *wire* is true and *gap* > 0.

        *direction* (right | left | up | down) overrides the way to step out
        from the other port. It is required when that port has no lead
        direction of its own (a junction, or a terminal whose port sits at
        its centre).
        """
        other_id, other_port = to
        anchor_pt, out_dir = self._port_geometry(other_id, other_port)
        if direction is not None:
            lookup = {v: k for k, v in _DIRS.items()}
            if direction not in lookup:
                raise DiagramError(f"direction must be one of {list(lookup)}")
            out_dir = tuple(float(c) for c in lookup[direction])
        elif out_dir == (0.0, 0.0):
            raise DiagramError(
                f"{other_id}.{other_port} has no lead direction; pass "
                f"direction='right'|'left'|'up'|'down'")
        target = (anchor_pt.x() + gap * out_dir[0],
                  anchor_pt.y() + gap * out_dir[1])

        if rotation is None:
            rotation = self._facing_rotation(key, port, out_dir,
                                             flip_h, flip_v)
        cid = self.add(key, target, anchor=port, rotation=rotation,
                       flip_h=flip_h, flip_v=flip_v, id=id)
        if wire and gap > 0:
            self.connect(other_id, other_port, cid, port)
        return cid

    def node(self, at: tuple[float, float], *, id: str | None = None) -> str:
        """A junction: one port, ``"center"``, that any number of wires share.

        Use it for T and cross connections; the app paints the node dot itself
        once three or more wires meet there.
        """
        from diagrammer.commands.connect_command import AddJunctionCommand
        from diagrammer.items.junction_item import JunctionItem
        nid = self._new_id(id, prefix="n")
        j = JunctionItem()
        j.setPos(QPointF(*at))
        self.undo_stack.push(AddJunctionCommand(self.scene, j))
        self._items[nid] = j
        return nid

    def connect(self, a: str, a_port: str, b: str, b_port: str, *,
                routing: str | None = None, id: str | None = None) -> str:
        """Wire ``a.a_port`` to ``b.b_port``. *routing*: ortho | ortho_45 | direct."""
        from diagrammer.commands.connect_command import CreateConnectionCommand
        pa = self._port(a, a_port)
        pb = self._port(b, b_port)
        cmd = CreateConnectionCommand(self.scene, pa, pb)
        self.undo_stack.push(cmd)
        conn = cmd.connection
        # The command takes its width from the *source* port's leads; a
        # junction or a lead-less terminal has none and gets the global
        # default. Owned by the command just pushed, so undo/redo keep it.
        width = self._wire_width(pa, pb)
        if width:
            conn.line_width = width
        if routing:
            if routing not in ("ortho", "ortho_45", "direct"):
                raise DiagramError(f"unknown routing {routing!r}")
            conn.routing_mode = routing
            conn.update_route()
        wid = self._new_id(id, prefix="w")
        self._items[wid] = conn
        return wid

    def text(self, text: str, at: tuple[float, float], *,
             size: float | None = None, id: str | None = None) -> str:
        """A text annotation with its top-left at *at*. ``$...$`` is math."""
        from diagrammer.commands.add_command import AddItemsCommand
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.panels.settings_dialog import app_settings
        tid = self._new_id(id, prefix="t")
        item = AnnotationItem(text)
        item.font_family = app_settings.default_annotation_font
        item.font_size = size or app_settings.default_annotation_size
        item.text_color = app_settings.default_annotation_color
        item.setPos(QPointF(*at))
        self.undo_stack.push(AddItemsCommand(self.scene, [item], "Add text"))
        self._items[tid] = item
        return tid

    def move(self, id: str, *, to: tuple[float, float] | None = None,
             by: tuple[float, float] | None = None,
             anchor: str | None = None) -> None:
        """Move an item: *to* puts its *anchor* (port, or centre) at a point;
        *by* shifts it. Attached wires follow."""
        from diagrammer.commands.add_command import MoveComponentCommand
        item = self._item(id)
        if (to is None) == (by is None):
            raise DiagramError("give exactly one of to= or by=")
        if by is not None:
            delta = QPointF(*by)
        else:
            delta = QPointF(*to) - self._anchor_point(item, anchor)
        self.undo_stack.push(
            MoveComponentCommand(item, item.pos(), item.pos() + delta))

    def remove(self, id: str) -> None:
        """Delete an item (and, for a component, the wires on it)."""
        from diagrammer.commands.delete_command import DeleteCommand
        item = self._item(id)
        doomed = [item]
        for port in getattr(item, "ports", []) or []:
            for conn in self._connections_of(port):
                doomed.append(conn)
        self.undo_stack.push(DeleteCommand(self.scene, doomed))
        gone = {i for i in doomed}
        self._items = {k: v for k, v in self._items.items() if v not in gone}

    def undo(self) -> None:
        self.undo_stack.undo()

    def clear(self) -> None:
        self.scene.clear()
        self.undo_stack.clear()
        self._items.clear()
        self._counter = 0

    # ------------------------------------------------------------------
    # Inspection and output
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Everything on the canvas, in the caller's ids, with port geometry."""
        from diagrammer.items.annotation_item import AnnotationItem
        from diagrammer.items.component_item import ComponentItem
        from diagrammer.items.connection_item import ConnectionItem
        from diagrammer.items.junction_item import JunctionItem
        ids = {v: k for k, v in self._items.items()}
        comps, wires, nodes, texts = [], [], [], []
        for item, iid in ids.items():
            if item.scene() is not self.scene:
                continue
            if isinstance(item, ComponentItem):
                br = item.sceneBoundingRect()
                comps.append({
                    "id": iid, "key": item.library_key,
                    "rotation": _r(item.rotation_angle),
                    "flip_h": item.flip_h, "flip_v": item.flip_v,
                    "bbox": [_r(br.left()), _r(br.top()),
                             _r(br.right()), _r(br.bottom())],
                    "ports": {
                        p.port_name: {
                            "at": [_r(v) for v in self._xy(p.scene_center())],
                            "points": _direction_name(
                                *self._port_geometry(iid, p.port_name)[1]),
                            "wires": len(self._connections_of(p)),
                        } for p in item.ports
                    },
                })
            elif isinstance(item, JunctionItem):
                nodes.append({"id": iid,
                              "at": [_r(v) for v in self._xy(item.pos())]})
            elif isinstance(item, ConnectionItem):
                wires.append({
                    "id": iid,
                    "from": [ids.get(item.source_port.component, "?"),
                             item.source_port.port_name],
                    "to": [ids.get(item.target_port.component, "?"),
                           item.target_port.port_name],
                    "routing": item.routing_mode,
                })
            elif isinstance(item, AnnotationItem):
                texts.append({"id": iid, "text": item.source_text,
                              "at": [_r(v) for v in self._xy(item.pos())]})
        return {"grid": GRID, "components": comps, "nodes": nodes,
                "wires": wires, "texts": texts}

    def render_png(self, dpi: int = 96, margin: float = 20.0) -> bytes:
        """PNG of the whole diagram on white, for a caller to look at."""
        from diagrammer.io.exporter import render_scene_to_qimage
        if not self.scene.items():
            raise DiagramError("nothing to render: the diagram is empty")
        image = render_scene_to_qimage(self.scene, dpi=dpi, margin=margin,
                                       background=QColor("white"))
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        return bytes(buf.data())

    def export(self, path: str | Path) -> Path:
        """Write ``.svg``, ``.pdf`` or ``.png`` (by suffix) for publication."""
        from diagrammer.io.exporter import DiagramExporter
        path = Path(path).expanduser()
        suffix = path.suffix.lower()
        if suffix == ".svg":
            DiagramExporter.export_svg(self.scene, str(path))
        elif suffix == ".pdf":
            DiagramExporter.export_pdf(self.scene, str(path))
        elif suffix == ".png":
            DiagramExporter.export_png(self.scene, str(path))
        else:
            raise DiagramError(f"export format must be .svg/.pdf/.png, "
                               f"not {suffix!r}")
        return path

    def save(self, path: str | Path) -> Path:
        """Write a ``.dgm`` that opens in the app for hand-finishing."""
        from diagrammer.io.serializer import DiagramSerializer
        path = Path(path).expanduser()
        if path.suffix.lower() != ".dgm":
            path = path.with_suffix(".dgm")
        DiagramSerializer.save(self.scene, path)
        return path

    def load(self, path: str | Path) -> None:
        """Replace the diagram with a ``.dgm``. Ids become the file's
        instance ids (the caller's short ids are not stored in the file)."""
        from diagrammer.io.serializer import DiagramSerializer
        self.clear()
        DiagramSerializer.load(self.scene, Path(path).expanduser(),
                               library=self.library)
        for item in self.scene.items():
            iid = getattr(item, "instance_id", None)
            if iid and item.parentItem() is None:
                self._items[iid] = item

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _def(self, key: str) -> ComponentDef:
        d = self.library.get(key)
        if d is None:
            import difflib
            names = {k: k.rsplit("/", 1)[-1].lower() for k in self.library._by_key}
            want = key.rsplit("/", 1)[-1].lower()
            close = set(difflib.get_close_matches(want, names.values(), n=5,
                                                  cutoff=0.6))
            hint = sorted(k for k, n in names.items() if n in close)[:8]
            raise DiagramError(f"no component {key!r}"
                               + (f"; similar: {hint}" if hint else ""))
        return d

    def _new_id(self, wanted: str | None, prefix: str = "c") -> str:
        if wanted:
            if wanted in self._items:
                raise DiagramError(f"id {wanted!r} is already used")
            return wanted
        while True:
            self._counter += 1
            cid = f"{prefix}{self._counter}"
            if cid not in self._items:
                return cid

    def _item(self, id: str):
        item = self._items.get(id)
        if item is None or item.scene() is not self.scene:
            raise DiagramError(f"no item {id!r}; ids are {sorted(self._items)}")
        return item

    def _port(self, id: str, name: str):
        item = self._item(id)
        if hasattr(item, "port_by_name"):
            port = item.port_by_name(name)
            if port is None:
                raise DiagramError(
                    f"{id} has no port {name!r}; ports are "
                    f"{[p.port_name for p in item.ports]}")
            return port
        if hasattr(item, "port"):  # junction
            return item.port
        raise DiagramError(f"{id} has no ports")

    def _connections_of(self, port) -> list:
        return self.scene.connections_on_port(port)

    def _wire_width(self, *ports) -> float | None:
        """Lead width at either end, else the library's house weight."""
        import re

        from diagrammer.commands.connect_command import _get_lead_stroke_width
        for port in ports:
            w = _get_lead_stroke_width(port)
            if w:
                return w
        keys = [getattr(p.component, "library_key", "") for p in ports]
        keys += [getattr(i, "library_key", "") for i in self._items.values()]
        for key in keys:
            m = re.match(r"[^/]*_(\d+(?:\.\d+)?)pt/", key or "")
            if m:
                return float(m.group(1))
        return None

    @staticmethod
    def _xy(p: QPointF) -> tuple[float, float]:
        return (p.x(), p.y())

    @staticmethod
    def _anchor_point(item, anchor: str | None) -> QPointF:
        if anchor is None:
            return item.mapToScene(item.intrinsic_anchor())
        return item.port_by_name(anchor).scene_center()

    def _port_geometry(self, id: str, name: str
                       ) -> tuple[QPointF, tuple[float, float]]:
        """Scene position of a port and the unit direction its lead points."""
        port = self._port(id, name)
        item = port.component if hasattr(port, "component") else None
        pt = port.scene_center()
        from diagrammer.items.component_item import ComponentItem
        if not isinstance(item, ComponentItem):
            return pt, (0.0, 0.0)  # a junction has no lead direction
        o = item.mapToScene(QPointF(0, 0))
        v = item.mapToScene(QPointF(port.approach_dx, port.approach_dy)) - o
        n = math.hypot(v.x(), v.y()) or 1.0
        return pt, (v.x() / n, v.y() / n)

    def _facing_rotation(self, key: str, port: str,
                         out_dir: tuple[float, float],
                         flip_h: bool, flip_v: bool) -> float:
        """The 0/90/180/270 rotation making *port* point against *out_dir*."""
        cdef = self._def(key)
        pdef = next((p for p in cdef.ports if p.name == port), None)
        if pdef is None:
            raise DiagramError(f"{key} has no port {port!r}; ports are "
                               f"{[p.name for p in cdef.ports]}")
        if out_dir == (0.0, 0.0):
            return 0.0
        dx = -pdef.approach_dx if flip_h else pdef.approach_dx
        dy = -pdef.approach_dy if flip_v else pdef.approach_dy
        best, best_dot = 0.0, -2.0
        for rot in (0.0, 90.0, 180.0, 270.0):
            # Qt's rotation is clockwise on screen (y down).
            a = math.radians(rot)
            rx = dx * math.cos(a) - dy * math.sin(a)
            ry = dx * math.sin(a) + dy * math.cos(a)
            dot = -(rx * out_dir[0] + ry * out_dir[1])
            if dot > best_dot + 1e-6:
                best, best_dot = rot, dot
        return best
