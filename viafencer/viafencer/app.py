"""Tkinter UI for Via Fencer."""

from __future__ import annotations

import argparse
import logging
import tkinter as tk
from tkinter import messagebox, ttk

from .geometry import (
    CollisionResult,
    TrackGeometry,
    filter_overlapping_candidates,
    generate_fence_candidates,
)
from .kicad_ipc import (
    KiCadFenceSession,
    LayerOption,
    choose_default_ground_net,
    resolve_socket,
)
from .models import (
    FenceConfig,
    SizeUnit,
    annular_width_nm,
    format_nm_for_entry,
    format_nm_as_unit,
    parse_size_to_nm,
    validate_config,
)

DEFAULT_TIMEOUT_MS = 60_000
LOGGER = logging.getLogger(__name__)

COLLISION_GENERATED = "Generated only"
COLLISION_EXISTING = "Generated + existing vias/pads"

SIDE_LABELS = {
    "Both": "both",
    "Left": "left",
    "Right": "right",
}

SIZE_FIELDS = {
    "via_diameter": "Via diameter",
    "drill_diameter": "Drill diameter",
    "spacing": "Via edge spacing",
    "gap": "Trace gap",
    "collision_margin": "Collision margin",
}


class ViaFencerApp:
    def __init__(
        self,
        root: tk.Tk,
        socket_path: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ):
        self.root = root
        self.socket_path = resolve_socket(socket_path)
        self.timeout_ms = timeout_ms
        self.session: KiCadFenceSession | None = None
        self.tracks: list[TrackGeometry] = []
        self.layer_options: list[LayerOption] = []
        self.layer_by_label: dict[str, int] = {}
        self.current_size_unit: SizeUnit = "mm"

        self.status_var = tk.StringVar(value=f"Socket: {self.socket_path}")
        self.selection_var = tk.StringVar(value="No selection loaded")
        self.size_unit_var = tk.StringVar(value="mm")
        self.annular_var = tk.StringVar(value="Annular ring: -")
        self.net_var = tk.StringVar(value="")
        self.start_layer_var = tk.StringVar(value="")
        self.stop_layer_var = tk.StringVar(value="")
        self.side_var = tk.StringVar(value="Both")
        self.collision_var = tk.StringVar(value=COLLISION_GENERATED)
        self.group_var = tk.BooleanVar(value=True)
        self.input_vars = {
            "via_diameter": tk.StringVar(value="0.600"),
            "drill_diameter": tk.StringVar(value="0.300"),
            "spacing": tk.StringVar(value="1.000"),
            "gap": tk.StringVar(value="0.250"),
            "collision_margin": tk.StringVar(value="0.000"),
        }

        self.root.title("KiCad Via Fencer")
        self.root.geometry("980x500")
        self._build_ui()
        for variable in self.input_vars.values():
            variable.trace_add("write", lambda *_args: self._update_annular())
        self.size_unit_var.trace_add("write", lambda *_args: self._unit_changed())

        self._update_annular()
        self.root.after(100, self.refresh_selection)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Label(top, textvariable=self.status_var, wraplength=560).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(top, textvariable=self.selection_var).grid(row=1, column=0, sticky="w")

        buttons = ttk.Frame(top)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(buttons, text="Refresh Selection", command=self.refresh_selection).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(buttons, text="Create Fence", command=self.create_fence).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(buttons, text="Close", command=self.root.destroy).grid(row=0, column=2)

        settings = ttk.LabelFrame(self.root, text="Fence", padding=10)
        settings.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        for column, minsize in enumerate((190, 230, 170, 210)):
            settings.columnconfigure(column, weight=1, minsize=minsize)

        self._add_unit_selector(settings, row=0, column=0)
        self._add_entry(settings, "via_diameter", row=0, column=1)
        self._add_entry(settings, "drill_diameter", row=0, column=2)
        ttk.Label(settings, textvariable=self.annular_var).grid(
            row=0, column=3, sticky="w", padx=(0, 8), pady=(0, 8)
        )

        self._add_entry(settings, "spacing", row=1, column=0)
        self._add_entry(settings, "gap", row=1, column=1)
        self._add_side_selector(settings, row=1, column=2)
        self._add_group_checkbox(settings, row=1, column=3)

        self._add_net_selector(settings, row=2, column=0)
        self._add_collision_selector(settings, row=2, column=1)
        self._add_entry(settings, "collision_margin", row=2, column=2)

        self.start_layer_combo = self._add_layer_selector(
            settings, "Start layer", self.start_layer_var, row=3, column=0
        )
        self.stop_layer_combo = self._add_layer_selector(
            settings, "Stop layer", self.stop_layer_var, row=3, column=1
        )

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

    def _add_side_selector(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Sides").grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(
            frame,
            textvariable=self.side_var,
            values=list(SIDE_LABELS),
            state="readonly",
            width=12,
        )
        combo.grid(row=1, column=0, sticky="ew")

    def _add_group_checkbox(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(16, 8))
        ttk.Checkbutton(frame, text="Group generated vias", variable=self.group_var).grid(
            row=0, column=0, sticky="w"
        )

    def _add_net_selector(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Via net").grid(row=0, column=0, sticky="w")
        self.net_combo = ttk.Combobox(frame, textvariable=self.net_var, state="readonly")
        self.net_combo.grid(row=1, column=0, sticky="ew")

    def _add_collision_selector(self, parent: ttk.Frame, row: int, column: int) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Collision").grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(
            frame,
            textvariable=self.collision_var,
            values=[COLLISION_GENERATED, COLLISION_EXISTING],
            state="readonly",
            width=26,
        )
        combo.grid(row=1, column=0, sticky="ew")

    def _add_layer_selector(
        self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, column: int
    ) -> ttk.Combobox:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(frame, textvariable=variable, state="readonly")
        combo.grid(row=1, column=0, sticky="ew")
        return combo

    def _connect(self) -> None:
        LOGGER.info(
            "Connecting to KiCad IPC socket %s with timeout %d ms",
            self.socket_path,
            self.timeout_ms,
        )
        try:
            self.session = KiCadFenceSession(self.socket_path, timeout_ms=self.timeout_ms)
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
            LOGGER.info("Refreshing selected tracks and nets")
            self.tracks = self.session.selected_tracks()
            self._refresh_nets()
            self._refresh_layers()
        except Exception as exc:
            self.status_var.set(f"Selection refresh failed: {exc}")
            LOGGER.exception("Selection refresh failed")
            messagebox.showerror("Selection refresh failed", str(exc))
            return

        self.selection_var.set(f"{len(self.tracks)} selected tracks/arcs loaded")
        self.status_var.set("Selection refreshed")
        LOGGER.info("Selection refreshed: %d tracks/arcs", len(self.tracks))

    def create_fence(self) -> None:
        if self.session is None:
            self._connect()
        if self.session is None:
            return
        if not self.tracks:
            self.refresh_selection()
        if not self.tracks:
            messagebox.showwarning("No selected tracks", "Select RF tracks or arcs first.")
            return

        config, errors = self._config_from_inputs()
        if errors or config is None:
            messagebox.showerror("Invalid fence settings", "\n".join(errors))
            return
        if not self.net_var.get():
            messagebox.showerror("Missing via net", "Choose a via net.")
            return
        layer_pair, layer_errors = self._selected_layer_pair()
        if layer_errors or layer_pair is None:
            messagebox.showerror("Invalid via layers", "\n".join(layer_errors))
            return
        start_layer, stop_layer = layer_pair

        LOGGER.info(
            "Generating fence candidates for %d tracks: "
            "via=%d nm drill=%d nm edge_spacing=%d nm gap=%d nm sides=%s layers=%s->%s",
            len(self.tracks),
            config.via_diameter_nm,
            config.drill_diameter_nm,
            config.spacing_nm,
            config.gap_nm,
            config.sides,
            self.start_layer_var.get(),
            self.stop_layer_var.get(),
        )
        generation = generate_fence_candidates(self.tracks, config)
        obstacles = []
        if self.collision_var.get() == COLLISION_EXISTING:
            try:
                LOGGER.info("Loading existing via/pad obstacles")
                obstacles = self.session.existing_obstacles()
            except Exception as exc:
                LOGGER.exception("Obstacle lookup failed")
                messagebox.showerror("Obstacle lookup failed", str(exc))
                return

        collision = filter_overlapping_candidates(
            generation.candidates,
            config.via_diameter_nm,
            config.collision_margin_nm,
            obstacles,
        )
        LOGGER.info(
            "Fence candidate filtering accepted=%d skipped_generated=%d skipped_obstacle=%d skipped_geometry=%d",
            len(collision.accepted),
            collision.skipped_generated_overlap,
            collision.skipped_obstacle_overlap,
            len(generation.skipped),
        )
        if not collision.accepted:
            self._set_preview_status(generation.skipped, collision)
            messagebox.showwarning("No vias created", "No non-overlapping via locations found.")
            return

        try:
            self.status_var.set(f"Creating {len(collision.accepted)} fence vias...")
            self.root.update_idletasks()
            LOGGER.info(
                "Creating %d fence vias on net %s from %s to %s",
                len(collision.accepted),
                self.net_var.get(),
                self.start_layer_var.get(),
                self.stop_layer_var.get(),
            )
            summary = self.session.create_fence_vias(
                [candidate.center for candidate in collision.accepted],
                self.net_var.get(),
                config.via_diameter_nm,
                config.drill_diameter_nm,
                start_layer=start_layer,
                end_layer=stop_layer,
                grouped=self.group_var.get(),
            )
        except Exception as exc:
            self.status_var.set("Via creation failed; see dialog or terminal log")
            LOGGER.exception("Via creation failed")
            messagebox.showerror("Via creation failed", str(exc))
            return

        suffix = " in one group" if summary.grouped else ""
        skipped = self._skipped_summary(generation.skipped, collision)
        self.status_var.set(f"Created {summary.via_count} fence vias{suffix}{skipped}")
        LOGGER.info("Created %d fence vias grouped=%s", summary.via_count, summary.grouped)

    def _refresh_nets(self) -> None:
        if self.session is None:
            return
        names = self.session.net_names()
        self.net_combo.configure(values=names)
        current = self.net_var.get()
        if current in names:
            return
        self.net_var.set(choose_default_ground_net(names))

    def _refresh_layers(self) -> None:
        if self.session is None:
            return

        self.layer_options = self.session.copper_layer_options()
        labels = [option.label for option in self.layer_options]
        self.layer_by_label = {option.label: option.layer for option in self.layer_options}
        self.start_layer_combo.configure(values=labels)
        self.stop_layer_combo.configure(values=labels)

        if not labels:
            self.start_layer_var.set("")
            self.stop_layer_var.set("")
            return

        if self.start_layer_var.get() not in self.layer_by_label:
            self.start_layer_var.set(labels[0])
        if self.stop_layer_var.get() not in self.layer_by_label:
            self.stop_layer_var.set(labels[-1])

    def _selected_layer_pair(self) -> tuple[tuple[int, int] | None, list[str]]:
        if len(self.layer_options) < 2:
            return None, ["The board must have at least two enabled copper layers."]

        start_label = self.start_layer_var.get()
        stop_label = self.stop_layer_var.get()
        errors: list[str] = []
        start_layer = self.layer_by_label.get(start_label)
        stop_layer = self.layer_by_label.get(stop_label)
        if start_layer is None:
            errors.append("Choose a valid start layer.")
        if stop_layer is None:
            errors.append("Choose a valid stop layer.")
        if errors:
            return None, errors

        layer_order = {option.layer: index for index, option in enumerate(self.layer_options)}
        if start_layer == stop_layer:
            errors.append("Start layer and stop layer must be different.")
        elif layer_order[start_layer] >= layer_order[stop_layer]:
            errors.append("Start layer must be above the stop layer in the board stack.")

        return (None, errors) if errors else ((start_layer, stop_layer), [])

    def _config_from_inputs(self) -> tuple[FenceConfig | None, list[str]]:
        parsed: dict[str, int] = {}
        errors: list[str] = []
        size_unit = self._selected_size_unit()
        for name, label in SIZE_FIELDS.items():
            value = parse_size_to_nm(self.input_vars[name].get(), size_unit)
            if value is None:
                errors.append(f"{label} is invalid.")
            else:
                parsed[name] = value

        if errors:
            return None, errors

        config = FenceConfig(
            via_diameter_nm=parsed["via_diameter"],
            drill_diameter_nm=parsed["drill_diameter"],
            spacing_nm=parsed["spacing"],
            gap_nm=parsed["gap"],
            collision_margin_nm=parsed["collision_margin"],
            sides=SIDE_LABELS.get(self.side_var.get(), "both"),  # type: ignore[arg-type]
        )
        errors.extend(validate_config(config))
        return (None, errors) if errors else (config, [])

    def _update_annular(self) -> None:
        size_unit = self._selected_size_unit()
        diameter = parse_size_to_nm(self.input_vars["via_diameter"].get(), size_unit)
        drill = parse_size_to_nm(self.input_vars["drill_diameter"].get(), size_unit)
        if diameter is None or drill is None or drill >= diameter:
            self.annular_var.set("Annular ring: -")
            return
        annular = annular_width_nm(diameter, drill)
        self.annular_var.set(f"Annular ring: {format_nm_as_unit(annular, size_unit)}")

    def _unit_changed(self) -> None:
        new_unit = self._selected_size_unit()
        old_unit = self.current_size_unit
        if new_unit == old_unit:
            self._update_annular()
            return

        for variable in self.input_vars.values():
            value_nm = parse_size_to_nm(variable.get(), old_unit)
            if value_nm is not None:
                variable.set(format_nm_for_entry(value_nm, new_unit))

        self.current_size_unit = new_unit
        self._update_annular()

    def _selected_size_unit(self) -> SizeUnit:
        return "mil" if self.size_unit_var.get() == "mil" else "mm"

    def _set_preview_status(
        self, geometry_skips: list[str], collision: CollisionResult
    ) -> None:
        skipped = self._skipped_summary(geometry_skips, collision)
        self.status_var.set(f"No vias created{skipped}")

    def _skipped_summary(
        self, geometry_skips: list[str], collision: CollisionResult
    ) -> str:
        skipped_count = (
            len(geometry_skips)
            + collision.skipped_generated_overlap
            + collision.skipped_obstacle_overlap
        )
        return f"; skipped {skipped_count}" if skipped_count else ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create RF via fences around selected KiCad PCB tracks and arcs."
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
    LOGGER.info("Starting Via Fencer")

    root = tk.Tk()
    ViaFencerApp(root, socket_path=args.socket, timeout_ms=args.timeout_ms)
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
