from __future__ import annotations

import math

from viafencer.geometry import Point, TrackGeometry
from viafencer.mask_geometry import generate_mask_openings

MM = 1_000_000


def test_segment_mask_opening_uses_square_external_ends() -> None:
    track = TrackGeometry(
        kind="segment",
        start=Point(0, 0),
        end=Point(10 * MM, 0),
        width_nm=1 * MM,
    )

    result = generate_mask_openings([track], side_offset_nm=250_000)

    assert not result.skipped
    assert result.openings[0].polygon == (
        Point(0, 750_000),
        Point(10 * MM, 750_000),
        Point(10 * MM, -750_000),
        Point(0, -750_000),
    )


def test_connected_segment_corner_extends_internal_endpoint_only() -> None:
    horizontal = TrackGeometry(
        kind="segment",
        start=Point(0, 0),
        end=Point(10 * MM, 0),
        width_nm=1 * MM,
    )
    vertical = TrackGeometry(
        kind="segment",
        start=Point(10 * MM, 0),
        end=Point(10 * MM, 10 * MM),
        width_nm=1 * MM,
    )

    result = generate_mask_openings([horizontal, vertical], side_offset_nm=500_000)

    horizontal_polygon = result.openings[0].polygon
    vertical_polygon = result.openings[1].polygon
    assert min(point.x for point in horizontal_polygon) == 0
    assert max(point.x for point in horizontal_polygon) == 11 * MM
    assert min(point.y for point in vertical_polygon) == -1 * MM
    assert max(point.y for point in vertical_polygon) == 10 * MM


def test_arc_mask_opening_uses_radial_caps_without_endpoint_growth() -> None:
    track = TrackGeometry(
        kind="arc",
        start=Point(10 * MM, 0),
        mid=Point(0, 10 * MM),
        end=Point(-10 * MM, 0),
        width_nm=1 * MM,
    )

    result = generate_mask_openings([track], side_offset_nm=500_000)

    assert not result.skipped
    polygon = result.openings[0].polygon
    assert math.isclose(polygon[0].x, 11 * MM, abs_tol=1)
    assert math.isclose(polygon[0].y, 0, abs_tol=1)
    assert math.isclose(polygon[-1].x, 9 * MM, abs_tol=1)
    assert math.isclose(polygon[-1].y, 0, abs_tol=1)


def test_invalid_tracks_are_skipped() -> None:
    result = generate_mask_openings(
        [
            TrackGeometry(
                kind="segment",
                start=Point(0, 0),
                end=Point(1, 0),
                width_nm=0,
                label="rf",
            )
        ],
        side_offset_nm=100_000,
    )

    assert result.openings == []
    assert result.skipped == ["rf: missing or invalid track width"]
