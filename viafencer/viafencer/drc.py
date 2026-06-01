"""Optional KiCad CLI DRC diff support."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DrcSnapshot:
    violations: frozenset[str]
    severity_counts: dict[str, int]
    raw_count: int


@dataclass(frozen=True)
class DrcDiff:
    before_count: int
    after_count: int
    added_count: int
    removed_count: int
    before_severity_counts: dict[str, int]
    after_severity_counts: dict[str, int]

    @property
    def changed(self) -> bool:
        return self.added_count > 0 or self.removed_count > 0

    def summary(self) -> str:
        status = "DRC changed" if self.changed else "DRC unchanged"
        severity = _format_severity_counts(
            self.before_severity_counts, self.after_severity_counts
        )
        return (
            f"{status}: before={self.before_count}, after={self.after_count}, "
            f"new={self.added_count}, resolved={self.removed_count}{severity}"
        )


def find_kicad_cli() -> str | None:
    path = shutil.which("kicad-cli")
    if path:
        return path

    candidates = [
        Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"),
        Path("/Applications/KiCad.app/Contents/MacOS/kicad-cli"),
        Path.home() / "Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
        Path.home() / "Applications/KiCad.app/Contents/MacOS/kicad-cli",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def run_drc_snapshot(board_path: str, output_path: str, kicad_cli: str) -> DrcSnapshot:
    command = [
        kicad_cli,
        "pcb",
        "drc",
        "--format",
        "json",
        "--output",
        output_path,
        board_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    output = Path(output_path)
    if result.returncode != 0 and not output.exists():
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"kicad-cli DRC failed: {stderr or result.returncode}")
    if not output.exists():
        raise RuntimeError("kicad-cli DRC did not create a report.")

    with output.open("r", encoding="utf-8") as report:
        data = json.load(report)
    return parse_drc_report(data)


def parse_drc_report(data: Any) -> DrcSnapshot:
    violations = _find_violation_entries(data)
    signatures = {_violation_signature(violation) for violation in violations}
    severity_counts = Counter(_violation_severity(violation) for violation in violations)
    return DrcSnapshot(
        violations=frozenset(signatures),
        severity_counts=dict(severity_counts),
        raw_count=len(violations),
    )


def diff_drc(before: DrcSnapshot, after: DrcSnapshot) -> DrcDiff:
    added = after.violations - before.violations
    removed = before.violations - after.violations
    return DrcDiff(
        before_count=len(before.violations),
        after_count=len(after.violations),
        added_count=len(added),
        removed_count=len(removed),
        before_severity_counts=before.severity_counts,
        after_severity_counts=after.severity_counts,
    )


def _find_violation_entries(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("violations", "drc_violations", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]
        entries: list[dict[str, Any]] = []
        for value in data.values():
            entries.extend(_find_violation_entries(value))
        return entries
    if isinstance(data, list):
        entries = []
        for value in data:
            entries.extend(_find_violation_entries(value))
        return entries
    return []


def _violation_signature(violation: dict[str, Any]) -> str:
    normalized = _normalize_for_signature(violation)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_for_signature(value: Any) -> Any:
    if isinstance(value, dict):
        ignored = {"uuid", "id", "timestamp", "time"}
        return {
            key: _normalize_for_signature(child)
            for key, child in sorted(value.items())
            if key.lower() not in ignored
        }
    if isinstance(value, list):
        return [_normalize_for_signature(child) for child in value]
    return value


def _violation_severity(violation: dict[str, Any]) -> str:
    for key in ("severity", "type", "category"):
        value = violation.get(key)
        if value:
            return str(value)
    return "unknown"


def _format_severity_counts(before: dict[str, int], after: dict[str, int]) -> str:
    keys = sorted(set(before) | set(after))
    if not keys:
        return ""
    changes = ", ".join(f"{key}: {before.get(key, 0)}->{after.get(key, 0)}" for key in keys)
    return f" ({changes})"
