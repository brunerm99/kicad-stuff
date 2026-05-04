"""KiCad IPC adapter for reading and updating the PCB selection."""

from __future__ import annotations

import os
from typing import Any, Iterable

from .models import RouteItemInfo, UNKNOWN

DEFAULT_SOCKET = "ipc:///tmp/kicad/api.sock"
NO_NET = "(no net)"


def resolve_socket(socket_path: str | None = None) -> str:
    """Resolve the KiCad IPC socket to use for this process."""

    return socket_path or os.environ.get("KICAD_API_SOCKET") or DEFAULT_SOCKET


class KiCadRouteSession:
    """Thin wrapper around the KiCad 10 IPC Python client."""

    def __init__(self, socket_path: str | None = None, timeout_ms: int = 5000):
        try:
            from kipy import KiCad
        except ImportError as exc:
            raise RuntimeError(
                "Missing kicad-python. Install requirements.txt in the Python "
                "environment used to launch this tool."
            ) from exc

        self.socket_path = resolve_socket(socket_path)
        self.kicad = KiCad(
            socket_path=self.socket_path,
            client_name="downselector",
            timeout_ms=timeout_ms,
        )
        self.board = self.kicad.get_board()

    def selected_route_items(self) -> list[RouteItemInfo]:
        """Read selected track/via items from the currently open board."""

        selected = self._get_selection()
        route_items = [item for item in selected if self._is_route_item(item)]
        net_classes = self._netclass_map(route_items)
        return [self._to_route_info(item, net_classes) for item in route_items]

    def replace_selection(self, infos: Iterable[RouteItemInfo]) -> int:
        """Replace the KiCad board selection with the supplied normalized items."""

        selected_items = [info.source for info in infos if info.source is not None]
        self.board.clear_selection()
        if selected_items:
            self.board.add_to_selection(selected_items)
        return len(selected_items)

    def _get_selection(self) -> list[Any]:
        try:
            from kipy.board import KiCadObjectType

            return list(
                self.board.get_selection(
                    types=[
                        KiCadObjectType.KOT_PCB_TRACE,
                        KiCadObjectType.KOT_PCB_ARC,
                        KiCadObjectType.KOT_PCB_VIA,
                    ]
                )
            )
        except Exception:
            return list(self.board.get_selection())

    def _is_route_item(self, item: Any) -> bool:
        try:
            from kipy.board_types import ArcTrack, Track, Via

            return isinstance(item, (Track, ArcTrack, Via))
        except Exception:
            class_name = item.__class__.__name__.lower()
            return class_name in {"track", "arctrack", "via"}

    def _to_route_info(
        self, item: Any, net_classes: dict[str, str]
    ) -> RouteItemInfo:
        try:
            from kipy.board_types import ArcTrack, Track, Via

            is_via = isinstance(item, Via)
            is_arc = isinstance(item, ArcTrack)
            is_track = isinstance(item, Track) or is_arc
        except Exception:
            class_name = item.__class__.__name__.lower()
            is_via = class_name == "via"
            is_arc = class_name == "arctrack"
            is_track = class_name in {"track", "arctrack"}

        net_name = _net_name(getattr(item, "net", None))
        net_class = net_classes.get(net_name, UNKNOWN)
        locked = bool(getattr(item, "locked", False))

        if is_track:
            return RouteItemInfo(
                id_text=_id_text(getattr(item, "id", "")),
                kind="track",
                shape="arc" if is_arc else "segment",
                net=net_name,
                net_class=net_class,
                locked=locked,
                source=item,
                track_width_nm=_int_or_none(getattr(item, "width", None)),
                track_layer=self._layer_name(getattr(item, "layer", None)),
            )

        if is_via:
            return RouteItemInfo(
                id_text=_id_text(getattr(item, "id", "")),
                kind="via",
                shape="via",
                net=net_name,
                net_class=net_class,
                locked=locked,
                source=item,
                via_diameter_nm=_int_or_none(getattr(item, "diameter", None)),
                via_drill_nm=_int_or_none(getattr(item, "drill_diameter", None)),
                via_layer_pair=self._via_layer_pair(item),
                via_type=self._via_type_name(getattr(item, "type", None)),
            )

        raise TypeError(f"Unsupported board item type: {item.__class__.__name__}")

    def _netclass_map(self, route_items: Iterable[Any]) -> dict[str, str]:
        nets = []
        seen = set()
        for item in route_items:
            net = getattr(item, "net", None)
            name = _net_name(net)
            if net is not None and name not in seen:
                seen.add(name)
                nets.append(net)

        if not nets:
            return {}

        try:
            netclasses = self.board.get_netclass_for_nets(nets)
        except Exception:
            return {name: UNKNOWN for name in seen}

        resolved: dict[str, str] = {}
        for name, netclass in netclasses.items():
            resolved[str(name) or NO_NET] = str(getattr(netclass, "name", UNKNOWN) or UNKNOWN)

        for name in seen:
            resolved.setdefault(name, UNKNOWN)

        return resolved

    def _layer_name(self, layer: Any) -> str:
        if layer is None:
            return UNKNOWN
        try:
            name = self.board.get_layer_name(layer)
            if name:
                return str(name)
        except Exception:
            pass

        try:
            from kipy.board_types import BoardLayer

            return BoardLayer.Name(layer)
        except Exception:
            return str(layer)

    def _via_layer_pair(self, via: Any) -> str:
        padstack = getattr(via, "padstack", None)
        drill = getattr(padstack, "drill", None)
        start_layer = getattr(drill, "start_layer", None)
        end_layer = getattr(drill, "end_layer", None)

        if start_layer is not None and end_layer is not None:
            return f"{self._layer_name(start_layer)} -> {self._layer_name(end_layer)}"

        layers = getattr(padstack, "layers", None)
        if layers:
            return ", ".join(self._layer_name(layer) for layer in layers)

        return UNKNOWN

    def _via_type_name(self, via_type: Any) -> str:
        if via_type is None:
            return UNKNOWN
        try:
            from kipy.board_types import ViaType

            return ViaType.Name(via_type)
        except Exception:
            return str(via_type)


def _id_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


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
