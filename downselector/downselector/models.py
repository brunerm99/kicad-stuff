"""Pure-Python data model and filters for route-item downselection."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

ALL = "All"
UNKNOWN = "Unknown"
NM_PER_MM = 1_000_000
NM_PER_MIL = 25_400
SIZE_MATCH_TOLERANCE_NM = 1_000

ItemKind = Literal["track", "via"]
SizeUnit = Literal["mm", "mil"]


@dataclass(frozen=True)
class RouteItemInfo:
    """Normalized IPC board item metadata used by the UI filters."""

    id_text: str
    kind: ItemKind
    shape: str
    net: str
    net_class: str
    locked: bool
    source: Any = field(default=None, compare=False, repr=False)
    track_width_nm: int | None = None
    track_layer: str | None = None
    via_diameter_nm: int | None = None
    via_drill_nm: int | None = None
    via_layer_pair: str | None = None
    via_type: str | None = None

    @property
    def kind_label(self) -> str:
        return "Track" if self.kind == "track" else "Via"

    @property
    def locked_label(self) -> str:
        return "Locked" if self.locked else "Unlocked"

    @property
    def track_width_label(self) -> str:
        return format_nm_as_mm(self.track_width_nm)

    @property
    def via_diameter_label(self) -> str:
        return format_nm_as_mm(self.via_diameter_nm)

    @property
    def via_drill_label(self) -> str:
        return format_nm_as_mm(self.via_drill_nm)

    @property
    def layer_label(self) -> str:
        if self.kind == "track":
            return self.track_layer or UNKNOWN
        return self.via_layer_pair or UNKNOWN


@dataclass(frozen=True)
class FilterCriteria:
    """User-selected route filters.

    Type-specific filters are restrictive. For example, a via drill filter
    excludes tracks because tracks cannot satisfy that criterion.
    """

    item_kind: Literal["all", "track", "via"] = "all"
    track_width: str = ALL
    track_layer: str = ALL
    via_diameter: str = ALL
    via_drill: str = ALL
    via_layer_pair: str = ALL
    via_type: str = ALL
    net_class: str = ALL
    net: str = ALL
    locked: str = ALL


def format_nm_as_mm(value_nm: int | None) -> str:
    """Format KiCad nanometer board units as a stable millimeter label."""

    if value_nm is None:
        return "-"
    return f"{value_nm / NM_PER_MM:.3f} mm"


def format_nm_as_mil(value_nm: int | None) -> str:
    """Format KiCad nanometer board units as a stable mil label."""

    if value_nm is None:
        return "-"
    return f"{value_nm / NM_PER_MIL:.2f} mil"


def format_nm_as_unit(value_nm: int | None, unit: SizeUnit) -> str:
    """Format KiCad nanometer board units in the selected UI unit."""

    if unit == "mil":
        return format_nm_as_mil(value_nm)
    return format_nm_as_mm(value_nm)


def parse_size_to_nm(value: str) -> int | None:
    """Parse a user size filter entered in millimeters or mils.

    Unitless values are treated as millimeters because KiCad's UI commonly
    displays metric sizes that way. Accepted imperial suffixes are `mil`,
    `mils`, and `thou`.
    """

    text = value.strip().lower()
    if not text or text == ALL.lower() or text == "-":
        return None

    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([a-z]*)", text)
    if not match:
        return None

    number = float(match.group(1))
    unit = match.group(2) or "mm"
    if unit in {"mm", "millimeter", "millimeters"}:
        scale = NM_PER_MM
    elif unit in {"mil", "mils", "thou", "thousandth"}:
        scale = NM_PER_MIL
    else:
        return None

    return round(number * scale)


def filter_route_items(
    items: Iterable[RouteItemInfo], criteria: FilterCriteria
) -> list[RouteItemInfo]:
    """Return items matching all active filters."""

    result: list[RouteItemInfo] = []
    track_width_active = _filter_active(criteria.track_width)
    via_diameter_active = _filter_active(criteria.via_diameter)
    via_drill_active = _filter_active(criteria.via_drill)
    track_width_nm = parse_size_to_nm(criteria.track_width)
    via_diameter_nm = parse_size_to_nm(criteria.via_diameter)
    via_drill_nm = parse_size_to_nm(criteria.via_drill)

    track_filter_active = track_width_active or criteria.track_layer != ALL
    via_filter_active = (
        via_diameter_active
        or via_drill_active
        or criteria.via_layer_pair != ALL
        or criteria.via_type != ALL
    )

    for item in items:
        if criteria.item_kind != "all" and item.kind != criteria.item_kind:
            continue

        if track_filter_active and item.kind != "track":
            continue
        if via_filter_active and item.kind != "via":
            continue

        if item.kind == "track":
            if track_width_active and not _size_matches(
                item.track_width_nm, track_width_nm
            ):
                continue
            if criteria.track_layer != ALL and item.track_layer != criteria.track_layer:
                continue

        if item.kind == "via":
            if via_diameter_active and not _size_matches(
                item.via_diameter_nm, via_diameter_nm
            ):
                continue
            if via_drill_active and not _size_matches(item.via_drill_nm, via_drill_nm):
                continue
            if criteria.via_layer_pair != ALL and item.via_layer_pair != criteria.via_layer_pair:
                continue
            if criteria.via_type != ALL and item.via_type != criteria.via_type:
                continue

        if criteria.net_class != ALL and item.net_class != criteria.net_class:
            continue
        if criteria.net != ALL and item.net != criteria.net:
            continue
        if criteria.locked != ALL and item.locked_label != criteria.locked:
            continue

        result.append(item)

    return result


def variation_rows(
    items: Iterable[RouteItemInfo], size_unit: SizeUnit = "mm"
) -> list[dict[str, str | int]]:
    """Group selected items by the fields that the UI can filter."""

    counter: Counter[tuple[str, str, str, str, str, str, str, str, str]] = Counter()
    for item in items:
        counter[
            (
                item.kind_label,
                format_nm_as_unit(item.track_width_nm, size_unit),
                format_nm_as_unit(item.via_diameter_nm, size_unit),
                format_nm_as_unit(item.via_drill_nm, size_unit),
                item.layer_label,
                item.via_type or "-",
                item.net_class,
                item.net,
                item.locked_label,
            )
        ] += 1

    rows: list[dict[str, str | int]] = []
    for (
        kind,
        track_width,
        via_diameter,
        via_drill,
        layer,
        via_type,
        net_class,
        net,
        locked,
    ), count in counter.items():
        rows.append(
            {
                "count": count,
                "kind": kind,
                "track_width": track_width,
                "via_diameter": via_diameter,
                "via_drill": via_drill,
                "layer": layer,
                "via_type": via_type,
                "net_class": net_class,
                "net": net,
                "locked": locked,
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            0 if row["kind"] == "Track" else 1,
            _natural_sort_key(str(row["track_width"])),
            _natural_sort_key(str(row["via_diameter"])),
            _natural_sort_key(str(row["via_drill"])),
            str(row["layer"]),
            str(row["via_type"]),
            str(row["net_class"]),
            str(row["net"]),
        ),
    )


def option_values(items: Iterable[RouteItemInfo], attribute: str) -> list[str]:
    """Return sorted unique filter values for a RouteItemInfo label attribute."""

    values = {
        str(getattr(item, attribute))
        for item in items
        if getattr(item, attribute) not in (None, "", "-")
    }
    return sorted(values, key=_natural_sort_key)


def size_option_values(
    items: Iterable[RouteItemInfo], attribute: str, size_unit: SizeUnit = "mm"
) -> list[str]:
    """Return unique size values in the selected UI unit."""

    sizes_nm = sorted(
        {
            value
            for item in items
            if (value := getattr(item, attribute)) is not None
        }
    )
    return [format_nm_as_unit(value_nm, size_unit) for value_nm in sizes_nm]


def summarize_items(items: Iterable[RouteItemInfo]) -> str:
    """Short status text for a refreshed KiCad selection."""

    counts = Counter(item.kind for item in items)
    total = sum(counts.values())
    return f"{total} route items: {counts['track']} tracks, {counts['via']} vias"


def _natural_sort_key(value: str) -> tuple[float, str]:
    try:
        number_text = value.split()[0]
        return (float(number_text), value)
    except (ValueError, IndexError):
        return (float("inf"), value)


def _filter_active(value: str) -> bool:
    return bool(value.strip()) and value.strip().lower() != ALL.lower()


def _size_matches(value_nm: int | None, target_nm: int | None) -> bool:
    if value_nm is None or target_nm is None:
        return False
    return abs(value_nm - target_nm) <= SIZE_MATCH_TOLERANCE_NM
