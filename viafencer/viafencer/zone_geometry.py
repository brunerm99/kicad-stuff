"""Geometry helpers for conservative zone via stitching."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .geometry import Point

EPSILON = 1e-6


@dataclass(frozen=True)
class ZonePolygon:
    outline: tuple[Point, ...]
    holes: tuple[tuple[Point, ...], ...] = ()


@dataclass(frozen=True)
class StitchCandidate:
    center: Point


@dataclass(frozen=True)
class StitchGeneration:
    candidates: list[StitchCandidate]
    skipped: list[str]


@dataclass(frozen=True)
class CircleObstacle:
    center: Point
    radius_nm: float
    layers: frozenset[int]
    net_name: str
    label: str = ""


@dataclass(frozen=True)
class SegmentObstacle:
    start: Point
    end: Point
    radius_nm: float
    layers: frozenset[int]
    net_name: str
    label: str = ""


@dataclass(frozen=True)
class PolygonObstacle:
    polygon: ZonePolygon
    layers: frozenset[int]
    net_name: str
    label: str = ""


StitchObstacle = CircleObstacle | SegmentObstacle | PolygonObstacle


@dataclass(frozen=True)
class StitchCollisionResult:
    accepted: list[StitchCandidate]
    skipped_generated_overlap: int = 0
    skipped_obstacle_overlap: int = 0


def generate_stitch_candidates(
    polygons_by_layer: dict[int, list[ZonePolygon]],
    x_spacing_nm: int,
    y_spacing_nm: int,
    via_diameter_nm: int,
    staggered: bool,
) -> StitchGeneration:
    skipped: list[str] = []
    layers = sorted(layer for layer, polygons in polygons_by_layer.items() if polygons)
    if not layers:
        return StitchGeneration([], ["zone has no filled copper polygons"])
    if x_spacing_nm <= 0 or y_spacing_nm <= 0:
        return StitchGeneration([], ["x and y spacing must be greater than zero"])
    if via_diameter_nm <= 0:
        return StitchGeneration([], ["via diameter must be greater than zero"])

    reference_layer = _largest_filled_layer(polygons_by_layer)
    if reference_layer is None:
        return StitchGeneration([], ["zone has no usable filled copper polygons"])

    via_radius_nm = via_diameter_nm / 2
    candidates: list[StitchCandidate] = []
    seen: set[tuple[int, int]] = set()
    for polygon in polygons_by_layer[reference_layer]:
        bounds = polygon_bounds(polygon)
        if bounds is None:
            skipped.append("filled polygon has no outline")
            continue

        min_x, min_y, max_x, max_y = bounds
        y = min_y + via_radius_nm
        row_index = 0
        while y <= max_y - via_radius_nm + EPSILON:
            row_offset = x_spacing_nm / 2 if staggered and row_index % 2 else 0
            x = min_x + via_radius_nm + row_offset
            while x <= max_x - via_radius_nm + EPSILON:
                point = Point(x, y)
                if _point_in_all_layers(point, polygons_by_layer, layers, via_radius_nm):
                    key = (round(point.x), round(point.y))
                    if key not in seen:
                        candidates.append(StitchCandidate(center=point))
                        seen.add(key)
                x += x_spacing_nm
            y += y_spacing_nm
            row_index += 1

    return StitchGeneration(candidates=candidates, skipped=skipped)


def filter_stitch_candidates(
    candidates: Iterable[StitchCandidate],
    via_diameter_nm: int,
    via_layers: Iterable[int],
    target_net_name: str,
    clearance_nm: int,
    obstacles: Iterable[StitchObstacle] = (),
) -> StitchCollisionResult:
    accepted: list[StitchCandidate] = []
    via_radius_nm = via_diameter_nm / 2
    layer_set = frozenset(via_layers)
    obstacle_list = list(obstacles)
    generated_index = _SpatialIndex(max(via_diameter_nm, 1))
    generated_skips = 0
    obstacle_skips = 0

    for candidate in candidates:
        if generated_index.overlaps(candidate.center, via_diameter_nm):
            generated_skips += 1
            continue
        if _overlaps_obstacle(
            candidate.center,
            via_radius_nm,
            layer_set,
            target_net_name,
            clearance_nm,
            obstacle_list,
        ):
            obstacle_skips += 1
            continue

        accepted.append(candidate)
        generated_index.add(candidate.center)

    return StitchCollisionResult(
        accepted=accepted,
        skipped_generated_overlap=generated_skips,
        skipped_obstacle_overlap=obstacle_skips,
    )


def polygon_contains_with_clearance(
    polygon: ZonePolygon, point: Point, clearance_nm: float
) -> bool:
    if len(polygon.outline) < 3:
        return False
    if not point_in_polyline(polygon.outline, point):
        return False
    if _distance_to_closed_polyline(point, polygon.outline) < clearance_nm - EPSILON:
        return False

    for hole in polygon.holes:
        if len(hole) < 3:
            continue
        if point_in_polyline(hole, point):
            return False
        if _distance_to_closed_polyline(point, hole) < clearance_nm - EPSILON:
            return False

    return True


def point_in_polyline(polyline: tuple[Point, ...], point: Point) -> bool:
    inside = False
    count = len(polyline)
    if count < 3:
        return False

    previous = polyline[-1]
    for current in polyline:
        if _distance_to_segment(point, previous, current) <= EPSILON:
            return True
        crosses_y = (current.y > point.y) != (previous.y > point.y)
        if crosses_y:
            x_at_y = (previous.x - current.x) * (point.y - current.y) / (
                previous.y - current.y
            ) + current.x
            if point.x < x_at_y:
                inside = not inside
        previous = current
    return inside


def polygon_bounds(polygon: ZonePolygon) -> tuple[float, float, float, float] | None:
    if not polygon.outline:
        return None
    return (
        min(point.x for point in polygon.outline),
        min(point.y for point in polygon.outline),
        max(point.x for point in polygon.outline),
        max(point.y for point in polygon.outline),
    )


def _largest_filled_layer(polygons_by_layer: dict[int, list[ZonePolygon]]) -> int | None:
    best_layer = None
    best_area = 0.0
    for layer, polygons in polygons_by_layer.items():
        area = sum(abs(_signed_area(polygon.outline)) for polygon in polygons)
        if area > best_area:
            best_area = area
            best_layer = layer
    return best_layer


def _point_in_all_layers(
    point: Point,
    polygons_by_layer: dict[int, list[ZonePolygon]],
    layers: list[int],
    clearance_nm: float,
) -> bool:
    return all(
        any(
            polygon_contains_with_clearance(polygon, point, clearance_nm)
            for polygon in polygons_by_layer[layer]
        )
        for layer in layers
    )


def _overlaps_obstacle(
    point: Point,
    via_radius_nm: float,
    via_layers: frozenset[int],
    target_net_name: str,
    clearance_nm: int,
    obstacles: list[StitchObstacle],
) -> bool:
    for obstacle in obstacles:
        if not via_layers.intersection(obstacle.layers):
            continue

        same_net = obstacle.net_name == target_net_name
        keepout_nm = via_radius_nm + (0 if same_net else clearance_nm)
        if isinstance(obstacle, CircleObstacle):
            min_distance = keepout_nm + obstacle.radius_nm
            if point.distance_to(obstacle.center) < min_distance - EPSILON:
                return True
        elif isinstance(obstacle, SegmentObstacle):
            min_distance = keepout_nm + obstacle.radius_nm
            if _distance_to_segment(point, obstacle.start, obstacle.end) < min_distance - EPSILON:
                return True
        elif not same_net and _polygon_obstacle_overlap(
            obstacle.polygon, point, keepout_nm
        ):
            return True

    return False


def _polygon_obstacle_overlap(
    polygon: ZonePolygon, point: Point, keepout_nm: float
) -> bool:
    if point_in_polyline(polygon.outline, point):
        return True
    if _distance_to_closed_polyline(point, polygon.outline) < keepout_nm - EPSILON:
        return True
    for hole in polygon.holes:
        if _distance_to_closed_polyline(point, hole) < keepout_nm - EPSILON:
            return True
    return False


def _distance_to_closed_polyline(point: Point, polyline: tuple[Point, ...]) -> float:
    if len(polyline) < 2:
        return math.inf
    distance = math.inf
    previous = polyline[-1]
    for current in polyline:
        distance = min(distance, _distance_to_segment(point, previous, current))
        previous = current
    return distance


def _distance_to_segment(point: Point, start: Point, end: Point) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    length_sq = dx * dx + dy * dy
    if length_sq <= EPSILON:
        return point.distance_to(start)

    t = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    projection = Point(start.x + t * dx, start.y + t * dy)
    return point.distance_to(projection)


def _signed_area(points: tuple[Point, ...]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    previous = points[-1]
    for current in points:
        area += previous.x * current.y - current.x * previous.y
        previous = current
    return area / 2


class _SpatialIndex:
    def __init__(self, cell_size: float):
        self.cell_size = max(cell_size, 1.0)
        self.cells: dict[tuple[int, int], list[Point]] = defaultdict(list)

    def add(self, point: Point) -> None:
        self.cells[self._cell(point)].append(point)

    def overlaps(self, point: Point, min_distance_nm: float) -> bool:
        cx, cy = self._cell(point)
        radius = math.ceil(min_distance_nm / self.cell_size)
        for ix in range(cx - radius, cx + radius + 1):
            for iy in range(cy - radius, cy + radius + 1):
                for existing in self.cells.get((ix, iy), []):
                    if point.distance_to(existing) < min_distance_nm - EPSILON:
                        return True
        return False

    def _cell(self, point: Point) -> tuple[int, int]:
        return (
            math.floor(point.x / self.cell_size),
            math.floor(point.y / self.cell_size),
        )
