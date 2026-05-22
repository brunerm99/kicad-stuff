"""Shared units and configuration for RF via fencing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

NM_PER_MM = 1_000_000
NM_PER_MIL = 25_400

SizeUnit = Literal["mm", "mil"]
FenceSide = Literal["both", "left", "right"]


@dataclass(frozen=True)
class FenceConfig:
    via_diameter_nm: int
    drill_diameter_nm: int
    spacing_nm: int
    gap_nm: int
    collision_margin_nm: int = 0
    sides: FenceSide = "both"


def parse_size_to_nm(value: str, default_unit: SizeUnit = "mm") -> int | None:
    text = value.strip().lower()
    if not text:
        return None

    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([a-z]*)", text)
    if not match:
        return None

    number = float(match.group(1))
    unit = match.group(2) or default_unit
    if unit in {"mm", "millimeter", "millimeters"}:
        scale = NM_PER_MM
    elif unit in {"mil", "mils", "thou", "thousandth"}:
        scale = NM_PER_MIL
    else:
        return None

    return round(number * scale)


def format_nm_as_mm(value_nm: int | None) -> str:
    if value_nm is None:
        return "-"
    return f"{value_nm / NM_PER_MM:.3f} mm"


def format_nm_as_mil(value_nm: int | None) -> str:
    if value_nm is None:
        return "-"
    return f"{value_nm / NM_PER_MIL:.2f} mil"


def format_nm_as_unit(value_nm: int | None, unit: SizeUnit) -> str:
    if unit == "mil":
        return format_nm_as_mil(value_nm)
    return format_nm_as_mm(value_nm)


def format_nm_for_entry(value_nm: int, unit: SizeUnit) -> str:
    if unit == "mil":
        return f"{value_nm / NM_PER_MIL:.2f}"
    return f"{value_nm / NM_PER_MM:.3f}"


def annular_width_nm(via_diameter_nm: int, drill_diameter_nm: int) -> int:
    return round((via_diameter_nm - drill_diameter_nm) / 2)


def selected_sides(side: FenceSide) -> tuple[Literal["left", "right"], ...]:
    if side == "left":
        return ("left",)
    if side == "right":
        return ("right",)
    return ("left", "right")


def validate_config(config: FenceConfig) -> list[str]:
    errors: list[str] = []
    if config.via_diameter_nm <= 0:
        errors.append("Via diameter must be greater than zero.")
    if config.drill_diameter_nm <= 0:
        errors.append("Drill diameter must be greater than zero.")
    if config.drill_diameter_nm >= config.via_diameter_nm:
        errors.append("Drill diameter must be smaller than via diameter.")
    if config.spacing_nm < 0:
        errors.append("Via edge spacing must be zero or greater.")
    if config.gap_nm < 0:
        errors.append("Trace-to-via gap must be zero or greater.")
    if config.collision_margin_nm < 0:
        errors.append("Collision margin must be zero or greater.")
    return errors
