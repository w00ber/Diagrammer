"""Geometry helpers -- connection routing, corner rounding, and distance calculations."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

_COLLINEAR_TOL = 1e-3  # pixels -- treat points this close as aligned


# ---------------------------------------------------------------------------
# Orthogonal routing
# ---------------------------------------------------------------------------


def ortho_route(start: QPointF, end: QPointF) -> list[QPointF]:
    """Generate an orthogonal (H/V) route between *start* and *end*.

    The strategy:
    - If the two points are (nearly) aligned horizontally or vertically,
      return a two-point straight segment.
    - Otherwise produce an S-shaped route: horizontal to the midpoint X,
      vertical to the destination Y, then horizontal to the destination X
      (or the transposed equivalent when the vertical span is larger).

    The returned list always includes *start* and *end* and may contain
    2, 3, or 4 points total.
    """
    dx = end.x() - start.x()
    dy = end.y() - start.y()

    # Nearly collinear -- single straight segment
    if abs(dy) < _COLLINEAR_TOL:
        return [QPointF(start), QPointF(end)]
    if abs(dx) < _COLLINEAR_TOL:
        return [QPointF(start), QPointF(end)]

    # Decide horizontal-first vs vertical-first.
    # Horizontal-first: go halfway horizontally, then full vertical, then
    # remaining horizontal.  This tends to look cleaner when the horizontal
    # span dominates.
    if abs(dx) >= abs(dy):
        mid_x = start.x() + dx / 2.0
        return [
            QPointF(start),
            QPointF(mid_x, start.y()),
            QPointF(mid_x, end.y()),
            QPointF(end),
        ]
    else:
        mid_y = start.y() + dy / 2.0
        return [
            QPointF(start),
            QPointF(start.x(), mid_y),
            QPointF(end.x(), mid_y),
            QPointF(end),
        ]


# ---------------------------------------------------------------------------
# Diagonal-aware routing (45-degree support)
# ---------------------------------------------------------------------------


def ortho_route_45(start: QPointF, end: QPointF) -> list[QPointF]:
    """Like :func:`ortho_route` but may use a single 45-degree diagonal
    segment when the points are close to a diagonal alignment.

    Falls back to pure orthogonal when the diagonal shortcut wouldn't
    meaningfully simplify the path.
    """
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    adx = abs(dx)
    ady = abs(dy)

    if adx < _COLLINEAR_TOL or ady < _COLLINEAR_TOL:
        return ortho_route(start, end)

    # If aspect ratio is close to 1:1, use a direct diagonal.
    ratio = adx / ady if ady > _COLLINEAR_TOL else 999.0
    if 0.8 <= ratio <= 1.2:
        # Nearly 45 degrees -- use a single diagonal segment.
        return [QPointF(start), QPointF(end)]

    # Otherwise: horizontal, diagonal to align, vertical (or transposed).
    # The diagonal covers the smaller of adx/ady.
    diag = min(adx, ady)
    sx = math.copysign(1.0, dx)
    sy = math.copysign(1.0, dy)

    if adx >= ady:
        # Horizontal run first, then diagonal, then horizontal finish.
        horiz_run = (adx - diag) / 2.0
        p1 = QPointF(start.x() + sx * horiz_run, start.y())
        p2 = QPointF(p1.x() + sx * diag, start.y() + sy * diag)
        p3 = QPointF(end.x(), end.y())
        return _dedup([QPointF(start), p1, p2, p3])
    else:
        vert_run = (ady - diag) / 2.0
        p1 = QPointF(start.x(), start.y() + sy * vert_run)
        p2 = QPointF(start.x() + sx * diag, p1.y() + sy * diag)
        p3 = QPointF(end.x(), end.y())
        return _dedup([QPointF(start), p1, p2, p3])


def _dedup(points: list[QPointF]) -> list[QPointF]:
    """Remove consecutive near-duplicate points."""
    if not points:
        return points
    out = [points[0]]
    for p in points[1:]:
        if point_distance(out[-1], p) > _COLLINEAR_TOL:
            out.append(p)
    return out if len(out) >= 2 else [points[0], points[-1]]


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------


def build_rounded_path(
    points: list[QPointF], radius: float, *, closed: bool = False,
) -> QPainterPath:
    """Build a QPainterPath from a list of points with rounded corners.

    Args:
        points: Ordered list of path vertices (at least 2).
        radius: Corner rounding radius. 0 means sharp corners.
        closed: If True, close the path into a polygon and round the
                closing corner as well.

    Returns:
        A QPainterPath with rounded corners at each bend point.
    """
    path = QPainterPath()
    if len(points) < 2:
        return path

    # -- Closed polygon path -------------------------------------------
    if closed:
        # Strip duplicate closing point if present
        pts = list(points)
        if len(pts) >= 2 and point_distance(pts[0], pts[-1]) < 1.0:
            pts = pts[:-1]
        n = len(pts)
        if n < 3 or radius <= 0:
            path.moveTo(pts[0])
            for p in pts[1:]:
                path.lineTo(p)
            path.closeSubpath()
            return path

        def _corner(idx):
            """Compute arc_start and arc_end for corner at pts[idx]."""
            prev = pts[(idx - 1) % n]
            curr = pts[idx]
            nxt = pts[(idx + 1) % n]
            tp = QPointF(prev.x() - curr.x(), prev.y() - curr.y())
            tn = QPointF(nxt.x() - curr.x(), nxt.y() - curr.y())
            lp = max((tp.x() ** 2 + tp.y() ** 2) ** 0.5, 1e-9)
            ln = max((tn.x() ** 2 + tn.y() ** 2) ** 0.5, 1e-9)
            r = min(radius, lp / 2, ln / 2)
            a_start = QPointF(curr.x() + tp.x() / lp * r,
                              curr.y() + tp.y() / lp * r)
            a_end = QPointF(curr.x() + tn.x() / ln * r,
                            curr.y() + tn.y() / ln * r)
            return a_start, curr, a_end

        # Start just after the rounded corner at point 0
        s0, c0, e0 = _corner(0)
        path.moveTo(e0)

        # Round corners 1 through n-1
        for i in range(1, n):
            si, ci, ei = _corner(i)
            path.lineTo(si)
            path.quadTo(ci, ei)

        # Close: round the corner at point 0
        path.lineTo(s0)
        path.quadTo(c0, e0)
        path.closeSubpath()
        return path

    # -- Open polyline path --------------------------------------------
    path.moveTo(points[0])

    if radius <= 0 or len(points) == 2:
        for pt in points[1:]:
            path.lineTo(pt)
        return path

    for i in range(1, len(points) - 1):
        prev = points[i - 1]
        curr = points[i]
        nxt = points[i + 1]

        to_prev = QPointF(prev.x() - curr.x(), prev.y() - curr.y())
        to_next = QPointF(nxt.x() - curr.x(), nxt.y() - curr.y())

        len_prev = max((to_prev.x() ** 2 + to_prev.y() ** 2) ** 0.5, 1e-9)
        len_next = max((to_next.x() ** 2 + to_next.y() ** 2) ** 0.5, 1e-9)

        r = min(radius, len_prev / 2, len_next / 2)

        arc_start = QPointF(
            curr.x() + to_prev.x() / len_prev * r,
            curr.y() + to_prev.y() / len_prev * r,
        )
        arc_end = QPointF(
            curr.x() + to_next.x() / len_next * r,
            curr.y() + to_next.y() / len_next * r,
        )

        path.lineTo(arc_start)
        path.quadTo(curr, arc_end)

    path.lineTo(points[-1])
    return path


# ---------------------------------------------------------------------------
# Distance / projection helpers
# ---------------------------------------------------------------------------


def point_distance(a: QPointF, b: QPointF) -> float:
    """Euclidean distance between two QPointF's."""
    dx = a.x() - b.x()
    dy = a.y() - b.y()
    return (dx * dx + dy * dy) ** 0.5


