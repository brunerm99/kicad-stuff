"""KiCad IPC adapter for RF solder-mask opening zones."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from .geometry import Point
from .kicad_ipc import (
    CREATE_CHUNK_SIZE,
    FIRST_COPPER_LAYER,
    LAST_COPPER_LAYER,
    KiCadFenceSession,
    _chunks,
    _escape_pcb_string,
    _format_board_mm,
    _group_as_board_string,
    _id_text,
    _parse_and_create_items_from_string,
    _retry_kicad_ready,
)
from .mask_geometry import MaskOpening

LOGGER = logging.getLogger(__name__)

AUTO_MASK_LAYER = "Auto from trace layer"
FRONT_MASK_LAYER = "F.Mask"
BACK_MASK_LAYER = "B.Mask"
MASK_LAYER_CHOICES = (AUTO_MASK_LAYER, FRONT_MASK_LAYER, BACK_MASK_LAYER)


@dataclass(frozen=True)
class MaskZoneSummary:
    zone_count: int
    skipped_layer_count: int = 0
    grouped: bool = False
    group_error: str | None = None


class MaskZoneSession(KiCadFenceSession):
    """KiCad session with solder-mask opening helpers."""

    def create_mask_zones(
        self,
        openings: Iterable[MaskOpening],
        layer_choice: str = AUTO_MASK_LAYER,
        grouped: bool = True,
    ) -> MaskZoneSummary:
        zone_texts: list[str] = []
        zone_ids: list[str] = []
        skipped_layer_count = 0

        for opening in openings:
            mask_layer = resolve_mask_layer(opening.source_layer, layer_choice)
            if mask_layer is None:
                skipped_layer_count += 1
                continue

            zone_id = str(uuid.uuid4())
            zone_ids.append(zone_id)
            zone_texts.append(
                _mask_zone_as_board_string(
                    zone_id,
                    mask_layer,
                    opening.polygon,
                    name=f"RF mask opening {opening.source_label}",
                )
            )

        if not zone_texts:
            return MaskZoneSummary(
                zone_count=0,
                skipped_layer_count=skipped_layer_count,
            )

        existing_zone_ids = self._zone_id_set()
        LOGGER.info(
            "Creating %d RF solder-mask opening zones through native KiCad parser",
            len(zone_texts),
        )
        for index, chunk in enumerate(_chunks(zone_texts, CREATE_CHUNK_SIZE), start=1):
            LOGGER.info("Creating mask-zone chunk %d: %d zones", index, len(chunk))
            _retry_kicad_ready(
                lambda chunk=chunk: _parse_and_create_items_from_string(
                    self.board, "".join(chunk)
                ),
                "creating mask-zone chunk",
            )

        try:
            LOGGER.info("Refilling zones after mask-zone creation")
            self.board.refill_zones(block=True, max_poll_seconds=30.0)
        except TypeError:
            self.board.refill_zones()

        created_zones = _retry_kicad_ready(
            lambda: self._get_zones_created_after(existing_zone_ids),
            "reading created mask zones",
        )
        if len(created_zones) != len(zone_ids):
            LOGGER.warning(
                "Created %d mask zones, but KiCad returned %d new zones for selection/grouping",
                len(zone_ids),
                len(created_zones),
            )

        created_group = None
        group_error = None
        created_zone_ids = [
            zone_id
            for zone_id in (_id_text(getattr(zone, "id", "")) for zone in created_zones)
            if zone_id
        ]
        if grouped and zone_ids and not created_zone_ids:
            group_error = (
                f"Created {len(zone_ids)} mask zones, but could not read their KiCad IDs "
                "for grouping."
            )
            LOGGER.error(group_error)
        elif grouped and created_zone_ids:
            group_id = str(uuid.uuid4())
            group_text = _group_as_board_string(
                group_id, "RF Mask Openings", created_zone_ids
            )
            try:
                LOGGER.info(
                    "Creating native KiCad group for %d mask zones",
                    len(created_zone_ids),
                )
                _retry_kicad_ready(
                    lambda: _parse_and_create_items_from_string(self.board, group_text),
                    "creating native KiCad mask-zone group",
                )
            except Exception:
                group_error = (
                    f"Created {len(created_zones)} mask zones, but failed to create "
                    "the KiCad group."
                )
                LOGGER.exception(group_error)

            if group_error is None:
                created_group = _retry_kicad_ready(
                    lambda: self._get_group_by_id(group_id),
                    "reading created mask-zone group",
                )
                if created_group is None:
                    group_error = (
                        f"Created {len(created_zones)} mask zones, but KiCad did not "
                        "return the new group."
                    )
                    LOGGER.error(group_error)

        LOGGER.info("Selecting created mask %s", "group" if created_group else "zones")
        self._select_created_items([created_group] if created_group else created_zones)
        return MaskZoneSummary(
            zone_count=len(zone_ids),
            skipped_layer_count=skipped_layer_count,
            grouped=created_group is not None,
            group_error=group_error,
        )

    def display_layer_name(self, layer: int | None) -> str:
        if layer is None:
            return "Unknown"
        try:
            name = self.board.get_layer_name(layer)
            if name:
                return str(name)
        except Exception:
            LOGGER.exception("Could not read layer name for layer %s", layer)

        try:
            from kipy.util.board_layer import canonical_name

            canonical = canonical_name(layer)
            if canonical != "Unknown":
                return canonical
        except Exception:
            LOGGER.exception("Could not load canonical layer name for layer %s", layer)

        return str(layer)

    def _zone_id_set(self) -> set[str]:
        return {
            zone_id
            for zone_id in (
                _id_text(getattr(zone, "id", ""))
                for zone in self.board.get_zones()
            )
            if zone_id
        }

    def _get_zones_created_after(self, existing_zone_ids: set[str]) -> list[Any]:
        return [
            zone
            for zone in self.board.get_zones()
            if _id_text(getattr(zone, "id", "")) not in existing_zone_ids
        ]


def resolve_mask_layer(source_layer: int | None, layer_choice: str) -> str | None:
    if layer_choice == FRONT_MASK_LAYER:
        return FRONT_MASK_LAYER
    if layer_choice == BACK_MASK_LAYER:
        return BACK_MASK_LAYER
    if source_layer == FIRST_COPPER_LAYER:
        return FRONT_MASK_LAYER
    if source_layer == LAST_COPPER_LAYER:
        return BACK_MASK_LAYER
    return None


def _mask_zone_as_board_string(
    zone_id: str,
    layer_name: str,
    polygon: tuple[Point, ...],
    name: str,
) -> str:
    points = "\n".join(
        f"\t\t\t\t(xy {_format_board_mm(point.x)} {_format_board_mm(point.y)})"
        for point in polygon
    )
    return (
        "(zone\n"
        f'\t(layer "{_escape_pcb_string(layer_name)}")\n'
        f'\t(uuid "{_escape_pcb_string(zone_id)}")\n'
        f'\t(name "{_escape_pcb_string(name)}")\n'
        "\t(hatch edge 0.5)\n"
        "\t(connect_pads\n"
        "\t\t(clearance 0)\n"
        "\t)\n"
        "\t(min_thickness 0.001)\n"
        "\t(fill yes\n"
        "\t\t(thermal_gap 0.5)\n"
        "\t\t(thermal_bridge_width 0.5)\n"
        "\t\t(island_removal_mode 0)\n"
        "\t)\n"
        "\t(polygon\n"
        "\t\t(pts\n"
        f"{points}\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )
