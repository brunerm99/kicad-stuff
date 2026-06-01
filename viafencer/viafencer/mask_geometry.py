"""Pure geometry for RF solder-mask opening zones."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .geometry import Point, TrackGeometry

EPSILON = 1e-6
TAU = 2 * math.pi
ARC_STEP_RADIANS = math.radians(5)


@dataclass(frozen=True)
class MaskOpening:
    source_label: str
    source_layer: int | None
    polygon: tuple[Point, ...]


@dataclass(frozen=True)
class MaskGeneration:
    openings: list[MaskOpening]
    skipped: list[str]


def generate_mask_openings(
    tracks: Iterable[TrackGeometry],
    side_offset_nm: int,
) -> MaskGeneration:
    """Create side-offset mask-opening polygons for selected tracks.

    External track ends use square caps with no longitudinal growth. Endpoints
    shared by selected tracks receive a small overlap to prevent gaps at corners.
    """

    track_list = list(tracks)
    endpoint_counts = _endpoint_counts(track_list)
    openings: list[MaskOpening] = []
    skipped: list[str] = []

    if side_offset_nm < 0:
        return MaskGeneration([], ["trace-edge offset must be zero or greater"])

    for index, track in enumerate(track_list):
        label = track.label or f"track {index + 1}"
        if track.width_nm <= 0:
            skipped.append(f"{label}: missing or invalid track width")
            continue

        half_extent_nm = track.width_nm / 2 + side_offset_nm
        start_join = endpoint_counts[_endpoint_key(track.start)] > 1
        end_join = endpoint_counts[_endpoint_key(track.end)] > 1

        if track.kind == "arc":
            polygon, reason = _arc_polygon(
                track,
                half_extent_nm,
                start_extension_nm=half_extent_nm if start_join else 0,
                end_extension_nm=half_extent_nm if end_join else 0,
            )
        else:
            polygon, reason = _segment_polygon(
                track,
                half_extent_nm,
                start_extension_nm=half_extent_nm if start_join else 0,
                end_extension_nm=half_extent_nm if end_join else 0,
            )

        if polygon is None:
            skipped.append(f"{label}: {reason}")
            continue

        openings.append(
            MaskOpening(
                source_label=label,
                source_layer=track.layer,
                polygon=polygon,
            )
        )

    return MaskGeneration(openings=openings, skipped=skipped)


def _segment_polygon(
    track: TrackGeometry,
    half_extent_nm: float,
    start_extension_nm: float,
    end_extension_nm: float,
) -> tuple[tuple[Point, ...] | None, str | None]:
    dx = track.end.x - track.start.x
    dy = track.end.y - track.start.y
    length = math.hypot(dx, dy)
    if length <= EPSILON:
        return None, "zero-length segment"

    ux = dx / length
    uy = dy / length
    nx = -uy
    ny = ux

    start = Point(
        track.start.x - ux * start_extension_nm,
        track.start.y - uy * start_extension_nm,
    )
    end = Point(
        track.end.x + ux * end_extension_nm,
        track.end.y + uy * end_extension_nm,
    )

    return (
        (
            Point(start.x + nx * half_extent_nm, start.y + ny * half_extent_nm),
            Point(end.x + nx * half_extent_nm, end.y + ny * half_extent_nm),
            Point(end.x - nx * half_extent_nm, end.y - ny * half_extent_nm),
            Point(start.x - nx * half_extent_nm, start.y - ny * half_extent_nm),
        ),
        None,
    )


def _arc_polygon(
    track: TrackGeometry,
    half_extent_nm: float,
    start_extension_nm: float,
    end_extension_nm: float,
) -> tuple[tuple[Point, ...] | None, str | None]:
    if track.mid is None:
        return None, "arc is missing midpoint"

    center = _circle_center(track.start, track.mid, track.end)
    if center is None:
        return _segment_polygon(
            track,
            half_extent_nm,
            start_extension_nm=start_extension_nm,
            end_extension_nm=end_extension_nm,
        )

    radius = center.distance_to(track.start)
    if radius <= EPSILON:
        return None, "zero-radius arc"

    inner_radius = radius - half_extent_nm
    outer_radius = radius + half_extent_nm
    if inner_radius <= EPSILON:
        return None, "mask opening radius collapses"

    start_angle = math.atan2(track.start.y - center.y, track.start.x - center.x)
    mid_angle = math.atan2(track.mid.y - center.y, track.mid.x - center.x)
    end_angle = math.atan2(track.end.y - center.y, track.end.x - center.x)
    signed_sweep = _signed_sweep_through_mid(start_angle, mid_angle, end_angle)
    if abs(signed_sweep) <= EPSILON:
        return None, "zero-angle arc"

    direction = 1 if signed_sweep > 0 else -1
    adjusted_start = start_angle - direction * (start_extension_nm / radius)
    adjusted_end = start_angle + signed_sweep + direction * (end_extension_nm / radius)
    adjusted_sweep = adjusted_end - adjusted_start

    angles = _sample_angles(adjusted_start, adjusted_sweep)
    outer = [_polar(center, outer_radius, angle) for angle in angles]
    inner = [_polar(center, inner_radius, angle) for angle in reversed(angles)]
    return tuple(outer + inner), None


def _endpoint_counts(tracks: Iterable[TrackGeometry]) -> Counter[tuple[int, int]]:
    counts: Counter[tuple[int, int]] = Counter()
    for track in tracks:
        counts[_endpoint_key(track.start)] += 1
        counts[_endpoint_key(track.end)] += 1
    return counts


def _endpoint_key(point: Point) -> tuple[int, int]:
    return round(point.x), round(point.y)


def _sample_angles(start_angle: float, signed_sweep: float) -> list[float]:
    segment_count = max(1, math.ceil(abs(signed_sweep) / ARC_STEP_RADIANS))
    return [
        start_angle + signed_sweep * index / segment_count
        for index in range(segment_count + 1)
    ]


def _polar(center: Point, radius: float, angle: float) -> Point:
    return Point(
        center.x + radius * math.cos(angle),
        center.y + radius * math.sin(angle),
    )


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
    return Point(
        (
            a_sq * (b.y - c.y)
            + b_sq * (c.y - a.y)
            + c_sq * (a.y - b.y)
        )
        / determinant,
        (
            a_sq * (c.x - b.x)
            + b_sq * (a.x - c.x)
            + c_sq * (b.x - a.x)
        )
        / determinant,
    )


def _signed_sweep_through_mid(start: float, mid: float, end: float) -> float:
    ccw_sweep = _ccw_angle_delta(start, end)
    ccw_to_mid = _ccw_angle_delta(start, mid)
    if ccw_to_mid <= ccw_sweep + 1e-9:
        return ccw_sweep
    return -_ccw_angle_delta(end, start)


def _ccw_angle_delta(start: float, end: float) -> float:
    return (end - start) % TAU
