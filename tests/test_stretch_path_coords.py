"""Tests for ComponentItem._shift_path_coords — gap-stretch coordinate shifting.

Regression coverage for the Illustrator export encoding: paths are exported as
one absolute ``moveto`` followed by *relative* curve/line commands. The shift
logic must normalize to absolute coordinates first, otherwise only the lone
absolute moveto moves and the whole subpath rigidly translates ("drags")
instead of extending across the break line.
"""

from __future__ import annotations

import re

import pytest

from diagrammer.items.component_item import ComponentItem

shift = ComponentItem._shift_path_coords


def _bbox(d: str) -> tuple[float, float, float, float]:
    """Geometric bbox (endpoints + control points) of an SVG path string."""
    toks = re.findall(r'[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)
    ca = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0}
    cx = cy = sx = sy = 0.0
    xs: list[float] = []
    ys: list[float] = []
    i = 0
    cmd = ""
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                cx, cy = sx, sy
            continue
        U = cmd.upper()
        rel = cmd.islower()
        k = ca[U]
        a = [float(toks[i + j]) for j in range(k)]
        i += k
        if U in ("M", "L", "T"):
            if rel:
                a[0] += cx
                a[1] += cy
            cx, cy = a[0], a[1]
        elif U == "H":
            cx = a[0] + (cx if rel else 0)
        elif U == "V":
            cy = a[0] + (cy if rel else 0)
        elif U in ("C", "S", "Q"):
            if rel:
                for j in range(0, k, 2):
                    a[j] += cx
                    a[j + 1] += cy
            for j in range(0, k, 2):
                xs.append(a[j])
                ys.append(a[j + 1])
            cx, cy = a[k - 2], a[k - 1]
        elif U == "A":
            if rel:
                a[5] += cx
                a[6] += cy
            cx, cy = a[5], a[6]
        xs.append(cx)
        ys.append(cy)
        if U == "M":
            sx, sy = cx, cy
            cmd = "l" if rel else "L"
    return (round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2))


# Illustrator-style: absolute moveto + relative curves (the real coax bulb).
REL = ("M30,3.1c1.3-1.2,2.7-1.8,4.1-1.8h-22.8c-5.2,0-9.6,8.4-9.6,18.7s4.3,18.7,"
       "9.6,18.7h22.8c-1.3,0-2.5-.5-3.7-1.5,0,0-.4-34.1-.4-34.1Z")
# Hand-authored absolute equivalent of the same geometry.
ABS = ("M30 3.1 C31.3 1.9 32.7 1.3 34.1 1.3 H11.3 C6.1 1.3 1.7 9.7 1.7 20 "
       "S6 38.7 11.3 38.7 H34.1 C32.8 38.7 31.6 38.2 30.4 37.2 "
       "C30.4 37.2 30 3.1 30 3.1 Z")


@pytest.mark.parametrize("dx", [0.0, 15.0, 30.0])
def test_relative_and_absolute_stretch_identically(dx):
    """A relative-encoded path stretches the same as its absolute twin."""
    assert _bbox(shift(REL, 20.0, dx, "x")) == _bbox(shift(ABS, 20.0, dx, "x"))


def test_break_line_anchors_left_side():
    """Geometry before the break stays put; only the far side extends.

    This is the core of the bug: with the old shift-absolute-only logic the
    whole subpath translated, so the left edge (min-x) would move too.
    """
    x_min0, _, x_max0, _ = _bbox(REL)
    x_min1, _, x_max1, _ = _bbox(shift(REL, 20.0, 30.0, "x"))
    assert x_min1 == pytest.approx(x_min0)          # left cap anchored
    assert x_max1 == pytest.approx(x_max0 + 30.0)   # right side extended


def test_output_is_absolute_only():
    """Result contains no relative (lowercase) command letters."""
    out = shift(REL, 20.0, 30.0, "x")
    assert not any(c.isalpha() and c.islower() for c in out)


def test_h_lead_relative_matches_absolute():
    assert _bbox(shift("M0,10 h40", 20.0, 10.0, "x")) == \
           _bbox(shift("M0 10 H40", 20.0, 10.0, "x"))


def test_arc_shifts_endpoint_not_radii():
    """An arc's endpoint shifts past the break; rx/ry/rotation/flags do not."""
    toks = shift("M0 0 A5 5 0 0 1 30 0", 20.0, 10.0, "x").split()
    arc_args = toks[toks.index("A") + 1:toks.index("A") + 8]
    # rx ry rotation large-arc-flag sweep-flag x y
    assert arc_args == ["5", "5", "0", "0", "1", "40", "0"]


def test_polyline_points_still_supported():
    """Bare 'x y x y' points lists (polyline/polygon) shift by axis parity."""
    assert shift("0 0 30 0 30 10", 20.0, 10.0, "x") == "0 0 40 0 40 10"


def test_zero_delta_is_geometry_preserving():
    assert _bbox(shift(REL, 20.0, 0.0, "x")) == _bbox(REL)


# --- Repeat (tile) stretch: rigid=True must translate whole subpaths, never
#     deform. Regression for TWP1, whose twist artwork has control points that
#     overshoot the break line. ---

# A far-side twist that *touches* break2=60 (leftmost endpoint at 60) and
# extends right — must translate rigidly by delta, not tear at x=60.
TWP_RIGHT = ("M80,14.9v2c-4.6,0-6.9,1.9-9.4,3.9-2.6,2.1-5.3,4.3-10.6,4.3v-2"
             "c4.6,0,6.9-1.9,9.4-3.9,2.6-2.1,5.3-4.3,10.6-4.3Z")
# A tile-region twist (starts at 40) whose control points overshoot to x=60 —
# must stay put so clones fill the gap behind it.
TWP_TILE = ("M40,25.2v-2c4.6,0,6.9-1.9,9.4-3.9,2.6-2.1,5.3-4.3,10.6-4.3v2"
            "c-4.6,0,6.9,1.9-9.4,3.9-2.6,2.1-5.3,4.3-10.6,4.3Z")


def test_rigid_translates_far_subpath_as_unit():
    x0, _, x1_, _ = _bbox(TWP_RIGHT)
    x0s, _, x1s, _ = _bbox(shift(TWP_RIGHT, 60.0, 40.0, "x", rigid=True))
    assert x0s == pytest.approx(x0 + 40.0)   # left edge moved too (rigid)
    assert x1s == pytest.approx(x1_ + 40.0)


def test_rigid_leaves_tile_subpath_untouched():
    """Overshooting control points must not drag a tile subpath across break2."""
    assert _bbox(shift(TWP_TILE, 60.0, 40.0, "x", rigid=True)) == _bbox(TWP_TILE)


def test_rigid_vs_gap_differ_on_break_touching_subpath():
    """The two modes must disagree exactly where it matters: a subpath whose
    far side touches the break. Gap pins the touch point; rigid moves it."""
    gap = _bbox(shift(TWP_RIGHT, 60.0, 40.0, "x", rigid=False))
    rig = _bbox(shift(TWP_RIGHT, 60.0, 40.0, "x", rigid=True))
    assert gap[0] == pytest.approx(60.0)     # gap: left edge pinned at break
    assert rig[0] == pytest.approx(100.0)    # rigid: left edge translated