def closest_point_on_segment(
    point: QPointF, seg_start: QPointF, seg_end: QPointF
) -> tuple[QPointF, float]:
    """Find the closest point on a line segment to a given point.

    Returns ``(closest_point, distance)``.
    """
    dx = seg_end.x() - seg_start.x()
    dy = seg_end.y() - seg_start.y()
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-12:
        return QPointF(seg_start), point_distance(point, seg_start)

    t = ((point.x() - seg_start.x()) * dx + (point.y() - seg_start.y()) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))

    closest = QPointF(seg_start.x() + t * dx, seg_start.y() + t * dy)
    return closest, point_distance(point, closest)


# ---------------------------------------------------------------------------
# Segment classification
# ---------------------------------------------------------------------------


def segment_orientation(p1: QPointF, p2: QPointF) -> str:
    """Return ``'h'`` for horizontal, ``'v'`` for vertical, ``'d'`` for diagonal."""
    dx = abs(p2.x() - p1.x())
    dy = abs(p2.y() - p1.y())
    if dy < _COLLINEAR_TOL:
        return "h"
    if dx < _COLLINEAR_TOL:
        return "v"
    return "d"


# ---------------------------------------------------------------------------
# Backward-compat alias
# ---------------------------------------------------------------------------

manhattan_route = ortho_route
