"""Tests for utils/geometry.py — routing, path building, and distance helpers."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from diagrammer.utils.geometry import (
    build_rounded_path,
    closest_point_on_segment,
    ortho_route,
    ortho_route_45,
    point_distance,
    segment_orientation,
)


# ---------------------------------------------------------------------------
# ortho_route
# ---------------------------------------------------------------------------


class TestOrthoRoute:
    def test_horizontal_alignment(self):
        pts = ortho_route(QPointF(0, 0), QPointF(100, 0))
        assert len(pts) == 2
        assert pts[0].x() == pytest.approx(0)
        assert pts[1].x() == pytest.approx(100)

    def test_vertical_alignment(self):
        pts = ortho_route(QPointF(0, 0), QPointF(0, 100))
        assert len(pts) == 2

    def test_horizontal_dominant(self):
        pts = ortho_route(QPointF(0, 0), QPointF(100, 40))
        assert len(pts) == 4
        # Should go horizontal to midpoint, then vertical, then horizontal
        assert pts[1].y() == pytest.approx(0)  # still on start Y
        assert pts[2].y() == pytest.approx(40)  # now on end Y
        assert pts[1].x() == pytest.approx(50)  # midpoint X
        assert pts[2].x() == pytest.approx(50)

    def test_vertical_dominant(self):
        pts = ortho_route(QPointF(0, 0), QPointF(20, 100))
        assert len(pts) == 4
        # Should go vertical to midpoint, then horizontal, then vertical
        assert pts[1].x() == pytest.approx(0)
        assert pts[2].x() == pytest.approx(20)

    def test_same_point(self):
        pts = ortho_route(QPointF(50, 50), QPointF(50, 50))
        assert len(pts) == 2

    def test_negative_direction(self):
        pts = ortho_route(QPointF(100, 100), QPointF(0, 0))
        assert len(pts) == 4
        assert pts[0].x() == pytest.approx(100)
        assert pts[-1].x() == pytest.approx(0)


# ---------------------------------------------------------------------------
# ortho_route_45
# ---------------------------------------------------------------------------


class TestOrthoRoute45:
    def test_horizontal_alignment_falls_back(self):
        pts = ortho_route_45(QPointF(0, 0), QPointF(100, 0))
        assert len(pts) == 2

    def test_diagonal_alignment(self):
        pts = ortho_route_45(QPointF(0, 0), QPointF(100, 100))
        # 1:1 aspect ratio → direct diagonal
        assert len(pts) == 2

    def test_near_diagonal(self):
        pts = ortho_route_45(QPointF(0, 0), QPointF(100, 90))
        # Aspect ratio ~1.11, within 0.8-1.2 range → direct
        assert len(pts) == 2

    def test_non_diagonal(self):
        pts = ortho_route_45(QPointF(0, 0), QPointF(200, 50))
        # Wide aspect ratio → H + diagonal + H
        assert len(pts) >= 3


# ---------------------------------------------------------------------------
# point_distance
# ---------------------------------------------------------------------------


class TestPointDistance:
    def test_same_point(self):
        assert point_distance(QPointF(0, 0), QPointF(0, 0)) == pytest.approx(0)

    def test_horizontal(self):
        assert point_distance(QPointF(0, 0), QPointF(3, 0)) == pytest.approx(3)

    def test_diagonal(self):
        assert point_distance(QPointF(0, 0), QPointF(3, 4)) == pytest.approx(5)


# ---------------------------------------------------------------------------
# closest_point_on_segment
# ---------------------------------------------------------------------------


class TestClosestPointOnSegment:
    def test_point_on_segment(self):
        pt, dist = closest_point_on_segment(
            QPointF(5, 0), QPointF(0, 0), QPointF(10, 0)
        )
        assert pt.x() == pytest.approx(5)
        assert dist == pytest.approx(0)

    def test_point_off_segment(self):
        pt, dist = closest_point_on_segment(
            QPointF(5, 3), QPointF(0, 0), QPointF(10, 0)
        )
        assert pt.x() == pytest.approx(5)
        assert pt.y() == pytest.approx(0)
        assert dist == pytest.approx(3)

    def test_point_before_start(self):
        pt, dist = closest_point_on_segment(
            QPointF(-5, 0), QPointF(0, 0), QPointF(10, 0)
        )
        assert pt.x() == pytest.approx(0)
        assert dist == pytest.approx(5)

    def test_point_after_end(self):
        pt, dist = closest_point_on_segment(
            QPointF(15, 0), QPointF(0, 0), QPointF(10, 0)
        )
        assert pt.x() == pytest.approx(10)
        assert dist == pytest.approx(5)

    def test_zero_length_segment(self):
        pt, dist = closest_point_on_segment(
            QPointF(5, 5), QPointF(3, 3), QPointF(3, 3)
        )
        assert pt.x() == pytest.approx(3)
        assert dist == pytest.approx(point_distance(QPointF(5, 5), QPointF(3, 3)))


# ---------------------------------------------------------------------------
# segment_orientation
# ---------------------------------------------------------------------------


class TestSegmentOrientation:
    def test_horizontal(self):
        assert segment_orientation(QPointF(0, 0), QPointF(10, 0)) == "h"

    def test_vertical(self):
        assert segment_orientation(QPointF(0, 0), QPointF(0, 10)) == "v"

    def test_diagonal(self):
        assert segment_orientation(QPointF(0, 0), QPointF(10, 10)) == "d"


# ---------------------------------------------------------------------------
# build_rounded_path
# ---------------------------------------------------------------------------


class TestBuildRoundedPath:
    def test_two_points_no_rounding(self):
        path = build_rounded_path([QPointF(0, 0), QPointF(100, 0)], 0)
        assert not path.isEmpty()

    def test_two_points_with_rounding(self):
        path = build_rounded_path([QPointF(0, 0), QPointF(100, 0)], 5)
        assert not path.isEmpty()

    def test_three_points_with_rounding(self):
        pts = [QPointF(0, 0), QPointF(50, 0), QPointF(50, 50)]
        path = build_rounded_path(pts, 5)
        assert not path.isEmpty()

    def test_empty_points(self):
        path = build_rounded_path([], 5)
        assert path.isEmpty()

    def test_single_point(self):
        path = build_rounded_path([QPointF(0, 0)], 5)
        assert path.isEmpty()

    def test_closed_path(self):
        pts = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100), QPointF(0, 100)]
        path = build_rounded_path(pts, 5, closed=True)
        assert not path.isEmpty()

    def test_closed_path_no_rounding(self):
        pts = [QPointF(0, 0), QPointF(100, 0), QPointF(100, 100)]
        path = build_rounded_path(pts, 0, closed=True)
        assert not path.isEmpty()
