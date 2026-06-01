"""Tkinter UI for RF solder-mask opening zones."""

from __future__ import annotations

import argparse
import logging
import tkinter as tk
from collections import Counter
from tkinter import messagebox, ttk

from .geometry import TrackGeometry
from .kicad_ipc import resolve_socket
from .mask_geometry import generate_mask_openings
from .mask_ipc import AUTO_MASK_LAYER, MASK_LAYER_CHOICES, MaskZoneSession
from .models import SizeUnit, format_nm_for_entry, parse_size_to_nm

DEFAULT_TIMEOUT_MS = 60_000
LOGGER = logging.getLogger(__name__)

SIZE_FIELDS = {
    "edge_offset": "Trace-edge offset",
}


class MaskExpanderApp:
    def __init__(
        self,
        root: tk.Tk,
        socket_path: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ):
        self.root = root
        self.socket_path = resolve_socket(socket_path)
        self.timeout_ms = timeout_ms
        self.session: MaskZoneSession | None = None
        self.tracks: list[TrackGeometry] = []
        self.current_size_unit: SizeUnit = "mm"

        self.status_var = tk.StringVar(value=f"Socket: {self.socket_path}")
        self.selection_var = tk.StringVar(value="No selection loaded")
        self.size_unit_var = tk.StringVar(value="mm")
        self.mask_layer_var = tk.StringVar(value=AUTO_MASK_LAYER)
        self.group_var = tk.BooleanVar(value=True)
        self.input_vars = {
            "edge_offset": tk.StringVar(value="0.100"),
        }

        self.root.title("KiCad RF Mask Expander")
        self.root.geometry("760x250")
        self._build_ui()
        self.size_unit_var.trace_add("write", lambda *_args: self._unit_changed())
        self.root.after(100, self.refresh_selection)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Label(top, textvariable=self.status_var, wraplength=450).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(top, textvariable=self.selection_var, wraplength=450).grid(
            row=1, column=0, sticky="w"
        )

        buttons = ttk.Frame(top)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(buttons, text="Refresh Selection", command=self.refresh_selection).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(buttons, text="Create Mask Zones", command=self.create_mask_zones).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(buttons, text="Close", command=self.root.destroy).grid(row=0, column=2)

        settings = ttk.LabelFrame(self.root, text="Mask Opening", padding=10)
        settings.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        for column, minsize in enumerate((160, 190, 230, 170)):
            settings.columnconfigure(column, weight=1, minsize=minsize)

        self._add_unit_selector(settings, row=0, column=0)
        self._add_entry(settings, "edge_offset", row=0, column=1)
        self._add_mask_layer_selector(settings, row=0, column=2)
        self._add_group_checkbox(settings, row=0, column=3)

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

    def _add_mask_layer_selector(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Mask layer").grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(
            frame,
            textvariable=self.mask_layer_var,
            values=list(MASK_LAYER_CHOICES),
            state="readonly",
            width=24,
        )
        combo.grid(row=1, column=0, sticky="ew")

    def _add_group_checkbox(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(16, 8))
        ttk.Checkbutton(frame, text="Group zones", variable=self.group_var).grid(
            row=0, column=0, sticky="w"
        )

    def _connect(self) -> None:
        LOGGER.info(
            "Connecting to KiCad IPC socket %s with timeout %d ms",
            self.socket_path,
            self.timeout_ms,
        )
        try:
            self.session = MaskZoneSession(self.socket_path, timeout_ms=self.timeout_ms)
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
            LOGGER.info("Refreshing selected tracks for mask expansion")
            self.tracks = self.session.selected_tracks()
        except Exception as exc:
            self.status_var.set(f"Selection refresh failed: {exc}")
            LOGGER.exception("Selection refresh failed")
            messagebox.showerror("Selection refresh failed", str(exc))
            return

        self.selection_var.set(self._selection_summary())
        self.status_var.set("Selection refreshed")
        LOGGER.info("Selection refreshed: %d tracks/arcs", len(self.tracks))

    def create_mask_zones(self) -> None:
        if self.session is None:
            self._connect()
        if self.session is None:
            return
        if not self.tracks:
            self.refresh_selection()
        if not self.tracks:
            messagebox.showwarning("No selected tracks", "Select RF tracks or arcs first.")
            return

        edge_offset, errors = self._settings_from_inputs()
        if errors or edge_offset is None:
            messagebox.showerror("Invalid mask settings", "\n".join(errors))
            return

        generation = generate_mask_openings(self.tracks, edge_offset)
        if not generation.openings:
            self.status_var.set("No mask zones created")
            messagebox.showwarning(
                "No mask zones created",
                "\n".join(generation.skipped) or "No valid selected tracks were found.",
            )
            return

        try:
            self.status_var.set(f"Creating {len(generation.openings)} mask zones...")
            self.root.update_idletasks()
            summary = self.session.create_mask_zones(
                generation.openings,
                self.mask_layer_var.get(),
                grouped=self.group_var.get(),
            )
        except Exception as exc:
            self.status_var.set("Mask zone creation failed; see dialog or terminal log")
            LOGGER.exception("Mask zone creation failed")
            messagebox.showerror("Mask zone creation failed", str(exc))
            return

        skipped_count = len(generation.skipped) + summary.skipped_layer_count
        skipped = f"; skipped {skipped_count}" if skipped_count else ""
        grouped = " in one group" if summary.grouped else ""
        self.status_var.set(f"Created {summary.zone_count} mask zones{grouped}{skipped}")

        if summary.zone_count == 0 and summary.skipped_layer_count:
            messagebox.showwarning(
                "No mask zones created",
                "Auto mask layer mode only supports selected F.Cu and B.Cu traces.",
            )
        if summary.group_error:
            messagebox.showwarning("Mask zones created without group", summary.group_error)
        LOGGER.info("Created %d mask zones grouped=%s", summary.zone_count, summary.grouped)

    def _settings_from_inputs(self) -> tuple[int | None, list[str]]:
        size_unit = self._selected_size_unit()
        edge_offset = parse_size_to_nm(self.input_vars["edge_offset"].get(), size_unit)
        if edge_offset is None:
            return None, ["Trace-edge offset is invalid."]
        if edge_offset < 0:
            return None, ["Trace-edge offset must be zero or greater."]
        return edge_offset, []

    def _selection_summary(self) -> str:
        if not self.tracks:
            return "No selected tracks/arcs loaded"
        if self.session is None:
            return f"{len(self.tracks)} selected tracks/arcs loaded"

        counts = Counter(self.session.display_layer_name(track.layer) for track in self.tracks)
        layers = ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))
        return f"{len(self.tracks)} selected tracks/arcs loaded ({layers})"

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create side-offset solder-mask opening zones around selected RF tracks."
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
    LOGGER.info("Starting RF Mask Expander")

    root = tk.Tk()
    MaskExpanderApp(root, socket_path=args.socket, timeout_ms=args.timeout_ms)
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
