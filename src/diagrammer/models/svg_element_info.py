"""SVG element identification and bounding box computation for isolation mode.

Enumerates leaf SVG elements within the artwork and leads layers,
assigning each a stable path string (e.g. ``artwork/0``, ``leads/1``)
and computing an approximate bounding box for hit-testing.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF

# SVG namespace
_SVG_NS = "http://www.w3.org/2000/svg"

# Tags that represent visible leaf elements
_LEAF_TAGS = {"line", "path", "rect", "circle", "ellipse", "polyline", "polygon", "text", "use"}


def _strip_ns(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


@dataclass
class SvgElementInfo:
    """Information about a single SVG element within a component."""
    element_path: str    # e.g. "artwork/0", "leads/1"
    tag: str             # e.g. "path", "line", "rect"
    layer: str           # "artwork" or "leads"
    css_classes: list[str]
    bbox: QRectF         # approximate bounding box in viewBox coords
    label: str           # human-readable, e.g. "artwork: path #1"


def enumerate_svg_elements(svg_path: Path) -> list[SvgElementInfo]:
    """Walk the artwork and leads layers and return info for each leaf element.

    The element_path is a stable identifier based on depth-first child
    indices within the layer group — e.g. ``artwork/0``, ``artwork/1/0``.
    """
    tree = ET.parse(str(svg_path))
    root = tree.getroot()
    results: list[SvgElementInfo] = []

    for layer_id in ("artwork", "leads", "leads-left", "leads-right",
                     "leads-top", "leads-bottom"):
        group = _find_group(root, layer_id)
        if group is None:
            continue
        _walk_layer(group, layer_id, layer_id, results)

    return results


def _find_group(root: ET.Element, group_id: str) -> ET.Element | None:
    for elem in root.iter():
        if _strip_ns(elem.tag) == "g" and elem.get("id") == group_id:
            return elem
    return None


def _walk_layer(
    elem: ET.Element,
    layer: str,
    path_prefix: str,
    results: list[SvgElementInfo],
) -> None:
    """Recursively enumerate leaf elements, building index paths."""
    child_idx = 0
    for child in elem:
        tag = _strip_ns(child.tag)
        child_path = f"{path_prefix}/{child_idx}"

        if tag == "g":
            # Recurse into sub-groups
            _walk_layer(child, layer, child_path, results)
        elif tag in _LEAF_TAGS:
            classes = child.get("class", "").split()
            bbox = _compute_bbox(child, tag)
            idx_in_layer = sum(1 for r in results if r.layer == layer)
            label = f"{layer}: {tag} #{idx_in_layer + 1}"
            results.append(SvgElementInfo(
                element_path=child_path,
                tag=tag,
                layer=layer,
                css_classes=classes,
                bbox=bbox,
                label=label,
            ))

        child_idx += 1


def _compute_bbox(elem: ET.Element, tag: str) -> QRectF:
    """Compute an approximate bounding box from element attributes."""
    try:
        if tag == "line":
            x1 = float(elem.get("x1", "0"))
            y1 = float(elem.get("y1", "0"))
            x2 = float(elem.get("x2", "0"))
            y2 = float(elem.get("y2", "0"))
            return _rect_from_points([(x1, y1), (x2, y2)])

        if tag == "rect":
            x = float(elem.get("x", "0"))
            y = float(elem.get("y", "0"))
            w = float(elem.get("width", "0"))
            h = float(elem.get("height", "0"))
            return QRectF(x, y, w, h)

        if tag == "circle":
            cx = float(elem.get("cx", "0"))
            cy = float(elem.get("cy", "0"))
            r = float(elem.get("r", "0"))
            return QRectF(cx - r, cy - r, r * 2, r * 2)

        if tag == "ellipse":
            cx = float(elem.get("cx", "0"))
            cy = float(elem.get("cy", "0"))
            rx = float(elem.get("rx", "0"))
            ry = float(elem.get("ry", "0"))
            return QRectF(cx - rx, cy - ry, rx * 2, ry * 2)

        if tag == "path":
            return _path_bbox(elem.get("d", ""))

        if tag in ("polyline", "polygon"):
            return _points_bbox(elem.get("points", ""))

    except (ValueError, TypeError):
        pass

    return QRectF(0, 0, 0, 0)


def _rect_from_points(points: list[tuple[float, float]]) -> QRectF:
    if not points:
        return QRectF(0, 0, 0, 0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    # Add padding for stroke width
    pad = 2.0
    return QRectF(x_min - pad, y_min - pad,
                  (x_max - x_min) + 2 * pad, (y_max - y_min) + 2 * pad)


def _path_bbox(d: str) -> QRectF:
    """Approximate bounding box from SVG path data (control-point hull)."""
    nums = re.findall(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)
    if len(nums) < 2:
        return QRectF(0, 0, 0, 0)
    # Pair up as (x, y) coordinates — rough approximation
    points = []
    for i in range(0, len(nums) - 1, 2):
        try:
            points.append((float(nums[i]), float(nums[i + 1])))
        except (ValueError, IndexError):
            break
    return _rect_from_points(points)


def _points_bbox(points_str: str) -> QRectF:
    """Bounding box from SVG points attribute (polyline/polygon)."""
    nums = re.findall(r'[-+]?(?:\d+\.?\d*|\.\d+)', points_str)
    points = []
    for i in range(0, len(nums) - 1, 2):
        points.append((float(nums[i]), float(nums[i + 1])))
    return _rect_from_points(points)
