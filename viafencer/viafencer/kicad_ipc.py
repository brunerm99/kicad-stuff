"""KiCad IPC adapter for RF via fencing."""

from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from time import sleep
from typing import Any, Callable, Iterable, TypeVar

from .geometry import CircularObstacle, Point, TrackGeometry

DEFAULT_SOCKET = "ipc:///tmp/kicad/api.sock"
NO_NET = "(no net)"
CREATE_CHUNK_SIZE = 250
KICAD_READY_RETRIES = 3
KICAD_READY_RETRY_DELAY_S = 0.5
LOGGER = logging.getLogger(__name__)
FIRST_COPPER_LAYER = 3
LAST_COPPER_LAYER = 34
T = TypeVar("T")

GROUND_NET_PRIORITY = ("GND", "GNDA", "AGND", "DGND", "PGND", "VSS", "0")


@dataclass(frozen=True)
class CreationSummary:
    via_count: int
    grouped: bool
    group_error: str | None = None


@dataclass(frozen=True)
class LayerOption:
    layer: int
    label: str


def resolve_socket(socket_path: str | None = None) -> str:
    return socket_path or os.environ.get("KICAD_API_SOCKET") or DEFAULT_SOCKET


def choose_default_ground_net(net_names: Iterable[str]) -> str:
    names = [name for name in net_names if name and name != NO_NET]
    by_upper = {name.upper(): name for name in names}
    for preferred in GROUND_NET_PRIORITY:
        if preferred in by_upper:
            return by_upper[preferred]

    for name in sorted(names, key=str.upper):
        upper = name.upper()
        if "GND" in upper or "GROUND" in upper:
            return name

    return ""


