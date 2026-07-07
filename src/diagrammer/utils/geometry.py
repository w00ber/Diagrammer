"""Geometry helpers -- connection routing, corner rounding, and distance calculations."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF
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


def _emit_run_with_hops(
    path: QPainterPath,
    seg_a: QPointF,
    seg_b: QPointF,
    run_start_d: float,
    run_end: QPointF,
    run_end_d: float,
    pending_hops: list[tuple[QPointF, int]],
    hop_radius: float,
) -> None:
    """Emit the straight run of one segment, arcing over any hop points.

    The run covers the arclength window ``[run_start_d, run_end_d]`` of the
    segment ``seg_a → seg_b`` (the rest belongs to corner-rounding quads).
    Each pending hop is a ``(point, sign)`` pair; the sign selects which
    side the semicircle bulges (``+1`` = left of travel, ``-1`` = mirror).
    Hops projecting onto this segment are consumed from *pending_hops*;
    those whose semicircle would not fit inside the run window are dropped
    (that crossing renders plain).
    """
    seg_len = point_distance(seg_a, seg_b)
    if not pending_hops or seg_len < 1e-9 or hop_radius <= 1e-9:
        path.lineTo(run_end)
        return

    ux = (seg_b.x() - seg_a.x()) / seg_len
    uy = (seg_b.y() - seg_a.y()) / seg_len

    accepted: list[tuple[float, int]] = []
    for hop in list(pending_hops):
        pt, sign = hop
        proj, dist = closest_point_on_segment(pt, seg_a, seg_b)
        if dist > 0.25:
            continue
        # This hop belongs to this segment — consume it either way so a
        # collinear neighbouring segment can't match it a second time.
        pending_hops.remove(hop)
        t = (proj.x() - seg_a.x()) * ux + (proj.y() - seg_a.y()) * uy
        if run_start_d + hop_radius <= t <= run_end_d - hop_radius:
            accepted.append((t, sign))

    accepted.sort(key=lambda ts: ts[0])
    ang = math.degrees(math.atan2(-uy, ux))  # Qt angle of travel direction
    for t, sign in accepted:
        c = QPointF(seg_a.x() + ux * t, seg_a.y() + uy * t)
        path.lineTo(QPointF(c.x() - ux * hop_radius, c.y() - uy * hop_radius))
        rect = QRectF(
            c.x() - hop_radius, c.y() - hop_radius,
            hop_radius * 2.0, hop_radius * 2.0,
        )
        # Semicircle from the near cut to the far cut. sign=+1 bulges to
        # the left of travel (screen-up for a left-to-right wire); sign=-1
        # sweeps the opposite half-circle between the same two cut points
        # = mirror. arcTo ends exactly at c + u*hop_radius either way.
        path.arcTo(rect, ang + 180.0, -180.0 * sign)
    path.lineTo(run_end)


def build_rounded_path(
    points: list[QPointF], radius: float, *, closed: bool = False,
    hops: list[QPointF] | None = None, hop_radius: float = 0.0,
) -> QPainterPath:
    """Build a QPainterPath from a list of points with rounded corners.

    Args:
        points: Ordered list of path vertices (at least 2).
        radius: Corner rounding radius. 0 means sharp corners.
        closed: If True, close the path into a polygon and round the
                closing corner as well.
        hops: Optional crossing points to arc over with a semicircle
              (wire crossover "hops"). Each entry is either a bare
              ``QPointF`` (bulges left of travel) or a ``(QPointF, sign)``
              pair where ``sign=-1`` mirrors the bulge to the other side.
              Each point must lie on one of the path's segments. Ignored
              for closed polygons.
        hop_radius: Radius of the hop semicircles. Hops that would not
              fit inside a segment's straight run (too close to a corner
              or endpoint) are rendered plain.

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
    # Normalize hops to (point, sign) tuples. A bare QPointF (the form
    # used by the geometry tests and any caller that doesn't care about
    # bulge side) defaults to sign +1 (left-of-travel).
    def _norm_hop(h):
        if isinstance(h, QPointF):
            return (QPointF(h), 1)
        pt, sign = h
        return (QPointF(pt), 1 if sign >= 0 else -1)

    pending_hops = (
        [_norm_hop(h) for h in hops] if hops and hop_radius > 1e-9 else []
    )
    n = len(points)
    rounded = radius > 0 and n > 2

    # Corner geometry per interior vertex: (arc_start, ctrl, arc_end, r).
    # Same arithmetic as the pre-hop implementation so no-hop output is
    # unchanged.
    corners: list[tuple[QPointF, QPointF, QPointF, float]] = []
    if rounded:
        for i in range(1, n - 1):
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
            corners.append((arc_start, curr, arc_end, r))

    path.moveTo(points[0])
    for i in range(n - 1):
        seg_a = points[i]
        seg_b = points[i + 1]
        seg_len = point_distance(seg_a, seg_b)
        # Straight-run window along this segment: the corner quads at
        # either end own [0, r_i] and [seg_len - r_next, seg_len].
        run_start_d = corners[i - 1][3] if (rounded and i > 0) else 0.0
        if rounded and i < n - 2:
            run_end = corners[i][0]
            run_end_d = seg_len - corners[i][3]
        else:
            run_end = seg_b
            run_end_d = seg_len
        _emit_run_with_hops(
            path, seg_a, seg_b, run_start_d, run_end, run_end_d,
            pending_hops, hop_radius,
        )
        if rounded and i < n - 2:
            _, ctrl, arc_end, _ = corners[i]
            path.quadTo(ctrl, arc_end)
    return path


