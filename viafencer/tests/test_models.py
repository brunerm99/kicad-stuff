from __future__ import annotations

from viafencer.models import format_nm_for_entry, parse_size_to_nm


def test_format_nm_for_entry_converts_mm_to_mil_display() -> None:
    assert format_nm_for_entry(parse_size_to_nm("0.600", "mm") or 0, "mil") == "23.62"
    assert format_nm_for_entry(parse_size_to_nm("0.300", "mm") or 0, "mil") == "11.81"
    assert format_nm_for_entry(parse_size_to_nm("0.250", "mm") or 0, "mil") == "9.84"


def test_format_nm_for_entry_converts_mil_to_mm_display() -> None:
    assert format_nm_for_entry(parse_size_to_nm("23.62", "mil") or 0, "mm") == "0.600"
    assert format_nm_for_entry(parse_size_to_nm("9.84", "mil") or 0, "mm") == "0.250"