class KiCadFenceSession:
    """Thin wrapper around the KiCad 10 IPC Python client."""

    def __init__(self, socket_path: str | None = None, timeout_ms: int = 5000):
        try:
            from kipy import KiCad
        except ImportError as exc:
            raise RuntimeError(
                "Missing kicad-python. Install this tool in the Python environment "
                "used to launch KiCad IPC plugins."
            ) from exc

        self.socket_path = resolve_socket(socket_path)
        self.kicad = KiCad(
            socket_path=self.socket_path,
            client_name="viafencer",
            timeout_ms=timeout_ms,
        )
        self.board = self.kicad.get_board()

    def selected_tracks(self) -> list[TrackGeometry]:
        LOGGER.info("Requesting selected tracks/arcs from KiCad")
        selected = self._get_track_selection()
        tracks: list[TrackGeometry] = []
        for index, item in enumerate(selected):
            geometry = self._to_track_geometry(item, index)
            if geometry is not None:
                tracks.append(geometry)
        LOGGER.info("Read %d selected tracks/arcs", len(tracks))
        return tracks

    def net_names(self) -> list[str]:
        LOGGER.info("Requesting board nets from KiCad")
        try:
            nets = self.board.get_nets()
        except Exception:
            LOGGER.exception("Could not read board nets")
            return []

        names = sorted({_net_name(net) for net in nets if _net_name(net) != NO_NET})
        LOGGER.info("Read %d board nets", len(names))
        return names

    def copper_layer_options(self) -> list[LayerOption]:
        LOGGER.info("Requesting enabled copper layers from KiCad")
        try:
            enabled_layers = self.board.get_enabled_layers()
        except Exception:
            LOGGER.exception("Could not read enabled board layers")
            return []

        layers = sorted(
            {
                layer
                for layer in (_int_or_none(layer) for layer in enabled_layers)
                if layer is not None and _is_copper_layer(layer)
            }
        )
        layer_names: list[tuple[int, str]] = []
        for layer in layers:
            try:
                name = self.board.get_layer_name(layer)
            except Exception:
                LOGGER.exception("Could not read layer name for layer %d", layer)
                name = ""
            layer_names.append((layer, name or _canonical_copper_layer_name(layer)))

        options = _unique_layer_options(layer_names)
        LOGGER.info("Read %d enabled copper layers", len(options))
        return options

    def existing_obstacles(self) -> list[CircularObstacle]:
        obstacles: list[CircularObstacle] = []

        LOGGER.info("Requesting existing vias from KiCad")
        try:
            vias = self.board.get_vias()
        except Exception:
            LOGGER.exception("Could not read existing vias")
            vias = []
        for via in vias:
            diameter_nm = _int_or_none(getattr(via, "diameter", None))
            if diameter_nm is None:
                continue
            obstacles.append(
                CircularObstacle(
                    center=_point_from_vector(getattr(via, "position", None)),
                    radius_nm=diameter_nm / 2,
                    label="via",
                )
            )

        LOGGER.info("Requesting existing pads from KiCad")
        try:
            pads = self.board.get_pads()
        except Exception:
            LOGGER.exception("Could not read existing pads")
            pads = []
        for pad in pads:
            radius_nm = _pad_radius_nm(pad)
            if radius_nm <= 0:
                continue
            obstacles.append(
                CircularObstacle(
                    center=_point_from_vector(getattr(pad, "position", None)),
                    radius_nm=radius_nm,
                    label="pad",
                )
            )

        LOGGER.info("Built %d circular obstacles from existing vias/pads", len(obstacles))
        return obstacles

    def create_fence_vias(
        self,
        centers: Iterable[Point],
        net_name: str,
        via_diameter_nm: int,
        drill_diameter_nm: int,
        start_layer: int | None = None,
        end_layer: int | None = None,
        grouped: bool = True,
    ) -> CreationSummary:
        return self.create_vias(
            centers,
            net_name,
            via_diameter_nm,
            drill_diameter_nm,
            start_layer=start_layer,
            end_layer=end_layer,
            grouped=grouped,
            group_name="RF Via Fence",
        )

    def create_vias(
        self,
        centers: Iterable[Point],
        net_name: str,
        via_diameter_nm: int,
        drill_diameter_nm: int,
        start_layer: int | None = None,
        end_layer: int | None = None,
        grouped: bool = True,
        group_name: str = "Generated Vias",
    ) -> CreationSummary:
        centers = list(centers)
        if not centers:
            return CreationSummary(via_count=0, grouped=False)

        LOGGER.info("Looking up via net %s", net_name)
        self._find_net(net_name)
        layer_options = self.copper_layer_options()
        if start_layer is None or end_layer is None:
            default_pair = _default_layer_pair(layer_options)
            if default_pair is None:
                raise RuntimeError("No valid copper layer span is available for via creation.")
            start_layer, end_layer = default_pair

        layer_errors = _validate_layer_pair(start_layer, end_layer, layer_options)
        if layer_errors:
            raise RuntimeError("\n".join(layer_errors))

        LOGGER.info(
            "Creating %d via items from native board strings on layers %s -> %s",
            len(centers),
            _layer_label(start_layer, layer_options),
            _layer_label(end_layer, layer_options),
        )
        via_ids = [str(uuid.uuid4()) for _center in centers]
        via_texts = [
            _via_as_board_string(
                via_id,
                center,
                net_name,
                via_diameter_nm,
                drill_diameter_nm,
                start_layer,
                end_layer,
                layer_options,
            )
            for via_id, center in zip(via_ids, centers)
        ]

        LOGGER.info(
            "Prepared %d vias with %d assigned IDs",
            len(via_texts),
            len(via_ids),
        )
        for index, chunk in enumerate(_chunks(via_texts, CREATE_CHUNK_SIZE), start=1):
            LOGGER.info(
                "Creating via chunk %d: %d vias",
                index,
                len(chunk),
            )
            _retry_kicad_ready(
                lambda chunk=chunk: _parse_and_create_items_from_string(self.board, "".join(chunk)),
                "creating via chunk",
            )

        created_vias = _retry_kicad_ready(
            lambda: self._get_vias_by_ids(via_ids),
            "reading created vias",
        )
        if len(created_vias) != len(via_ids):
            LOGGER.warning(
                "Created %d vias, but KiCad returned %d of them by ID",
                len(via_ids),
                len(created_vias),
            )

        created_group = None
        group_error = None
        if grouped and via_ids:
            group_id = str(uuid.uuid4())
            group_text = _group_as_board_string(group_id, group_name, via_ids)
            try:
                LOGGER.info("Creating native KiCad group for %d vias", len(via_ids))
                _retry_kicad_ready(
                    lambda: _parse_and_create_items_from_string(self.board, group_text),
                    "creating native KiCad group",
                )
            except Exception:
                group_error = f"Created {len(created_vias)} vias, but failed to create the KiCad group."
                LOGGER.exception(group_error)

            if group_error is None:
                created_group = _retry_kicad_ready(
                    lambda: self._get_group_by_id(group_id),
                    "reading created group",
                )
                if created_group is None:
                    group_error = (
                        f"Created {len(created_vias)} vias, but KiCad did not return the new group."
                    )
                    LOGGER.error(group_error)

        LOGGER.info("Selecting created %s", "group" if created_group else "vias")
        self._select_created_items([created_group] if created_group else created_vias)
        return CreationSummary(
            via_count=len(via_ids),
            grouped=created_group is not None,
            group_error=group_error,
        )

    def _get_track_selection(self) -> list[Any]:
        try:
            from kipy.board import KiCadObjectType

            return list(
                self.board.get_selection(
                    types=[
                        KiCadObjectType.KOT_PCB_TRACE,
                        KiCadObjectType.KOT_PCB_ARC,
                    ]
                )
            )
        except Exception:
            return list(self.board.get_selection())

    def _to_track_geometry(self, item: Any, index: int) -> TrackGeometry | None:
        if not _is_track_item(item):
            return None

        is_arc = _is_arc_item(item)
        start = _point_from_vector(getattr(item, "start", None))
        end = _point_from_vector(getattr(item, "end", None))
        mid = _point_from_vector(getattr(item, "mid", None)) if is_arc else None
        width_nm = _int_or_none(getattr(item, "width", None)) or 0
        label = _id_text(getattr(item, "id", "")) or f"track {index + 1}"

        return TrackGeometry(
            kind="arc" if is_arc else "segment",
            start=start,
            mid=mid,
            end=end,
            width_nm=width_nm,
            label=label,
            layer=_int_or_none(getattr(item, "layer", None)),
        )

    def _find_net(self, net_name: str) -> Any:
        for net in self.board.get_nets():
            if _net_name(net) == net_name:
                return net
        raise RuntimeError(f"Net not found: {net_name}")

    def _select_created_items(self, items: list[Any]) -> None:
        if not items:
            return
        try:
            self.board.clear_selection()
            self.board.add_to_selection(items)
        except Exception:
            LOGGER.exception("Failed to select created items")

    def _get_group_by_id(self, group_id: str) -> Any | None:
        for group in self.board.get_groups():
            if _id_text(group.id) == group_id:
                return group
        return None

    def _get_vias_by_ids(self, via_ids: list[str]) -> list[Any]:
        wanted = set(via_ids)
        if hasattr(self.board, "get_items_by_id"):
            try:
                from kipy.proto.common.types import KIID

                items = self.board.get_items_by_id([KIID(value=via_id) for via_id in via_ids])
                by_item_id = {
                    _id_text(item.id): item
                    for item in items
                    if _id_text(getattr(item, "id", "")) in wanted
                }
                if by_item_id:
                    return [by_item_id[via_id] for via_id in via_ids if via_id in by_item_id]
            except Exception:
                LOGGER.exception("Could not read created vias by ID")

        by_id = {
            _id_text(via.id): via
            for via in self.board.get_vias()
            if _id_text(getattr(via, "id", "")) in wanted
        }
        return [by_id[via_id] for via_id in via_ids if via_id in by_id]