# ---------------------------------------------------------------------------
# Distance / projection helpers
# ---------------------------------------------------------------------------


def point_distance(a: QPointF, b: QPointF) -> float:
    """Euclidean distance between two QPointF's."""
    dx = a.x() - b.x()
    dy = a.y() - b.y()
    return (dx * dx + dy * dy) ** 0.5


def segment_intersection(
    p1: QPointF, p2: QPointF, q1: QPointF, q2: QPointF,
    *, endpoint_exclusion: float = 0.0,
) -> QPointF | None:
    """Intersection point of segments ``p1-p2`` and ``q1-q2``, or None.

    Parallel and collinear-overlapping segments yield None (overlapping
    wires get no crossing point). With *endpoint_exclusion* > 0, hits
    within that arclength of any segment end are rejected — this filters
    T-joins where wires meet at a shared port rather than crossing.
    """
    rx = p2.x() - p1.x()
    ry = p2.y() - p1.y()
    sx = q2.x() - q1.x()
    sy = q2.y() - q1.y()
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-9:
        return None
    qpx = q1.x() - p1.x()
    qpy = q1.y() - p1.y()
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if not (0.0 <= t <= 1.0 and 0.0 <= u <= 1.0):
        return None
    if endpoint_exclusion > 0.0:
        len_r = math.hypot(rx, ry)
        len_s = math.hypot(sx, sy)
        if (t * len_r < endpoint_exclusion
                or (1.0 - t) * len_r < endpoint_exclusion
                or u * len_s < endpoint_exclusion
                or (1.0 - u) * len_s < endpoint_exclusion):
            return None
    return QPointF(p1.x() + t * rx, p1.y() + t * ry)


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
# Arclength parameterization (position along a polyline)
# ---------------------------------------------------------------------------


def polyline_length(points: list[QPointF]) -> float:
    """Total arclength of a polyline."""
    total = 0.0
    for i in range(len(points) - 1):
        total += point_distance(points[i], points[i + 1])
    return total


def point_at_fraction(
    points: list[QPointF], t: float
) -> tuple[QPointF, QPointF]:
    """Point and unit tangent at arclength fraction *t* along a polyline.

    *t* is clamped to ``[0, 1]``. The tangent is the direction of the
    segment containing the point; when the point lands exactly on a
    shared vertex it is the incoming segment's direction. Degenerate
    polylines (< 2 points or zero length) return
    ``(first_point_or_origin, QPointF(1, 0))``.
    """
    fallback_tangent = QPointF(1.0, 0.0)
    if not points:
        return QPointF(), fallback_tangent
    if len(points) < 2:
        return QPointF(points[0]), fallback_tangent

    total = polyline_length(points)
    if total < 1e-9:
        return QPointF(points[0]), fallback_tangent

    t = max(0.0, min(1.0, t))
    target = t * total
    walked = 0.0
    for i in range(len(points) - 1):
        seg_len = point_distance(points[i], points[i + 1])
        if seg_len < 1e-12:
            continue
        if walked + seg_len >= target or i == len(points) - 2:
            s = (target - walked) / seg_len
            s = max(0.0, min(1.0, s))
            a, b = points[i], points[i + 1]
            pt = QPointF(a.x() + (b.x() - a.x()) * s,
                         a.y() + (b.y() - a.y()) * s)
            tang = QPointF((b.x() - a.x()) / seg_len,
                           (b.y() - a.y()) / seg_len)
            return pt, tang
        walked += seg_len
    return QPointF(points[-1]), fallback_tangent


def fraction_at_point(
    points: list[QPointF], pos: QPointF
) -> tuple[float, QPointF, float]:
    """Project *pos* onto a polyline.

    Returns ``(t, projected_point, distance)`` where *t* is the
    arclength fraction of the projection. Degenerate polylines return
    ``(0.0, first_point_or_origin, distance)``.
    """
    if not points:
        return 0.0, QPointF(), point_distance(pos, QPointF())
    if len(points) < 2:
        return 0.0, QPointF(points[0]), point_distance(pos, points[0])

    total = polyline_length(points)
    if total < 1e-9:
        return 0.0, QPointF(points[0]), point_distance(pos, points[0])

    best_t = 0.0
    best_proj = QPointF(points[0])
    best_dist = float("inf")
    walked = 0.0
    for i in range(len(points) - 1):
        seg_len = point_distance(points[i], points[i + 1])
        proj, dist = closest_point_on_segment(pos, points[i], points[i + 1])
        if dist < best_dist:
            best_dist = dist
            best_proj = proj
            best_t = (walked + point_distance(points[i], proj)) / total
        walked += seg_len
    return max(0.0, min(1.0, best_t)), best_proj, best_dist


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
