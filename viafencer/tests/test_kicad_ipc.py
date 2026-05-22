from __future__ import annotations

from dataclasses import dataclass

from viafencer.kicad_ipc import (
    LayerOption,
    _chunks,
    _default_layer_pair,
    _ensure_item_id,
    _group_as_board_string,
    _pad_radius_nm,
    _unique_layer_options,
    _validate_layer_pair,
    _via_type_for_layer_span,
    choose_default_ground_net,
)


@dataclass
class _Size:
    x: int
    y: int


@dataclass
class _Layer:
    size: _Size


@dataclass
class _PadStack:
    copper_layers: list[_Layer]


@dataclass
class _Pad:
    padstack: _PadStack


def test_choose_default_ground_net_prefers_exact_gnd() -> None:
    assert choose_default_ground_net(["RF", "AGND", "GND"]) == "GND"


def test_choose_default_ground_net_accepts_ground_like_name() -> None:
    assert choose_default_ground_net(["RF", "/board_ground"]) == "/board_ground"


def test_choose_default_ground_net_does_not_guess_arbitrary_net() -> None:
    assert choose_default_ground_net(["RF", "IF"]) == ""


def test_pad_radius_uses_largest_copper_dimension() -> None:
    pad = _Pad(
        padstack=_PadStack(
            copper_layers=[
                _Layer(size=_Size(x=400_000, y=600_000)),
                _Layer(size=_Size(x=900_000, y=500_000)),
            ]
        )
    )

    assert _pad_radius_nm(pad) == 450_000


def test_chunks_splits_large_create_batches() -> None:
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_ensure_item_id_assigns_blank_proto_id() -> None:
    class _Id:
        value = ""

    class _Proto:
        id = _Id()

    class _Item:
        proto = _Proto()
        id = proto.id

    item = _Item()

    _ensure_item_id(item)

    assert item.id.value


def test_group_as_board_string_uses_native_kicad_group_syntax() -> None:
    text = _group_as_board_string(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        'RF "Via" Fence',
        ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"],
    )

    assert '(group "RF \\"Via\\" Fence"' in text
    assert '(uuid "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")' in text
    assert '"11111111-1111-4111-8111-111111111111"' in text
    assert '"22222222-2222-4222-8222-222222222222"' in text


def test_default_layer_pair_uses_outer_enabled_layers() -> None:
    options = [
        LayerOption(3, "F.Cu"),
        LayerOption(4, "In1.Cu"),
        LayerOption(34, "B.Cu"),
    ]

    assert _default_layer_pair(options) == (3, 34)


def test_validate_layer_pair_requires_enabled_ordered_span() -> None:
    options = [
        LayerOption(3, "F.Cu"),
        LayerOption(4, "In1.Cu"),
        LayerOption(34, "B.Cu"),
    ]

    assert _validate_layer_pair(3, 4, options) == []
    assert _validate_layer_pair(4, 3, options) == [
        "Start layer must be above the stop layer in the board stack."
    ]
    assert _validate_layer_pair(3, 3, options) == [
        "Start layer and stop layer must be different."
    ]


def test_via_type_uses_through_only_for_full_stack() -> None:
    from kipy.board_types import ViaType

    options = [
        LayerOption(3, "F.Cu"),
        LayerOption(4, "In1.Cu"),
        LayerOption(34, "B.Cu"),
    ]

    assert _via_type_for_layer_span(3, 34, options) == ViaType.VT_THROUGH
    assert _via_type_for_layer_span(3, 4, options) == ViaType.VT_BLIND_BURIED
    assert _via_type_for_layer_span(4, 34, options) == ViaType.VT_BLIND_BURIED


def test_unique_layer_options_disambiguates_duplicate_custom_names() -> None:
    assert _unique_layer_options([(3, "Signal"), (4, "Signal"), (34, "B.Cu")]) == [
        LayerOption(3, "Signal (F.Cu)"),
        LayerOption(4, "Signal (In1.Cu)"),
        LayerOption(34, "B.Cu"),
    ]