def _is_track_item(item: Any) -> bool:
    try:
        from kipy.board_types import ArcTrack, Track

        return isinstance(item, (Track, ArcTrack))
    except Exception:
        return item.__class__.__name__.lower() in {"track", "arctrack"}


def _is_arc_item(item: Any) -> bool:
    try:
        from kipy.board_types import ArcTrack

        return isinstance(item, ArcTrack)
    except Exception:
        return item.__class__.__name__.lower() == "arctrack"


def _point_from_vector(vector: Any) -> Point:
    if vector is None:
        return Point(0, 0)
    return Point(float(getattr(vector, "x", 0)), float(getattr(vector, "y", 0)))


def _pad_radius_nm(pad: Any) -> float:
    padstack = getattr(pad, "padstack", None)
    copper_layers = getattr(padstack, "copper_layers", []) if padstack else []
    radii = []
    for layer in copper_layers:
        size = getattr(layer, "size", None)
        if size is None:
            continue
        width = _int_or_none(getattr(size, "x", None))
        height = _int_or_none(getattr(size, "y", None))
        if width is None or height is None:
            continue
        radii.append(max(width, height) / 2)
    return max(radii) if radii else 0


def _id_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _group_as_board_string(group_id: str, name: str, member_ids: list[str]) -> str:
    members = " ".join(f'"{member_id}"' for member_id in member_ids)
    return (
        f'(group "{_escape_pcb_string(name)}"\n'
        f'\t(uuid "{_escape_pcb_string(group_id)}")\n'
        f"\t(members {members})\n"
        ")\n"
    )


def _via_as_board_string(
    via_id: str,
    center: Point,
    net_name: str,
    via_diameter_nm: int,
    drill_diameter_nm: int,
    start_layer: int,
    end_layer: int,
    layer_options: list[LayerOption],
) -> str:
    via_kind = " blind" if _default_layer_pair(layer_options) != (start_layer, end_layer) else ""
    return (
        f"(via{via_kind}\n"
        f"\t(at {_format_board_mm(center.x)} {_format_board_mm(center.y)})\n"
        f"\t(size {_format_board_mm(via_diameter_nm)})\n"
        f"\t(drill {_format_board_mm(drill_diameter_nm)})\n"
        f'\t(layers "{_escape_pcb_string(_file_layer_name(start_layer))}" '
        f'"{_escape_pcb_string(_file_layer_name(end_layer))}")\n'
        f'\t(net "{_escape_pcb_string(net_name)}")\n'
        f'\t(uuid "{_escape_pcb_string(via_id)}")\n'
        ")\n"
    )


