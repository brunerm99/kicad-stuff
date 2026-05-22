from __future__ import annotations

import math

from viafencer.geometry import (
    CircularObstacle,
    Point,
    TrackGeometry,
    filter_overlapping_candidates,
    generate_fence_candidates,
)
from viafencer.models import FenceConfig, annular_width_nm, validate_config

MM = 1_000_000


def _config(**overrides: int | str) -> FenceConfig:
    values = {
        "via_diameter_nm": 500_000,
        "drill_diameter_nm": 250_000,
        "spacing_nm": 4_500_000,
        "gap_nm": 250_000,
        "collision_margin_nm": 0,
        "sides": "both",
    }
    values.update(overrides)
    return FenceConfig(**values)  # type: ignore[arg-type]


def test_segment_candidates_use_trace_edge_gap() -> None:
    track = TrackGeometry(
        kind="segment",
        start=Point(0, 0),
        end=Point(10 * MM, 0),
        width_nm=1 * MM,
    )

    result = generate_fence_candidates([track], _config())

    assert len(result.candidates) == 6
    left = [
        candidate.center for candidate in result.candidates if candidate.side == "left"
    ]
    right = [
        candidate.center for candidate in result.candidates if candidate.side == "right"
    ]
    assert [point.x for point in left] == [0, 5 * MM, 10 * MM]
    assert {point.y for point in left} == {1 * MM}
    assert {point.y for point in right} == {-1 * MM}


def test_spacing_is_pad_edge_to_pad_edge() -> None:
    track = TrackGeometry(
        kind="segment",
        start=Point(0, 0),
        end=Point(2 * MM, 0),
        width_nm=1 * MM,
    )

    result = generate_fence_candidates([track], _config(spacing_nm=500_000))

    left = [
        candidate.center for candidate in result.candidates if candidate.side == "left"
    ]
    assert [point.x for point in left] == [0, 1 * MM, 2 * MM]


def test_arc_candidates_place_inner_and_outer_rows() -> None:
    track = TrackGeometry(
        kind="arc",
        start=Point(10 * MM, 0),
        mid=Point(0, 10 * MM),
        end=Point(-10 * MM, 0),
        width_nm=1 * MM,
    )

    result = generate_fence_candidates([track], _config(spacing_nm=20 * MM))

    assert not result.skipped
    first_left = next(
        candidate.center for candidate in result.candidates if candidate.side == "left"
    )
    first_right = next(
        candidate.center for candidate in result.candidates if candidate.side == "right"
    )
    assert math.isclose(first_left.x, 9 * MM)
    assert math.isclose(first_left.y, 0, abs_tol=1)
    assert math.isclose(first_right.x, 11 * MM)
    assert math.isclose(first_right.y, 0, abs_tol=1)


def test_arc_inner_row_is_skipped_when_offset_collapses_radius() -> None:
    track = TrackGeometry(
        kind="arc",
        start=Point(500_000, 0),
        mid=Point(0, 500_000),
        end=Point(-500_000, 0),
        width_nm=1 * MM,
    )

    result = generate_fence_candidates([track], _config(spacing_nm=1 * MM))

    assert any("left fence radius collapses" in message for message in result.skipped)
    assert {candidate.side for candidate in result.candidates} == {"right"}


def test_generated_collision_filter_deduplicates_overlapping_rows() -> None:
    track = TrackGeometry(
        kind="segment",
        start=Point(0, 0),
        end=Point(10 * MM, 0),
        width_nm=1 * MM,
    )
    candidates = generate_fence_candidates([track, track], _config()).candidates

    result = filter_overlapping_candidates(candidates, via_diameter_nm=500_000)

    assert len(result.accepted) == 6
    assert result.skipped_generated_overlap == 6


def test_existing_obstacle_collision_skips_candidate_with_margin() -> None:
    track = TrackGeometry(
        kind="segment",
        start=Point(0, 0),
        end=Point(10 * MM, 0),
        width_nm=1 * MM,
    )
    candidates = generate_fence_candidates([track], _config()).candidates
    obstacle = CircularObstacle(center=Point(0, 1 * MM), radius_nm=250_000)

    result = filter_overlapping_candidates(
        candidates,
        via_diameter_nm=500_000,
        collision_margin_nm=100_000,
        obstacles=[obstacle],
    )

    assert len(result.accepted) == 5
    assert result.skipped_obstacle_overlap == 1


def test_validate_config_requires_positive_annular_ring() -> None:
    config = _config(via_diameter_nm=300_000, drill_diameter_nm=300_000)

    assert validate_config(config) == ["Drill diameter must be smaller than via diameter."]
    assert annular_width_nm(500_000, 250_000) == 125_000


def test_validate_config_allows_zero_edge_spacing() -> None:
    assert validate_config(_config(spacing_nm=0)) == []
