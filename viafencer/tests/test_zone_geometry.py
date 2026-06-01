from __future__ import annotations

from viafencer.geometry import Point
from viafencer.zone_geometry import (
    CircleObstacle,
    SegmentObstacle,
    ZonePolygon,
    filter_stitch_candidates,
    generate_stitch_candidates,
    point_in_polyline,
    polygon_contains_with_clearance,
)


def _square(size: int = 10_000) -> ZonePolygon:
    return ZonePolygon(
        outline=(
            Point(0, 0),
            Point(size, 0),
            Point(size, size),
            Point(0, size),
        )
    )


def test_point_in_polyline_accepts_inside_and_rejects_outside() -> None:
    square = _square().outline

    assert point_in_polyline(square, Point(5_000, 5_000))
    assert not point_in_polyline(square, Point(12_000, 5_000))


def test_polygon_contains_with_clearance_rejects_edge_and_hole() -> None:
    polygon = ZonePolygon(
        outline=_square().outline,
        holes=(
            (
                Point(4_000, 4_000),
                Point(6_000, 4_000),
                Point(6_000, 6_000),
                Point(4_000, 6_000),
            ),
        ),
    )

    assert polygon_contains_with_clearance(polygon, Point(2_000, 2_000), 500)
    assert not polygon_contains_with_clearance(polygon, Point(250, 2_000), 500)
    assert not polygon_contains_with_clearance(polygon, Point(5_000, 5_000), 500)


def test_generate_stitch_candidates_requires_inside_all_layers() -> None:
    bottom = ZonePolygon(
        outline=(
            Point(0, 0),
            Point(6_000, 0),
            Point(6_000, 10_000),
            Point(0, 10_000),
        )
    )

    result = generate_stitch_candidates(
        {3: [_square()], 34: [bottom]},
        x_spacing_nm=3_000,
        y_spacing_nm=3_000,
        via_diameter_nm=1_000,
        staggered=False,
    )

    assert result.candidates
    assert all(candidate.center.x <= 5_500 for candidate in result.candidates)


def test_generate_stitch_candidates_staggers_odd_rows() -> None:
    result = generate_stitch_candidates(
        {3: [_square()], 34: [_square()]},
        x_spacing_nm=3_000,
        y_spacing_nm=3_000,
        via_diameter_nm=1_000,
        staggered=True,
    )

    row_y = sorted({candidate.center.y for candidate in result.candidates})
    first_row = [
        candidate.center.x
        for candidate in result.candidates
        if candidate.center.y == row_y[0]
    ]
    second_row = [
        candidate.center.x for candidate in result.candidates if candidate.center.y == row_y[1]
    ]

    assert first_row[0] == 500
    assert second_row[0] == 2_000


def test_filter_stitch_candidates_rejects_other_net_circle_clearance() -> None:
    result = generate_stitch_candidates(
        {3: [_square()], 34: [_square()]},
        x_spacing_nm=3_000,
        y_spacing_nm=3_000,
        via_diameter_nm=1_000,
        staggered=False,
    )

    collision = filter_stitch_candidates(
        result.candidates,
        via_diameter_nm=1_000,
        via_layers=[3, 34],
        target_net_name="GND",
        clearance_nm=1_000,
        obstacles=[
            CircleObstacle(
                center=Point(500, 500),
                radius_nm=500,
                layers=frozenset({3}),
                net_name="VCC",
            )
        ],
    )

    assert collision.skipped_obstacle_overlap == 1
    assert all(candidate.center != Point(500, 500) for candidate in collision.accepted)


def test_filter_stitch_candidates_rejects_same_net_overlap() -> None:
    result = generate_stitch_candidates(
        {3: [_square()], 34: [_square()]},
        x_spacing_nm=3_000,
        y_spacing_nm=3_000,
        via_diameter_nm=1_000,
        staggered=False,
    )

    collision = filter_stitch_candidates(
        result.candidates,
        via_diameter_nm=1_000,
        via_layers=[3, 34],
        target_net_name="GND",
        clearance_nm=1_000,
        obstacles=[
            SegmentObstacle(
                start=Point(0, 500),
                end=Point(1_000, 500),
                radius_nm=250,
                layers=frozenset({3}),
                net_name="GND",
            )
        ],
    )

    assert collision.skipped_obstacle_overlap == 1