def _format_board_mm(value_nm: float | int) -> str:
    text = f"{round(value_nm) / 1_000_000:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _file_layer_name(layer: int) -> str:
    try:
        from kipy.util.board_layer import canonical_name

        name = canonical_name(layer)
        if name != "Unknown":
            return name
    except Exception:
        LOGGER.exception("Could not load canonical KiCad layer name for layer %d", layer)

    return _canonical_copper_layer_name(layer)


def _escape_pcb_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_and_create_items_from_string(board: Any, contents: str) -> None:
    from kipy.proto.common import ApiRequest, ApiResponse, ApiStatusCode
    from kipy.proto.common.commands import editor_commands_pb2

    client = board.client
    if not client.connected:
        client._connect()

    command = editor_commands_pb2.ParseAndCreateItemsFromString()
    command.document.CopyFrom(board.document)
    command.contents = contents

    envelope = ApiRequest()
    envelope.message.Pack(command)
    envelope.header.kicad_token = client._kicad_token
    envelope.header.client_name = client._client_name

    client._conn.send(envelope.SerializeToString())
    reply_data = client._conn.recv_msg()
    reply = ApiResponse()
    reply.ParseFromString(reply_data.bytes)

    if reply.status.status != ApiStatusCode.AS_OK:
        raise RuntimeError(reply.status.error_message or "KiCad rejected item creation")
    if client._kicad_token == "":
        client._kicad_token = reply.header.kicad_token


def _retry_kicad_ready(action: Callable[[], T], description: str) -> T:
    for attempt in range(KICAD_READY_RETRIES + 1):
        try:
            return action()
        except Exception as exc:
            if not _is_kicad_not_ready_error(exc) or attempt >= KICAD_READY_RETRIES:
                raise
            LOGGER.warning(
                "KiCad was not ready while %s; retrying %d/%d",
                description,
                attempt + 1,
                KICAD_READY_RETRIES,
            )
            sleep(KICAD_READY_RETRY_DELAY_S)

    raise RuntimeError("unreachable retry state")


def _is_kicad_not_ready_error(exc: Exception) -> bool:
    raw_message = getattr(exc, "_raw_message", "")
    return "KiCad is not ready to reply" in str(raw_message or exc)


def _is_copper_layer(layer: int) -> bool:
    return FIRST_COPPER_LAYER <= layer <= LAST_COPPER_LAYER


def _canonical_copper_layer_name(layer: int) -> str:
    if layer == FIRST_COPPER_LAYER:
        return "F.Cu"
    if layer == LAST_COPPER_LAYER:
        return "B.Cu"
    if FIRST_COPPER_LAYER < layer < LAST_COPPER_LAYER:
        return f"In{layer - FIRST_COPPER_LAYER}.Cu"
    return f"Layer {layer}"


def _unique_layer_options(layer_names: list[tuple[int, str]]) -> list[LayerOption]:
    counts = Counter(name for _layer, name in layer_names)
    return [
        LayerOption(
            layer=layer,
            label=name if counts[name] == 1 else f"{name} ({_canonical_copper_layer_name(layer)})",
        )
        for layer, name in layer_names
    ]


def _default_layer_pair(layer_options: list[LayerOption]) -> tuple[int, int] | None:
    if len(layer_options) < 2:
        return None
    return layer_options[0].layer, layer_options[-1].layer


def _validate_layer_pair(
    start_layer: int, end_layer: int, layer_options: list[LayerOption]
) -> list[str]:
    if len(layer_options) < 2:
        return ["The board must have at least two enabled copper layers."]

    layer_order = {option.layer: index for index, option in enumerate(layer_options)}
    errors: list[str] = []
    if start_layer not in layer_order:
        errors.append(f"Start layer is not an enabled copper layer: {start_layer}.")
    if end_layer not in layer_order:
        errors.append(f"Stop layer is not an enabled copper layer: {end_layer}.")
    if errors:
        return errors
    if start_layer == end_layer:
        return ["Start layer and stop layer must be different."]
    if layer_order[start_layer] >= layer_order[end_layer]:
        return ["Start layer must be above the stop layer in the board stack."]
    return []


def _layer_label(layer: int, layer_options: list[LayerOption]) -> str:
    for option in layer_options:
        if option.layer == layer:
            return option.label
    return _canonical_copper_layer_name(layer)


def _net_name(net: Any) -> str:
    name = getattr(net, "name", None)
    return str(name or NO_NET)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]
