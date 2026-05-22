"""KiCad IPC adapter for RF via fencing."""

from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from .geometry import CircularObstacle, Point, TrackGeometry

DEFAULT_SOCKET = "ipc:///tmp/kicad/api.sock"
NO_NET = "(no net)"
CREATE_CHUNK_SIZE = 250
LOGGER = logging.getLogger(__name__)
FIRST_COPPER_LAYER = 3
LAST_COPPER_LAYER = 34

GROUND_NET_PRIORITY = ("GND", "GNDA", "AGND", "DGND", "PGND", "VSS", "0")


@dataclass(frozen=True)
class CreationSummary:
    via_count: int
    grouped: bool


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
        centers = list(centers)
        if not centers:
            return CreationSummary(via_count=0, grouped=False)

        LOGGER.info("Looking up via net %s", net_name)
        net = self._find_net(net_name)
        layer_options = self.copper_layer_options()
        if start_layer is None or end_layer is None:
            default_pair = _default_layer_pair(layer_options)
            if default_pair is None:
                raise RuntimeError("No valid copper layer span is available for via creation.")
            start_layer, end_layer = default_pair

        layer_errors = _validate_layer_pair(start_layer, end_layer, layer_options)
        if layer_errors:
            raise RuntimeError("\n".join(layer_errors))

        from kipy.board_types import Via
        from kipy.geometry import Vector2

        via_type = _via_type_for_layer_span(start_layer, end_layer, layer_options)
        LOGGER.info(
            "Beginning KiCad via-create commit for %d vias on layers %s -> %s",
            len(centers),
            _layer_label(start_layer, layer_options),
            _layer_label(end_layer, layer_options),
        )
        commit = self.board.begin_commit()
        created_vias = []
        commit_open = True
        vias = []
        try:
            for center in centers:
                via = Via()
                _ensure_item_id(via)
                via.position = Vector2.from_xy(round(center.x), round(center.y))
                via.net = net
                via.type = via_type
                via.padstack.drill.start_layer = start_layer
                via.padstack.drill.end_layer = end_layer
                via.diameter = via_diameter_nm
                via.drill_diameter = drill_diameter_nm
                vias.append(via)

            LOGGER.info(
                "Prepared %d vias with %d assigned IDs",
                len(vias),
                sum(1 for via in vias if _id_text(via.id)),
            )
            for index, chunk in enumerate(_chunks(vias, CREATE_CHUNK_SIZE), start=1):
                LOGGER.info(
                    "Creating via chunk %d: %d vias",
                    index,
                    len(chunk),
                )
                created_vias.extend(self.board.create_items(chunk))

            LOGGER.info("Pushing KiCad via-create commit")
            self.board.push_commit(commit, "Create RF via fence vias")
            commit_open = False
        except Exception:
            if commit_open:
                LOGGER.exception("Via creation failed; dropping KiCad commit")
                try:
                    self.board.drop_commit(commit)
                except Exception:
                    LOGGER.exception("Failed to drop KiCad commit after via creation error")
            raise

        created_group = None
        if grouped and created_vias:
            via_ids = [_id_text(via.id) for via in vias]
            group_id = str(uuid.uuid4())
            group_text = _group_as_board_string(group_id, "RF Via Fence", via_ids)
            group_commit = self.board.begin_commit()
            group_commit_open = True
            try:
                LOGGER.info("Creating native KiCad group for %d vias", len(via_ids))
                _parse_and_create_items_from_string(self.board, group_text)
                LOGGER.info("Pushing KiCad group-create commit")
                self.board.push_commit(group_commit, "Group RF via fence")
                group_commit_open = False
            except Exception:
                if group_commit_open:
                    try:
                        self.board.drop_commit(group_commit)
                    except Exception:
                        LOGGER.exception("Failed to drop KiCad group commit")
                LOGGER.exception("Created vias, but failed to create group")
                raise RuntimeError(
                    f"Created {len(created_vias)} vias, but failed to create the KiCad group."
                )

            created_group = self._get_group_by_id(group_id)
            if created_group is None:
                raise RuntimeError(
                    f"Created {len(created_vias)} vias, but KiCad did not return the new group."
                )

        LOGGER.info("Selecting created %s", "group" if created_group else "vias")
        self._select_created_items([created_group] if created_group else created_vias)
        return CreationSummary(via_count=len(created_vias), grouped=created_group is not None)

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


def _ensure_item_id(item: Any) -> None:
    item_id = getattr(item, "id", None)
    if _id_text(item_id):
        return

    proto = getattr(item, "proto", None)
    proto_id = getattr(proto, "id", None)
    if proto_id is not None:
        proto_id.value = str(uuid.uuid4())


def _group_as_board_string(group_id: str, name: str, member_ids: list[str]) -> str:
    members = " ".join(f'"{member_id}"' for member_id in member_ids)
    return (
        f'(group "{_escape_pcb_string(name)}"\n'
        f'\t(uuid "{_escape_pcb_string(group_id)}")\n'
        f"\t(members {members})\n"
        ")\n"
    )


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
        raise RuntimeError(reply.status.error_message or "KiCad rejected group creation")
    if client._kicad_token == "":
        client._kicad_token = reply.header.kicad_token


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


def _via_type_for_layer_span(
    start_layer: int, end_layer: int, layer_options: list[LayerOption]
) -> Any:
    from kipy.board_types import ViaType

    default_pair = _default_layer_pair(layer_options)
    if default_pair == (start_layer, end_layer):
        return ViaType.VT_THROUGH
    return ViaType.VT_BLIND_BURIED


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
