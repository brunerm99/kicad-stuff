"""Pure geometry for RF via-fence placement."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal

from .models import FenceConfig, selected_sides

EPSILON = 1e-6
TAU = 2 * math.pi


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class TrackGeometry:
    kind: Literal["segment", "arc"]
    start: Point
    end: Point
    width_nm: int
    mid: Point | None = None
    label: str = ""
    layer: int | None = None


@dataclass(frozen=True)
class CircularObstacle:
    center: Point
    radius_nm: float
    label: str = ""


@dataclass(frozen=True)
class FenceCandidate:
    center: Point
    side: Literal["left", "right"]
    track_index: int
    track_label: str


@dataclass(frozen=True)
class CandidateGeneration:
    candidates: list[FenceCandidate]
    skipped: list[str]


@dataclass(frozen=True)
class CollisionResult:
    accepted: list[FenceCandidate]
    skipped_generated_overlap: int = 0
    skipped_obstacle_overlap: int = 0


def generate_fence_candidates(
    tracks: Iterable[TrackGeometry], config: FenceConfig
) -> CandidateGeneration:
    candidates: list[FenceCandidate] = []
    skipped: list[str] = []

    for index, track in enumerate(tracks):
        label = track.label or f"track {index + 1}"
        if track.width_nm <= 0:
            skipped.append(f"{label}: missing or invalid track width")
            continue

        offset_nm = track.width_nm / 2 + config.gap_nm + config.via_diameter_nm / 2
        center_pitch_nm = config.via_diameter_nm + config.spacing_nm
        if track.kind == "arc":
            generated, arc_skipped = _arc_candidates(
                track, index, label, offset_nm, center_pitch_nm, config
            )
        else:
            generated, arc_skipped = _segment_candidates(
                track, index, label, offset_nm, center_pitch_nm, config
            )
        candidates.extend(generated)
        skipped.extend(arc_skipped)

    return CandidateGeneration(candidates=candidates, skipped=skipped)


def filter_overlapping_candidates(
    candidates: Iterable[FenceCandidate],
    via_diameter_nm: int,
    collision_margin_nm: int = 0,
    obstacles: Iterable[CircularObstacle] = (),
) -> CollisionResult:
    accepted: list[FenceCandidate] = []
    generated_index = _SpatialIndex(max(via_diameter_nm + collision_margin_nm, 1))
    obstacle_list = list(obstacles)
    generated_skips = 0
    obstacle_skips = 0

    via_radius_nm = via_diameter_nm / 2
    generated_min_distance = via_diameter_nm + collision_margin_nm

    for candidate in candidates:
        if _overlaps_obstacle(
            candidate.center, via_radius_nm, collision_margin_nm, obstacle_list
        ):
            obstacle_skips += 1
            continue

        if generated_index.overlaps(candidate.center, generated_min_distance):
            generated_skips += 1
            continue

        accepted.append(candidate)
        generated_index.add(candidate.center)

    return CollisionResult(
        accepted=accepted,
        skipped_generated_overlap=generated_skips,
        skipped_obstacle_overlap=obstacle_skips,
    )


def _segment_candidates(
    track: TrackGeometry,
    track_index: int,
    label: str,
    offset_nm: float,
    center_pitch_nm: int,
    config: FenceConfig,
) -> tuple[list[FenceCandidate], list[str]]:
    dx = track.end.x - track.start.x
    dy = track.end.y - track.start.y
    length = math.hypot(dx, dy)
    if length <= EPSILON:
        return [], [f"{label}: zero-length segment"]

    ux = dx / length
    uy = dy / length
    normals = {
        "left": (-uy, ux),
        "right": (uy, -ux),
    }

    candidates: list[FenceCandidate] = []
    for side in selected_sides(config.sides):
        nx, ny = normals[side]
        for distance_nm in _sample_distances(length, center_pitch_nm):
            center = Point(
                track.start.x + ux * distance_nm + nx * offset_nm,
                track.start.y + uy * distance_nm + ny * offset_nm,
            )
            candidates.append(
                FenceCandidate(
                    center=center,
                    side=side,
                    track_index=track_index,
                    track_label=label,
                )
            )

    return candidates, []


def _arc_candidates(
    track: TrackGeometry,
    track_index: int,
    label: str,
    offset_nm: float,
    center_pitch_nm: int,
    config: FenceConfig,
) -> tuple[list[FenceCandidate], list[str]]:
    if track.mid is None:
        return [], [f"{label}: arc is missing midpoint"]

    center = _circle_center(track.start, track.mid, track.end)
    if center is None:
        return _segment_candidates(
            track, track_index, label, offset_nm, center_pitch_nm, config
        )

    base_radius = center.distance_to(track.start)
    if base_radius <= EPSILON:
        return [], [f"{label}: zero-radius arc"]

    start_angle = math.atan2(track.start.y - center.y, track.start.x - center.x)
    mid_angle = math.atan2(track.mid.y - center.y, track.mid.x - center.x)
    end_angle = math.atan2(track.end.y - center.y, track.end.x - center.x)
    signed_sweep = _signed_sweep_through_mid(start_angle, mid_angle, end_angle)
    if abs(signed_sweep) <= EPSILON:
        return [], [f"{label}: zero-angle arc"]

    direction = 1 if signed_sweep > 0 else -1
    side_radius = {
        "left": base_radius - direction * offset_nm,
        "right": base_radius + direction * offset_nm,
    }

    candidates: list[FenceCandidate] = []
    skipped: list[str] = []
    for side in selected_sides(config.sides):
        radius = side_radius[side]
        if radius <= EPSILON:
            skipped.append(f"{label}: {side} fence radius collapses")
            continue

        row_length = abs(signed_sweep) * radius
        for distance_nm in _sample_distances(row_length, center_pitch_nm):
            angle = start_angle + direction * (distance_nm / radius)
            candidates.append(
                FenceCandidate(
                    center=Point(
                        center.x + radius * math.cos(angle),
                        center.y + radius * math.sin(angle),
                    ),
                    side=side,
                    track_index=track_index,
                    track_label=label,
                )
            )

    return candidates, skipped


def _sample_distances(length_nm: float, spacing_nm: int) -> list[float]:
    if length_nm <= EPSILON:
        return []

    distances = [0.0]
    distance = float(spacing_nm)
    while distance < length_nm - EPSILON:
        distances.append(distance)
        distance += spacing_nm

    if not math.isclose(distances[-1], length_nm, abs_tol=1e-3):
        distances.append(length_nm)
    return distances


def _circle_center(a: Point, b: Point, c: Point) -> Point | None:
    determinant = 2 * (
        a.x * (b.y - c.y)
        + b.x * (c.y - a.y)
        + c.x * (a.y - b.y)
    )
    if abs(determinant) <= EPSILON:
        return None

    a_sq = a.x * a.x + a.y * a.y
    b_sq = b.x * b.x + b.y * b.y
    c_sq = c.x * c.x + c.y * c.y
    x = (
        a_sq * (b.y - c.y)
        + b_sq * (c.y - a.y)
        + c_sq * (a.y - b.y)
    ) / determinant
    y = (
        a_sq * (c.x - b.x)
        + b_sq * (a.x - c.x)
        + c_sq * (b.x - a.x)
    ) / determinant
    return Point(x=x, y=y)


def _signed_sweep_through_mid(
    start_angle: float, mid_angle: float, end_angle: float
) -> float:
    ccw_sweep = _ccw_angle_delta(start_angle, end_angle)
    ccw_to_mid = _ccw_angle_delta(start_angle, mid_angle)
    if ccw_to_mid <= ccw_sweep + 1e-9:
        return ccw_sweep
    return -_ccw_angle_delta(end_angle, start_angle)


def _ccw_angle_delta(start_angle: float, end_angle: float) -> float:
    return (end_angle - start_angle) % TAU


def _overlaps_obstacle(
    center: Point,
    via_radius_nm: float,
    collision_margin_nm: int,
    obstacles: list[CircularObstacle],
) -> bool:
    for obstacle in obstacles:
        if obstacle.radius_nm <= 0:
            continue
        min_distance = via_radius_nm + obstacle.radius_nm + collision_margin_nm
        if center.distance_to(obstacle.center) < min_distance - EPSILON:
            return True
    return False


class _SpatialIndex:
    def __init__(self, cell_size_nm: float):
        self.cell_size_nm = cell_size_nm
        self._cells: dict[tuple[int, int], list[Point]] = defaultdict(list)

    def add(self, point: Point) -> None:
        self._cells[self._cell(point)].append(point)

    def overlaps(self, point: Point, min_distance_nm: float) -> bool:
        cell_x, cell_y = self._cell(point)
        for x in range(cell_x - 1, cell_x + 2):
            for y in range(cell_y - 1, cell_y + 2):
                for other in self._cells.get((x, y), []):
                    if point.distance_to(other) < min_distance_nm - EPSILON:
                        return True
        return False

    def _cell(self, point: Point) -> tuple[int, int]:
        return (
            math.floor(point.x / self.cell_size_nm),
            math.floor(point.y / self.cell_size_nm),
        )
