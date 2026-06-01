from __future__ import annotations

from dataclasses import dataclass

from viafencer.geometry import Point
from viafencer.mask_geometry import MaskOpening
from viafencer.mask_ipc import (
    AUTO_MASK_LAYER,
    BACK_MASK_LAYER,
    FRONT_MASK_LAYER,
    MaskZoneSession,
    _mask_zone_as_board_string,
    resolve_mask_layer,
)


def test_resolve_mask_layer_maps_outer_copper_in_auto_mode() -> None:
    assert resolve_mask_layer(3, AUTO_MASK_LAYER) == FRONT_MASK_LAYER
    assert resolve_mask_layer(34, AUTO_MASK_LAYER) == BACK_MASK_LAYER
    assert resolve_mask_layer(4, AUTO_MASK_LAYER) is None
    assert resolve_mask_layer(4, FRONT_MASK_LAYER) == FRONT_MASK_LAYER
    assert resolve_mask_layer(4, BACK_MASK_LAYER) == BACK_MASK_LAYER


def test_mask_zone_as_board_string_uses_graphical_zone_syntax() -> None:
    text = _mask_zone_as_board_string(
        "zone-1",
        FRONT_MASK_LAYER,
        (
            Point(0, 0),
            Point(1_000_000, 0),
            Point(1_000_000, 500_000),
            Point(0, 500_000),
        ),
        'RF "mask"',
    )

    assert text.startswith("(zone\n")
    assert '(name "RF \\"mask\\"")' in text
    assert '(layer "F.Mask")' in text
    assert '(uuid "zone-1")' in text
    assert "(fill yes" in text
    assert "(xy 1 0.5)" in text
    assert "(net " not in text


@dataclass
class _Id:
    value: str


@dataclass
class _Zone:
    id: _Id


@dataclass
class _Group:
    id: _Id


class _ZoneBoard:
    def __init__(self) -> None:
        self.zones = [_Zone(_Id("existing-zone"))]
        self.groups: list[_Group] = []
        self.selected: list[object] = []
        self.group_text = ""

    def get_zones(self) -> list[_Zone]:
        return self.zones

    def get_groups(self) -> list[_Group]:
        return self.groups

    def refill_zones(self, **_kwargs: object) -> None:
        return None

    def clear_selection(self) -> None:
        self.selected = []

    def add_to_selection(self, items: list[object]) -> None:
        self.selected.extend(items)


def test_create_mask_zones_groups_actual_ids_when_kicad_reassigns_ids(monkeypatch) -> None:
    board = _ZoneBoard()
    session = object.__new__(MaskZoneSession)
    session.board = board
    ids = iter(["planned-1", "planned-2", "group-1"])
    actual_ids = iter(["actual-1", "actual-2"])
    monkeypatch.setattr("viafencer.mask_ipc.uuid.uuid4", lambda: next(ids))

    def parse_items(_board: object, contents: str) -> None:
        if contents.startswith("(group"):
            board.group_text = contents
            board.groups = [_Group(_Id("group-1"))]
        else:
            for _index in range(contents.count("(zone\n")):
                board.zones.append(_Zone(_Id(next(actual_ids))))

    monkeypatch.setattr("viafencer.mask_ipc._parse_and_create_items_from_string", parse_items)

    summary = session.create_mask_zones(
        [
            MaskOpening("a", 3, (Point(0, 0), Point(1, 0), Point(1, 1))),
            MaskOpening("b", 3, (Point(2, 0), Point(3, 0), Point(3, 1))),
        ],
        grouped=True,
    )

    assert summary.zone_count == 2
    assert summary.grouped is True
    assert '"actual-1"' in board.group_text
    assert '"actual-2"' in board.group_text
    assert '"planned-1"' not in board.group_text
    assert board.selected == board.groups
