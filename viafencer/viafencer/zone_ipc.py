"""KiCad IPC adapter for zone via stitching."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from .geometry import Point
from .kicad_ipc import (
    NO_NET,
    KiCadFenceSession,
    _id_text,
    _int_or_none,
    _is_arc_item,
    _is_copper_layer,
    _is_track_item,
    _net_name,
    _pad_radius_nm,
    _point_from_vector,
)
from .models import NM_PER_MM
from .zone_geometry import CircleObstacle, PolygonObstacle, SegmentObstacle, ZonePolygon

LOGGER = logging.getLogger(__name__)
DEFAULT_VIA_DIAMETER_NM = 600_000
DEFAULT_VIA_DRILL_NM = 300_000
DEFAULT_CLEARANCE_NM = 200_000


@dataclass(frozen=True)
class StitchZone:
    item_id: str
    label: str
    net_name: str
    layers: tuple[int, ...]
    via_layers: tuple[int, ...]
    polygons_by_layer: dict[int, list[ZonePolygon]]
    via_diameter_nm: int
    drill_diameter_nm: int
    clearance_nm: int


class ZoneStitchSession(KiCadFenceSession):
    """KiCad session with zone-stitching helpers."""

    def selected_stitch_zones(self) -> list[StitchZone]:
        LOGGER.info("Requesting selected zones from KiCad")
        selected = self._get_zone_selection()
        zones: list[StitchZone] = []
        for index, item in enumerate(selected):
            zone = self._to_stitch_zone(item, index)
            if zone is not None:
                zones.append(zone)
        LOGGER.info("Read %d selected stitchable zones", len(zones))
        return zones

    def stitch_obstacles(self, selected_zone: StitchZone) -> list[Any]:
        LOGGER.info("Building stitch collision obstacles")
        obstacles: list[Any] = []
        target_layers = frozenset(selected_zone.via_layers)

        for via in self.board.get_vias():
            layers = _padstack_span_layers(getattr(via, "padstack", None), target_layers)
            if not layers:
                continue
            diameter_nm = _int_or_none(getattr(via, "diameter", None))
            if diameter_nm is None:
                continue
            obstacles.append(
                CircleObstacle(
                    center=_point_from_vector(getattr(via, "position", None)),
                    radius_nm=diameter_nm / 2,
                    layers=frozenset(layers),
                    net_name=_net_name(getattr(via, "net", None)),
                    label="via",
                )
            )

        for pad in self.board.get_pads():
            layers = _pad_layers(pad, target_layers)
            if not layers:
                continue
            radius_nm = _pad_radius_nm(pad)
            if radius_nm <= 0:
                continue
            obstacles.append(
                CircleObstacle(
                    center=_point_from_vector(getattr(pad, "position", None)),
                    radius_nm=radius_nm,
                    layers=frozenset(layers),
                    net_name=_net_name(getattr(pad, "net", None)),
                    label="pad",
                )
            )

        for item in self.board.get_tracks():
            layer = _int_or_none(getattr(item, "layer", None))
            if layer not in target_layers:
                continue
            width_nm = _int_or_none(getattr(item, "width", None)) or 0
            if width_nm <= 0:
                continue
            for start, end in _track_segments(item):
                obstacles.append(
                    SegmentObstacle(
                        start=start,
                        end=end,
                        radius_nm=width_nm / 2,
                        layers=frozenset({layer}),
                        net_name=_net_name(getattr(item, "net", None)),
                        label="track",
                    )
                )

        for zone in self.board.get_zones():
            zone_id = _id_text(getattr(zone, "id", ""))
            if zone_id == selected_zone.item_id or _is_rule_area(zone):
                continue
            net_name = _net_name(getattr(zone, "net", None))
            if net_name == selected_zone.net_name:
                continue
            for layer, polygons in _zone_polygons_by_layer(zone).items():
                if layer not in target_layers:
                    continue
                for polygon in polygons:
                    obstacles.append(
                        PolygonObstacle(
                            polygon=polygon,
                            layers=frozenset({layer}),
                            net_name=net_name,
                            label="zone",
                        )
                    )

        LOGGER.info("Built %d stitch collision obstacles", len(obstacles))
        return obstacles

    def create_stitch_vias(
        self,
        centers: list[Point],
        zone: StitchZone,
    ):
        if not zone.layers:
            raise RuntimeError("Selected zone has no filled copper layers.")
        return self.create_vias(
            centers,
            zone.net_name,
            zone.via_diameter_nm,
            zone.drill_diameter_nm,
            start_layer=zone.layers[0],
            end_layer=zone.layers[-1],
            grouped=False,
            via_commit_message="Create zone stitch vias",
        )

    def save_board_copy(self, path: str) -> None:
        self.board.save_as(path, overwrite=True, include_project=True)

    def _get_zone_selection(self) -> list[Any]:
        try:
            from kipy.board import KiCadObjectType

            return list(self.board.get_selection(types=[KiCadObjectType.KOT_PCB_ZONE]))
        except Exception:
            return [
                item
                for item in self.board.get_selection()
                if item.__class__.__name__.lower() == "zone"
            ]

    def _to_stitch_zone(self, zone: Any, index: int) -> StitchZone | None:
        if _is_rule_area(zone):
            return None

        net_name = _net_name(getattr(zone, "net", None))
        if not net_name or net_name == NO_NET:
            return None

        polygons_by_layer = _zone_polygons_by_layer(zone)
        layers = tuple(sorted(layer for layer, polygons in polygons_by_layer.items() if polygons))
        enabled_layers = tuple(option.layer for option in self.copper_layer_options())
        via_layers = tuple(
            layer
            for layer in enabled_layers
            if layers and min(layers) <= layer <= max(layers)
        )
        via_diameter_nm, drill_diameter_nm, clearance_nm = self._zone_rule_sizes(zone)
        label = getattr(zone, "name", "") or f"{net_name} zone {index + 1}"
        return StitchZone(
            item_id=_id_text(getattr(zone, "id", "")),
            label=label,
            net_name=net_name,
            layers=layers,
            via_layers=via_layers,
            polygons_by_layer=polygons_by_layer,
            via_diameter_nm=via_diameter_nm,
            drill_diameter_nm=drill_diameter_nm,
            clearance_nm=clearance_nm,
        )

    def _zone_rule_sizes(self, zone: Any) -> tuple[int, int, int]:
        via_diameter_nm = DEFAULT_VIA_DIAMETER_NM
        drill_diameter_nm = DEFAULT_VIA_DRILL_NM
        clearance_nm = DEFAULT_CLEARANCE_NM
        net = getattr(zone, "net", None)
        if net is not None:
            try:
                net_classes = self.board.get_netclass_for_nets(net)
                net_class = net_classes.get(_net_name(net))
            except Exception:
                LOGGER.exception("Could not read net class for zone net")
                net_class = None
            if net_class is not None:
                via_diameter_nm = net_class.via_diameter or via_diameter_nm
                drill_diameter_nm = net_class.via_drill or drill_diameter_nm
                clearance_nm = net_class.clearance or clearance_nm

        zone_clearance = _int_or_none(getattr(zone, "clearance", None))
        if zone_clearance and zone_clearance > 0:
            clearance_nm = zone_clearance
        return via_diameter_nm, drill_diameter_nm, clearance_nm


def _zone_polygons_by_layer(zone: Any) -> dict[int, list[ZonePolygon]]:
    polygons_by_layer: dict[int, list[ZonePolygon]] = {}
    filled = getattr(zone, "filled_polygons", {}) or {}
    for layer, polygons in filled.items():
        layer_int = _int_or_none(layer)
        if layer_int is None or not _is_copper_layer(layer_int):
            continue
        converted = [_to_zone_polygon(polygon) for polygon in polygons]
        polygons_by_layer[layer_int] = [polygon for polygon in converted if polygon is not None]
    return polygons_by_layer


def _to_zone_polygon(polygon: Any) -> ZonePolygon | None:
    outline = _flatten_polyline(getattr(polygon, "outline", None))
    if len(outline) < 3:
        return None
    holes = tuple(
        tuple(points)
        for points in (_flatten_polyline(hole) for hole in getattr(polygon, "holes", []))
        if len(points) >= 3
    )
    return ZonePolygon(outline=tuple(outline), holes=holes)


def _flatten_polyline(polyline: Any) -> list[Point]:
    if polyline is None:
        return []
    points: list[Point] = []
    for node in polyline:
        if getattr(node, "has_point", False):
            points.append(_point_from_vector(node.point))
        elif getattr(node, "has_arc", False):
            arc_points = _arc_points(
                _point_from_vector(node.arc.start),
                _point_from_vector(node.arc.mid),
                _point_from_vector(node.arc.end),
            )
            if points and arc_points and points[-1].distance_to(arc_points[0]) < 1:
                points.extend(arc_points[1:])
            else:
                points.extend(arc_points)
    if len(points) > 1 and points[0].distance_to(points[-1]) < 1:
        points.pop()
    return points


def _track_segments(item: Any) -> list[tuple[Point, Point]]:
    if not _is_track_item(item):
        return []
    if _is_arc_item(item):
        points = _arc_points(
            _point_from_vector(getattr(item, "start", None)),
            _point_from_vector(getattr(item, "mid", None)),
            _point_from_vector(getattr(item, "end", None)),
        )
        return list(zip(points, points[1:]))
    return [
        (
            _point_from_vector(getattr(item, "start", None)),
            _point_from_vector(getattr(item, "end", None)),
        )
    ]


def _arc_points(start: Point, mid: Point, end: Point) -> list[Point]:
    center = _circle_center(start, mid, end)
    if center is None:
        return [start, end]
    radius = center.distance_to(start)
    if radius <= 0:
        return [start, end]

    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    mid_angle = math.atan2(mid.y - center.y, mid.x - center.x)
    end_angle = math.atan2(end.y - center.y, end.x - center.x)
    sweep = _signed_sweep_through_mid(start_angle, mid_angle, end_angle)
    segment_count = max(8, math.ceil(abs(sweep) / (math.pi / 18)))
    return [
        Point(
            center.x + radius * math.cos(start_angle + sweep * index / segment_count),
            center.y + radius * math.sin(start_angle + sweep * index / segment_count),
        )
        for index in range(segment_count + 1)
    ]


def _circle_center(a: Point, b: Point, c: Point) -> Point | None:
    determinant = 2 * (
        a.x * (b.y - c.y)
        + b.x * (c.y - a.y)
        + c.x * (a.y - b.y)
    )
    if abs(determinant) <= 1e-6:
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
    ccw = (end - start) % (2 * math.pi)
    mid_ccw = (mid - start) % (2 * math.pi)
    if 0 <= mid_ccw <= ccw:
        return ccw
    return -((start - end) % (2 * math.pi))


def _span_layers(layers: tuple[int, ...] | list[int]) -> list[int]:
    if not layers:
        return []
    start = min(layers)
    end = max(layers)
    return [layer for layer in range(start, end + 1) if _is_copper_layer(layer)]


def _pad_layers(pad: Any, target_layers: frozenset[int]) -> list[int]:
    padstack = getattr(pad, "padstack", None)
    layers = _padstack_span_layers(padstack, target_layers)
    if layers:
        return layers

    copper_layers = getattr(padstack, "copper_layers", []) if padstack else []
    layer_values = []
    for copper_layer in copper_layers:
        layer = _int_or_none(getattr(copper_layer, "layer", None))
        if layer is not None and _is_copper_layer(layer):
            layer_values.append(layer)
    return sorted(set(layer_values))


def _padstack_span_layers(padstack: Any, target_layers: frozenset[int]) -> list[int]:
    drill = getattr(padstack, "drill", None) if padstack else None
    diameter = getattr(drill, "diameter", None)
    drill_x = _int_or_none(getattr(diameter, "x", None))
    if drill_x is None or drill_x <= 0:
        return []

    start_layer = _int_or_none(getattr(drill, "start_layer", None))
    end_layer = _int_or_none(getattr(drill, "end_layer", None))
    if start_layer is None or end_layer is None:
        return sorted(target_layers)
    return _span_layers([start_layer, end_layer])


def _is_rule_area(zone: Any) -> bool:
    try:
        return bool(zone.is_rule_area())
    except Exception:
        return False


def format_nm(value_nm: int) -> str:
    return f"{value_nm / NM_PER_MM:.3f} mm"
