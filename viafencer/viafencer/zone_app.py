"""Tkinter UI for KiCad zone via stitching."""

from __future__ import annotations

import argparse
import logging
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .drc import diff_drc, find_kicad_cli, run_drc_snapshot
from .kicad_ipc import resolve_socket
from .models import (
    SizeUnit,
    format_nm_for_entry,
    parse_size_to_nm,
)
from .zone_geometry import filter_stitch_candidates, generate_stitch_candidates
from .zone_ipc import StitchZone, ZoneStitchSession, format_nm

DEFAULT_TIMEOUT_MS = 60_000
LOGGER = logging.getLogger(__name__)

SIZE_FIELDS = {
    "x_spacing": "X spacing",
    "y_spacing": "Y spacing",
}


class ZoneStitcherApp:
    def __init__(
        self,
        root: tk.Tk,
        socket_path: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ):
        self.root = root
        self.socket_path = resolve_socket(socket_path)
        self.timeout_ms = timeout_ms
        self.session: ZoneStitchSession | None = None
        self.zones: list[StitchZone] = []
        self.current_size_unit: SizeUnit = "mm"

        self.status_var = tk.StringVar(value=f"Socket: {self.socket_path}")
        self.zone_var = tk.StringVar(value="No selected zone loaded")
        self.details_var = tk.StringVar(value="Via: -")
        self.size_unit_var = tk.StringVar(value="mm")
        self.staggered_var = tk.BooleanVar(value=True)
        self.drc_diff_var = tk.BooleanVar(value=False)
        self.input_vars = {
            "x_spacing": tk.StringVar(value="2.000"),
            "y_spacing": tk.StringVar(value="2.000"),
        }

        self.root.title("KiCad Zone Stitcher")
        self.root.geometry("760x330")
        self._build_ui()
        self.size_unit_var.trace_add("write", lambda *_args: self._unit_changed())
        self.root.after(100, self.refresh_selection)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Label(top, textvariable=self.status_var, wraplength=430).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(top, textvariable=self.zone_var).grid(row=1, column=0, sticky="w")
        ttk.Label(top, textvariable=self.details_var).grid(row=2, column=0, sticky="w")

        buttons = ttk.Frame(top)
        buttons.grid(row=0, column=1, rowspan=3, sticky="e")
        ttk.Button(buttons, text="Refresh Selection", command=self.refresh_selection).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(buttons, text="Stitch Zone", command=self.stitch_zone).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(buttons, text="Close", command=self.root.destroy).grid(row=0, column=2)

        settings = ttk.LabelFrame(self.root, text="Stitch", padding=10)
        settings.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        for column, minsize in enumerate((190, 190, 180, 180)):
            settings.columnconfigure(column, weight=1, minsize=minsize)

        self._add_unit_selector(settings, row=0, column=0)
        self._add_entry(settings, "x_spacing", row=0, column=1)
        self._add_entry(settings, "y_spacing", row=0, column=2)
        self._add_staggered_checkbox(settings, row=0, column=3)
        self._add_drc_checkbox(settings, row=1, column=0)

    def _add_entry(self, parent: ttk.Frame, name: str, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=SIZE_FIELDS[name]).grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.input_vars[name], width=14).grid(
            row=1, column=0, sticky="ew"
        )

    def _add_unit_selector(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Units").grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(
            frame,
            textvariable=self.size_unit_var,
            values=["mm", "mil"],
            state="readonly",
            width=8,
        )
        combo.grid(row=1, column=0, sticky="ew")

    def _add_staggered_checkbox(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(16, 8))
        ttk.Checkbutton(frame, text="Stagger rows", variable=self.staggered_var).grid(
            row=0, column=0, sticky="w"
        )

    def _add_drc_checkbox(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, columnspan=3, sticky="ew", padx=(0, 8), pady=(8, 8))
        ttk.Checkbutton(
            frame,
            text="Run DRC diff before/after",
            variable=self.drc_diff_var,
        ).grid(row=0, column=0, sticky="w")

    def _connect(self) -> None:
        LOGGER.info(
            "Connecting to KiCad IPC socket %s with timeout %d ms",
            self.socket_path,
            self.timeout_ms,
        )
        try:
            self.session = ZoneStitchSession(self.socket_path, timeout_ms=self.timeout_ms)
            self.status_var.set(f"Connected: {self.socket_path}")
            LOGGER.info("Connected to KiCad IPC")
        except Exception as exc:
            self.session = None
            self.status_var.set(f"Connection failed: {exc}")
            LOGGER.exception("KiCad IPC connection failed")
            messagebox.showerror("KiCad IPC connection failed", str(exc))

    def refresh_selection(self) -> None:
        if self.session is None:
            self._connect()
        if self.session is None:
            return

        try:
            self.zones = self.session.selected_stitch_zones()
        except Exception as exc:
            self.status_var.set(f"Selection refresh failed: {exc}")
            LOGGER.exception("Selection refresh failed")
            messagebox.showerror("Selection refresh failed", str(exc))
            return

        if len(self.zones) == 1:
            zone = self.zones[0]
            self.zone_var.set(
                f"Selected zone: {zone.label} ({zone.net_name}), {len(zone.layers)} filled layers"
            )
            self.details_var.set(
                "Via: "
                f"{format_nm(zone.via_diameter_nm)} / drill {format_nm(zone.drill_diameter_nm)}, "
                f"clearance {format_nm(zone.clearance_nm)}"
            )
        else:
            self.zone_var.set(f"{len(self.zones)} selected stitchable zones")
            self.details_var.set("Select exactly one filled copper zone.")
        self.status_var.set("Selection refreshed")
        LOGGER.info("Selection refreshed: %d stitchable zones", len(self.zones))

    def stitch_zone(self) -> None:
        if self.session is None:
            self._connect()
        if self.session is None:
            return
        if not self.zones:
            self.refresh_selection()
        if len(self.zones) != 1:
            messagebox.showwarning("Select one zone", "Select exactly one filled copper zone.")
            return

        zone = self.zones[0]
        x_spacing, y_spacing, errors = self._spacing_from_inputs()
        if errors:
            messagebox.showerror("Invalid stitch settings", "\n".join(errors))
            return
        if len(zone.layers) < 2:
            messagebox.showwarning(
                "Zone has one layer",
                "The selected zone must have filled copper on at least two layers.",
            )
            return

        drc_before = None
        drc_cli = None
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if self.drc_diff_var.get():
            try:
                drc_cli = self._require_kicad_cli()
                temp_dir = tempfile.TemporaryDirectory(prefix="zonestitch-drc-")
                before_board = Path(temp_dir.name) / "before.kicad_pcb"
                before_json = Path(temp_dir.name) / "before-drc.json"
                self.status_var.set("Running DRC before stitch...")
                self.root.update_idletasks()
                self.session.save_board_copy(str(before_board))
                drc_before = run_drc_snapshot(str(before_board), str(before_json), drc_cli)
            except Exception as exc:
                if temp_dir is not None:
                    temp_dir.cleanup()
                LOGGER.exception("Pre-stitch DRC failed")
                messagebox.showerror("DRC failed", str(exc))
                return

        try:
            self.status_var.set("Generating stitch candidates...")
            self.root.update_idletasks()
            generation = generate_stitch_candidates(
                zone.polygons_by_layer,
                x_spacing,
                y_spacing,
                zone.via_diameter_nm,
                self.staggered_var.get(),
            )
            obstacles = self.session.stitch_obstacles(zone)
            collision = filter_stitch_candidates(
                generation.candidates,
                zone.via_diameter_nm,
                zone.via_layers,
                zone.net_name,
                zone.clearance_nm,
                obstacles,
            )
            LOGGER.info(
                "Zone stitch candidates accepted=%d skipped_generated=%d "
                "skipped_obstacle=%d skipped_geometry=%d",
                len(collision.accepted),
                collision.skipped_generated_overlap,
                collision.skipped_obstacle_overlap,
                len(generation.skipped),
            )
            if not collision.accepted:
                self.status_var.set("No vias created")
                messagebox.showwarning(
                    "No vias created",
                    "No non-colliding via locations were found inside the selected zone.",
                )
                return

            self.status_var.set(f"Creating {len(collision.accepted)} stitch vias...")
            self.root.update_idletasks()
            summary = self.session.create_stitch_vias(
                [candidate.center for candidate in collision.accepted],
                zone,
            )
        except Exception as exc:
            LOGGER.exception("Zone stitching failed")
            self.status_var.set("Zone stitching failed; see dialog or terminal log")
            messagebox.showerror("Zone stitching failed", str(exc))
            return

        drc_summary = ""
        if drc_before is not None and temp_dir is not None and drc_cli is not None:
            try:
                after_board = Path(temp_dir.name) / "after.kicad_pcb"
                after_json = Path(temp_dir.name) / "after-drc.json"
                self.status_var.set("Running DRC after stitch...")
                self.root.update_idletasks()
                self.session.save_board_copy(str(after_board))
                drc_after = run_drc_snapshot(str(after_board), str(after_json), drc_cli)
                drc_summary = diff_drc(drc_before, drc_after).summary()
                messagebox.showinfo("DRC diff", drc_summary)
            except Exception as exc:
                LOGGER.exception("Post-stitch DRC failed")
                messagebox.showerror("DRC failed", str(exc))
            finally:
                temp_dir.cleanup()

        skipped_count = (
            len(generation.skipped)
            + collision.skipped_generated_overlap
            + collision.skipped_obstacle_overlap
        )
        suffix = f"; skipped {skipped_count}" if skipped_count else ""
        if drc_summary:
            suffix += f"; {drc_summary}"
        self.status_var.set(f"Created {summary.via_count} stitch vias{suffix}")
        LOGGER.info("Created %d zone stitch vias", summary.via_count)

    def _spacing_from_inputs(self) -> tuple[int, int, list[str]]:
        parsed: dict[str, int] = {}
        errors: list[str] = []
        size_unit = self._selected_size_unit()
        for name, label in SIZE_FIELDS.items():
            value = parse_size_to_nm(self.input_vars[name].get(), size_unit)
            if value is None or value <= 0:
                errors.append(f"{label} must be greater than zero.")
            else:
                parsed[name] = value
        return parsed.get("x_spacing", 0), parsed.get("y_spacing", 0), errors

    def _unit_changed(self) -> None:
        new_unit = self._selected_size_unit()
        old_unit = self.current_size_unit
        if new_unit == old_unit:
            return

        for variable in self.input_vars.values():
            value_nm = parse_size_to_nm(variable.get(), old_unit)
            if value_nm is not None:
                variable.set(format_nm_for_entry(value_nm, new_unit))
        self.current_size_unit = new_unit

    def _selected_size_unit(self) -> SizeUnit:
        return "mil" if self.size_unit_var.get() == "mil" else "mm"

    def _require_kicad_cli(self) -> str:
        path = find_kicad_cli()
        if path is None:
            raise RuntimeError(
                "Could not find kicad-cli. Install it or put it on PATH to use DRC diff."
            )
        return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a conservative via stitch grid inside a selected KiCad zone."
    )
    parser.add_argument(
        "--socket",
        default=None,
        help="KiCad IPC socket. Defaults to KICAD_API_SOCKET or ipc:///tmp/kicad/api.sock.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help="KiCad IPC request timeout in milliseconds.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional path for a debug log file. Logs are also written to stderr.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    args = parser.parse_args()
    _configure_logging(args.debug, args.log_file)
    LOGGER.info("Starting Zone Stitcher")

    root = tk.Tk()
    ZoneStitcherApp(root, socket_path=args.socket, timeout_ms=args.timeout_ms)
    root.mainloop()


def _configure_logging(debug: bool, log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


if __name__ == "__main__":
    main()
