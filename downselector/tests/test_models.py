from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from downselector.models import (  # noqa: E402
    ALL,
    FilterCriteria,
    RouteItemInfo,
    filter_route_items,
    parse_size_to_nm,
    size_option_values,
    variation_rows,
)


def _items() -> list[RouteItemInfo]:
    return [
        RouteItemInfo(
            id_text="t1",
            kind="track",
            shape="segment",
            net="+3V3",
            net_class="Power",
            locked=False,
            track_width_nm=250_000,
            track_layer="F.Cu",
        ),
        RouteItemInfo(
            id_text="t2",
            kind="track",
            shape="segment",
            net="SCL",
            net_class="Signal",
            locked=False,
            track_width_nm=150_000,
            track_layer="B.Cu",
        ),
        RouteItemInfo(
            id_text="v1",
            kind="via",
            shape="via",
            net="+3V3",
            net_class="Power",
            locked=True,
            via_diameter_nm=600_000,
            via_drill_nm=300_000,
            via_layer_pair="F.Cu -> B.Cu",
            via_type="VIA_THROUGH",
        ),
        RouteItemInfo(
            id_text="v2",
            kind="via",
            shape="via",
            net="SCL",
            net_class="Signal",
            locked=False,
            via_diameter_nm=450_000,
            via_drill_nm=200_000,
            via_layer_pair="F.Cu -> In1.Cu",
            via_type="VIA_BLIND_BURIED",
        ),
    ]


def test_filters_tracks_by_width_and_layer() -> None:
    matches = filter_route_items(
        _items(),
        FilterCriteria(item_kind="track", track_width="0.250 mm", track_layer="F.Cu"),
    )

    assert [item.id_text for item in matches] == ["t1"]


def test_filters_tracks_by_width_in_mils() -> None:
    matches = filter_route_items(
        _items(),
        FilterCriteria(item_kind="track", track_width="9.84 mil", track_layer="F.Cu"),
    )

    assert [item.id_text for item in matches] == ["t1"]


def test_via_drill_filter_excludes_tracks_when_type_is_all() -> None:
    matches = filter_route_items(
        _items(),
        FilterCriteria(item_kind="all", via_drill="0.300 mm"),
    )

    assert [item.id_text for item in matches] == ["v1"]


def test_via_drill_filter_accepts_mils() -> None:
    matches = filter_route_items(
        _items(),
        FilterCriteria(item_kind="all", via_drill="11.81 mils"),
    )

    assert [item.id_text for item in matches] == ["v1"]


def test_invalid_size_filter_matches_nothing() -> None:
    matches = filter_route_items(
        _items(),
        FilterCriteria(item_kind="track", track_width="wide"),
    )

    assert matches == []


def test_filters_by_net_class_and_locked_state() -> None:
    matches = filter_route_items(
        _items(),
        FilterCriteria(net_class="Power", locked="Locked"),
    )

    assert [item.id_text for item in matches] == ["v1"]


def test_all_filter_keeps_everything() -> None:
    matches = filter_route_items(_items(), FilterCriteria(net=ALL))

    assert len(matches) == 4


def test_size_option_values_use_selected_unit() -> None:
    mm_values = size_option_values(
        (item for item in _items() if item.kind == "track"), "track_width_nm"
    )
    mil_values = size_option_values(
        (item for item in _items() if item.kind == "track"), "track_width_nm", "mil"
    )

    assert "0.250 mm" in mm_values
    assert "9.84 mil" not in mm_values
    assert "9.84 mil" in mil_values
    assert "0.250 mm" not in mil_values


def test_parse_size_to_nm_defaults_to_mm() -> None:
    assert parse_size_to_nm("0.254") == 254_000
    assert parse_size_to_nm("10 mil") == 254_000


def test_variation_rows_group_identical_combinations() -> None:
    items = _items()
    items.append(
        RouteItemInfo(
            id_text="v3",
            kind="via",
            shape="via",
            net="+3V3",
            net_class="Power",
            locked=True,
            via_diameter_nm=600_000,
            via_drill_nm=300_000,
            via_layer_pair="F.Cu -> B.Cu",
            via_type="VIA_THROUGH",
        )
    )

    rows = variation_rows(items)

    power_via = next(
        row for row in rows if row["kind"] == "Via" and row["net"] == "+3V3"
    )
    assert power_via["count"] == 2


def test_variation_rows_use_selected_unit() -> None:
    rows = variation_rows(_items(), size_unit="mil")

    track = next(row for row in rows if row["kind"] == "Track" and row["net"] == "+3V3")
    assert track["track_width"] == "9.84 mil"
