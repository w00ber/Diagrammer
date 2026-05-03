"""Geometry snapshot helpers for transform-invariance tests.

A snapshot is a content-addressed view of every diagram item's scene-space
geometry. Two snapshots compare equal iff the visual layout is the same
within a configurable tolerance — independent of internal storage choices.
This lets transform refactors prove "this sequence is a no-op" without
coupling tests to internal representation.
"""

from __future__ import annotations

import hashlib
from typing import Any

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsScene


def _round(value: float, tol: float) -> float:
    if tol <= 0:
        return float(value)
    return round(float(value) / tol) * tol


def _round_pt(pt: QPointF, tol: float) -> tuple[float, float]:
    return (_round(pt.x(), tol), _round(pt.y(), tol))


def _expanded_route_hash(conn, tol: float) -> str:
    """Hash the expanded (rendered) route of a ConnectionItem at *tol*."""
    pts = list(getattr(conn, "_expanded", []) or [])
    quantized = [_round_pt(p, tol) for p in pts]
    h = hashlib.sha1()
    for x, y in quantized:
        h.update(f"{x:.6f},{y:.6f};".encode("ascii"))
    return h.hexdigest()


def _component_entry(item, tol: float) -> dict[str, Any]:
    pos = item.pos()
    ports = {}
    for p in getattr(item, "ports", lambda: [])() if callable(getattr(item, "ports", None)) else []:
        ports[p.port_name] = _round_pt(p.scene_center(), tol)
    # ComponentItem stores `_ports` as a list; `ports` is a property returning it.
    if not ports:
        for p in getattr(item, "_ports", []) or []:
            ports[p.port_name] = _round_pt(p.scene_center(), tol)
    return {
        "kind": "component",
        "name": item.component_def.name,
        "pos": _round_pt(pos, tol),
        "z": _round(item.zValue(), tol),
        "rotation_angle": _round(item.rotation_angle, tol),
        "flip_h": bool(item.flip_h),
        "flip_v": bool(item.flip_v),
        "ports": ports,
    }


def _annotation_entry(item, tol: float) -> dict[str, Any]:
    # ``item.rotation()`` is the Qt-level rotation; ``rotation_angle`` (if/when
    # present after Phase C) is the persistent intrinsic angle. Capture both
    # so the harness works on either side of the refactor.
    pos = item.pos()
    rot = float(getattr(item, "rotation_angle", item.rotation()))
    flip_h = bool(getattr(item, "flip_h", False))
    flip_v = bool(getattr(item, "flip_v", False))
    bb = item.boundingRect()
    visual_center = item.mapToScene(bb.center())
    return {
        "kind": "annotation",
        "pos": _round_pt(pos, tol),
        "z": _round(item.zValue(), tol),
        "rotation": _round(rot, tol),
        "flip_h": flip_h,
        "flip_v": flip_v,
        "source_text": item.source_text,
        "visual_center": _round_pt(visual_center, tol),
    }


def _shape_entry(item, tol: float) -> dict[str, Any]:
    pos = item.pos()
    rot = float(getattr(item, "rotation_angle", item.rotation()))
    flip_h = bool(getattr(item, "flip_h", False))
    flip_v = bool(getattr(item, "flip_v", False))
    bb = item.boundingRect()
    visual_center = item.mapToScene(bb.center())
    entry: dict[str, Any] = {
        "kind": "shape",
        "subkind": type(item).__name__,
        "pos": _round_pt(pos, tol),
        "z": _round(item.zValue(), tol),
        "rotation": _round(rot, tol),
        "flip_h": flip_h,
        "flip_v": flip_v,
        "visual_center": _round_pt(visual_center, tol),
    }
    if hasattr(item, "shape_width"):
        entry["width"] = _round(item.shape_width, tol)
        entry["height"] = _round(item.shape_height, tol)
    if hasattr(item, "line_start"):
        entry["line_start"] = _round_pt(item.line_start, tol)
        entry["line_end"] = _round_pt(item.line_end, tol)
    return entry


def _connection_entry(item, tol: float) -> dict[str, Any]:
    src = item._source_port.scene_center() if item._source_port else QPointF()
    tgt = item._target_port.scene_center() if item._target_port else QPointF()
    waypoints_scene = []
    for w in item._waypoints:
        # Internal storage may be QPointF (pre-Phase B) or a Waypoint
        # object exposing ``to_scene()`` (post-Phase B). Probe both.
        if hasattr(w, "to_scene"):
            pt = w.to_scene()
        else:
            pt = QPointF(w)
        waypoints_scene.append(_round_pt(pt, tol))
    return {
        "kind": "connection",
        "z": _round(item.zValue(), tol),
        "source": _round_pt(src, tol),
        "target": _round_pt(tgt, tol),
        "waypoints": waypoints_scene,
        "routing_mode": item._routing_mode,
        "closed": bool(item._closed),
        "expanded_hash": _expanded_route_hash(item, tol),
    }


def _junction_entry(item, tol: float) -> dict[str, Any]:
    return {
        "kind": "junction",
        "pos": _round_pt(item.pos(), tol),
        "z": _round(item.zValue(), tol),
        "port": _round_pt(item.port.scene_center(), tol),
    }


def scene_snapshot(scene: QGraphicsScene, *, tol: float = 0.5) -> dict[str, dict[str, Any]]:
    """Snapshot every persistent item in *scene* keyed by ``instance_id``.

    Coordinates are rounded to multiples of *tol* so floating-point noise from
    transform composition doesn't cause spurious mismatches. Default 0.5
    matches what users perceive as "the same place" on a typical canvas.
    """
    from diagrammer.items.annotation_item import AnnotationItem
    from diagrammer.items.component_item import ComponentItem
    from diagrammer.items.connection_item import ConnectionItem
    from diagrammer.items.junction_item import JunctionItem
    from diagrammer.items.shape_item import LineItem, ShapeItem

    out: dict[str, dict[str, Any]] = {}
    for item in scene.items():
        iid = getattr(item, "instance_id", None) or getattr(item, "_id", None)
        if iid is None:
            continue
        if isinstance(item, ComponentItem):
            out[iid] = _component_entry(item, tol)
        elif isinstance(item, AnnotationItem):
            out[iid] = _annotation_entry(item, tol)
        elif isinstance(item, ConnectionItem):
            out[iid] = _connection_entry(item, tol)
        elif isinstance(item, JunctionItem):
            out[iid] = _junction_entry(item, tol)
        elif isinstance(item, (ShapeItem, LineItem)):
            out[iid] = _shape_entry(item, tol)
    return out


def assert_snapshots_equal(
    a: dict[str, dict[str, Any]],
    b: dict[str, dict[str, Any]],
    *,
    msg: str = "",
) -> None:
    """Compare two snapshots, raising AssertionError with a useful diff."""
    if a == b:
        return
    diff_lines: list[str] = []
    keys = sorted(set(a) | set(b))
    for k in keys:
        if k not in a:
            diff_lines.append(f"+ {k}: {b[k]}")
        elif k not in b:
            diff_lines.append(f"- {k}: {a[k]}")
        elif a[k] != b[k]:
            diff_lines.append(f"~ {k}:")
            ka, kb = a[k], b[k]
            sub_keys = sorted(set(ka) | set(kb))
            for sk in sub_keys:
                if ka.get(sk) != kb.get(sk):
                    diff_lines.append(f"    {sk}: {ka.get(sk)!r} -> {kb.get(sk)!r}")
    prefix = f"{msg}\n" if msg else ""
    raise AssertionError(prefix + "Snapshots differ:\n" + "\n".join(diff_lines))
