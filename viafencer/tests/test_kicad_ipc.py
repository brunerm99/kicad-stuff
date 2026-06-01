from __future__ import annotations

from dataclasses import dataclass

from viafencer.kicad_ipc import (
    KiCadFenceSession,
    LayerOption,
    _chunks,
    _default_layer_pair,
    _group_as_board_string,
    _is_kicad_not_ready_error,
    _pad_radius_nm,
    _retry_kicad_ready,
    _unique_layer_options,
    _validate_layer_pair,
    _via_as_board_string,
    choose_default_ground_net,
)
from viafencer.geometry import Point


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


@dataclass
class _Id:
    value: str


@dataclass
class _Group:
    id: _Id


@dataclass
class _Via:
    id: _Id


class _CreateOnlyBoard:
    def __init__(self) -> None:
        from kipy.board_types import Net

        self.net = Net(name="GND")
        self.created_board_strings: list[str] = []
        self.groups: list[_Group] = []
        self.selected: list[object] = []
        self.vias: list[_Via] = []

    def get_nets(self) -> list[object]:
        return [self.net]

    def get_enabled_layers(self) -> list[int]:
        return [3, 34]

    def get_layer_name(self, layer: int) -> str:
        return {3: "L1", 34: "L8"}[layer]

    def get_groups(self) -> list[_Group]:
        return self.groups

    def get_vias(self) -> list[_Via]:
        return self.vias

    def create_items(self, *_args: object) -> None:
        raise AssertionError("KiCad CreateItems must not be used for generated vias")

    def clear_selection(self) -> None:
        self.selected = []

    def add_to_selection(self, items: list[object]) -> None:
        self.selected.extend(items)

    def begin_commit(self) -> None:
        raise AssertionError("explicit KiCad commit transactions must not be used")

    def push_commit(self, *_args: object) -> None:
        raise AssertionError("explicit KiCad commit transactions must not be used")

    def drop_commit(self, *_args: object) -> None:
        raise AssertionError("explicit KiCad commit transactions must not be used")


def _session_for_board(board: object) -> KiCadFenceSession:
    session = object.__new__(KiCadFenceSession)
    session.board = board
    return session


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


def test_unique_layer_options_disambiguates_duplicate_custom_names() -> None:
    assert _unique_layer_options([(3, "Signal"), (4, "Signal"), (34, "B.Cu")]) == [
        LayerOption(3, "Signal (F.Cu)"),
        LayerOption(4, "Signal (In1.Cu)"),
        LayerOption(34, "B.Cu"),
    ]


def test_kicad_not_ready_detection_uses_raw_message() -> None:
    class _ApiError(Exception):
        _raw_message = "KiCad is not ready to reply"

    assert _is_kicad_not_ready_error(_ApiError("KiCad returned error"))


def test_retry_kicad_ready_retries_transient_busy_error(monkeypatch) -> None:
    attempts = 0

    class _ApiError(Exception):
        _raw_message = "KiCad is not ready to reply"

    def action() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _ApiError("KiCad returned error")
        return "ok"

    monkeypatch.setattr("viafencer.kicad_ipc.sleep", lambda _seconds: None)

    assert _retry_kicad_ready(action, "test action") == "ok"
    assert attempts == 2


def test_via_as_board_string_uses_native_kicad_syntax() -> None:
    text = _via_as_board_string(
        "via-1",
        Point(1_234_567, 2_000_000),
        'GN"D',
        457_200,
        203_200,
        3,
        34,
        [LayerOption(3, "L1"), LayerOption(34, "L8")],
    )

    assert text.startswith("(via\n")
    assert "(at 1.234567 2)" in text
    assert "(size 0.4572)" in text
    assert "(drill 0.2032)" in text
    assert '(layers "F.Cu" "B.Cu")' in text
    assert '(net "GN\\"D")' in text
    assert '(uuid "via-1")' in text


def test_via_as_board_string_marks_partial_span_as_blind() -> None:
    text = _via_as_board_string(
        "via-1",
        Point(0, 0),
        "GND",
        457_200,
        203_200,
        3,
        6,
        [LayerOption(3, "L1"), LayerOption(6, "L4"), LayerOption(34, "L8")],
    )

    assert text.startswith("(via blind\n")
    assert '(layers "F.Cu" "In3.Cu")' in text


def test_create_vias_uses_native_parser_without_commit_or_create_items(monkeypatch) -> None:
    board = _CreateOnlyBoard()
    session = _session_for_board(board)
    ids = iter(["via-1", "via-2"])
    monkeypatch.setattr("viafencer.kicad_ipc.uuid.uuid4", lambda: next(ids))

    def parse_items(_board: object, contents: str) -> None:
        board.created_board_strings.append(contents)
        board.vias = [_Via(_Id("via-1")), _Via(_Id("via-2"))]

    monkeypatch.setattr("viafencer.kicad_ipc._parse_and_create_items_from_string", parse_items)

    summary = session.create_vias(
        [Point(1, 2), Point(3, 4)],
        "GND",
        457_200,
        203_200,
        start_layer=3,
        end_layer=34,
        grouped=False,
    )

    assert summary.via_count == 2
    assert summary.grouped is False
    assert len(board.created_board_strings) == 1
    assert board.created_board_strings[0].count("(via\n") == 2
    assert len(board.selected) == 2


def test_create_vias_groups_without_commit(monkeypatch) -> None:
    board = _CreateOnlyBoard()
    session = _session_for_board(board)
    ids = iter(["via-1", "via-2", "group-1"])
    monkeypatch.setattr("viafencer.kicad_ipc.uuid.uuid4", lambda: next(ids))

    def parse_items(_board: object, contents: str) -> None:
        board.created_board_strings.append(contents)
        if contents.startswith("(group"):
            assert '"via-1"' in contents
            assert '"via-2"' in contents
            assert '(uuid "group-1")' in contents
            board.groups = [_Group(_Id("group-1"))]
        else:
            board.vias = [_Via(_Id("via-1")), _Via(_Id("via-2"))]

    monkeypatch.setattr("viafencer.kicad_ipc._parse_and_create_items_from_string", parse_items)

    summary = session.create_vias(
        [Point(1, 2), Point(3, 4)],
        "GND",
        457_200,
        203_200,
        start_layer=3,
        end_layer=34,
        grouped=True,
    )

    assert summary.via_count == 2
    assert summary.grouped is True
    assert len(board.created_board_strings) == 2
    assert board.selected == board.groups
