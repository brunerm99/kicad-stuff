"""Tkinter UI for Downselector."""

from __future__ import annotations

import argparse
import tkinter as tk
from tkinter import messagebox, ttk

from .kicad_ipc import KiCadRouteSession, resolve_socket
from .models import (
    ALL,
    FilterCriteria,
    RouteItemInfo,
    filter_route_items,
    format_nm_as_unit,
    option_values,
    parse_size_to_nm,
    SizeUnit,
    size_option_values,
    summarize_items,
    variation_rows,
)

SIZE_FILTER_FIELDS = ("track_width", "via_diameter", "via_drill")


class DownselectorApp:
    """UI controller for filtering the refreshed KiCad route selection."""

    def __init__(
        self,
        root: tk.Tk,
        socket_path: str | None = None,
        timeout_ms: int = 5000,
    ):
        self.root = root
        self.socket_path = resolve_socket(socket_path)
        self.timeout_ms = timeout_ms
        self.session: KiCadRouteSession | None = None
        self.items: list[RouteItemInfo] = []
        self.filtered_items: list[RouteItemInfo] = []
        self.row_filters: dict[str, dict[str, str | int]] = {}
        self.filter_widgets: dict[str, ttk.Combobox] = {}

        self.status_var = tk.StringVar(value=f"Socket: {self.socket_path}")
        self.selection_var = tk.StringVar(value="No selection loaded")
        self.match_var = tk.StringVar(value="0 matches")
        self.size_unit_var = tk.StringVar(value="mm")

        self.root.title("KiCad Downselector")
        self.root.geometry("1180x720")
        self._build_ui()
        self._connect()
        self.refresh_selection()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)

        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.selection_var).grid(row=1, column=0, sticky="w")

        controls = ttk.Frame(top)
        controls.grid(row=0, column=1, rowspan=2, sticky="e")

        unit_frame = ttk.Frame(controls)
        unit_frame.grid(row=0, column=0, padx=(0, 12))
        ttk.Label(unit_frame, text="Units").grid(row=0, column=0, sticky="w")
        unit_combo = ttk.Combobox(
            unit_frame,
            textvariable=self.size_unit_var,
            values=["mm", "mil"],
            state="readonly",
            width=6,
        )
        unit_combo.grid(row=1, column=0, sticky="ew")
        unit_combo.bind("<<ComboboxSelected>>", self._unit_changed)

        buttons = ttk.Frame(controls)
        buttons.grid(row=0, column=1)
        ttk.Button(buttons, text="Refresh Selection", command=self.refresh_selection).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(buttons, text="Select Matches In KiCad", command=self.select_matches).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(buttons, text="Reset Filters", command=self.reset_filters).grid(
            row=0, column=2, padx=(0, 6)
        )
        ttk.Button(buttons, text="Close", command=self.root.destroy).grid(row=0, column=3)

        filters = ttk.LabelFrame(self.root, text="Filters", padding=10)
        filters.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
        for index in range(10):
            filters.columnconfigure(index, weight=1)

        self._add_filter(filters, "item_kind", "Type", ["All", "Tracks only", "Vias only"], 0)
        self._add_filter(filters, "track_width", "Track width", [ALL], 1)
        self._add_filter(filters, "track_layer", "Track layer", [ALL], 2)
        self._add_filter(filters, "via_diameter", "Via diameter", [ALL], 3)
        self._add_filter(filters, "via_drill", "Via drill", [ALL], 4)
        self._add_filter(filters, "via_layer_pair", "Via layers", [ALL], 5)
        self._add_filter(filters, "via_type", "Via type", [ALL], 6)
        self._add_filter(filters, "net_class", "Net class", [ALL], 7)
        self._add_filter(filters, "net", "Net", [ALL], 8)
        self._add_filter(filters, "locked", "Locked", [ALL, "Locked", "Unlocked"], 9)

        table_frame = ttk.Frame(self.root, padding=(10, 4, 10, 10))
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = (
            "count",
            "kind",
            "track_width",
            "via_diameter",
            "via_drill",
            "layer",
            "via_type",
            "net_class",
            "net",
            "locked",
        )
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = {
            "count": "Count",
            "kind": "Type",
            "track_width": "Track Width",
            "via_diameter": "Via Diameter",
            "via_drill": "Via Drill",
            "layer": "Layer/Layers",
            "via_type": "Via Type",
            "net_class": "Net Class",
            "net": "Net",
            "locked": "Locked",
        }
        widths = {
            "count": 70,
            "kind": 80,
            "track_width": 110,
            "via_diameter": 120,
            "via_drill": 110,
            "layer": 170,
            "via_type": 130,
            "net_class": 140,
            "net": 160,
            "locked": 100,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=60, stretch=True)

        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._variation_selected)

        footer = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.match_var).grid(row=0, column=0, sticky="w")

    def _add_filter(
        self,
        parent: ttk.Frame,
        name: str,
        label: str,
        values: list[str],
        column: int,
    ) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=column, sticky="ew", padx=(0, 8))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(frame, values=values, state="readonly", width=15)
        combo.set(values[0])
        combo.grid(row=1, column=0, sticky="ew")
        combo.bind("<<ComboboxSelected>>", lambda _event: self.update_matches())
        self.filter_widgets[name] = combo

    def _connect(self) -> None:
        try:
            self.session = KiCadRouteSession(self.socket_path, timeout_ms=self.timeout_ms)
            self.status_var.set(f"Connected: {self.socket_path}")
        except Exception as exc:
            self.session = None
            self.status_var.set(f"Connection failed: {exc}")
            messagebox.showerror("KiCad IPC connection failed", str(exc))

    def refresh_selection(self) -> None:
        if self.session is None:
            self._connect()
        if self.session is None:
            return

        try:
            self.items = self.session.selected_route_items()
        except Exception as exc:
            self.status_var.set(f"Selection refresh failed: {exc}")
            messagebox.showerror("Selection refresh failed", str(exc))
            return

        self.selection_var.set(summarize_items(self.items))
        self._update_filter_options(reset=True)
        self.update_matches()

    def reset_filters(self) -> None:
        self._update_filter_options(reset=True)
        self.update_matches()

    def update_matches(self) -> None:
        criteria = self._criteria()
        self.filtered_items = filter_route_items(self.items, criteria)
        self.match_var.set(f"{len(self.filtered_items)} matches from {len(self.items)} loaded route items")
        self._populate_variations()

    def select_matches(self) -> None:
        self.update_matches()
        if self.session is None:
            self._connect()
        if self.session is None:
            return

        try:
            selected_count = self.session.replace_selection(self.filtered_items)
        except Exception as exc:
            self.status_var.set(f"KiCad selection update failed: {exc}")
            messagebox.showerror("KiCad selection update failed", str(exc))
            return

        self.status_var.set(f"Selected {selected_count} matching items in KiCad")

    def _unit_changed(self, _event: tk.Event | None = None) -> None:
        selected_sizes = {
            name: parse_size_to_nm(self.filter_widgets[name].get())
            for name in SIZE_FILTER_FIELDS
        }
        self._update_filter_options(reset=False, selected_sizes=selected_sizes)
        self.update_matches()

    def _criteria(self) -> FilterCriteria:
        kind_label = self.filter_widgets["item_kind"].get()
        if kind_label == "Tracks only":
            item_kind = "track"
        elif kind_label == "Vias only":
            item_kind = "via"
        else:
            item_kind = "all"

        return FilterCriteria(
            item_kind=item_kind,
            track_width=self.filter_widgets["track_width"].get(),
            track_layer=self.filter_widgets["track_layer"].get(),
            via_diameter=self.filter_widgets["via_diameter"].get(),
            via_drill=self.filter_widgets["via_drill"].get(),
            via_layer_pair=self.filter_widgets["via_layer_pair"].get(),
            via_type=self.filter_widgets["via_type"].get(),
            net_class=self.filter_widgets["net_class"].get(),
            net=self.filter_widgets["net"].get(),
            locked=self.filter_widgets["locked"].get(),
        )

    def _size_unit(self) -> SizeUnit:
        return "mil" if self.size_unit_var.get() == "mil" else "mm"

    def _update_filter_options(
        self,
        reset: bool = False,
        selected_sizes: dict[str, int | None] | None = None,
    ) -> None:
        size_unit = self._size_unit()
        options = {
            "item_kind": ["All", "Tracks only", "Vias only"],
            "track_width": [ALL] + size_option_values(
                (item for item in self.items if item.kind == "track"),
                "track_width_nm",
                size_unit,
            ),
            "track_layer": [ALL] + option_values(
                (item for item in self.items if item.kind == "track"), "track_layer"
            ),
            "via_diameter": [ALL] + size_option_values(
                (item for item in self.items if item.kind == "via"),
                "via_diameter_nm",
                size_unit,
            ),
            "via_drill": [ALL] + size_option_values(
                (item for item in self.items if item.kind == "via"),
                "via_drill_nm",
                size_unit,
            ),
            "via_layer_pair": [ALL] + option_values(
                (item for item in self.items if item.kind == "via"), "via_layer_pair"
            ),
            "via_type": [ALL] + option_values(
                (item for item in self.items if item.kind == "via"), "via_type"
            ),
            "net_class": [ALL] + option_values(self.items, "net_class"),
            "net": [ALL] + option_values(self.items, "net"),
            "locked": [ALL, "Locked", "Unlocked"],
        }

        for name, values in options.items():
            combo = self.filter_widgets[name]
            current = combo.get()
            combo.configure(values=values)
            if selected_sizes and name in selected_sizes and selected_sizes[name]:
                converted = format_nm_as_unit(selected_sizes[name], size_unit)
                combo.set(converted if converted in values else values[0])
            elif reset or current not in values:
                combo.set(values[0])
            else:
                combo.set(current)

    def _populate_variations(self) -> None:
        self.row_filters.clear()
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)

        for index, row in enumerate(
            variation_rows(self.filtered_items, size_unit=self._size_unit())
        ):
            item_id = f"variation-{index}"
            self.row_filters[item_id] = row
            self.tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    row["count"],
                    row["kind"],
                    row["track_width"],
                    row["via_diameter"],
                    row["via_drill"],
                    row["layer"],
                    row["via_type"],
                    row["net_class"],
                    row["net"],
                    row["locked"],
                ),
            )

    def _variation_selected(self, _event: tk.Event) -> None:
        selected = self.tree.selection()
        if not selected:
            return

        row = self.row_filters.get(selected[0])
        if row is None:
            return

        self.filter_widgets["item_kind"].set(
            "Tracks only" if row["kind"] == "Track" else "Vias only"
        )

        if row["kind"] == "Track":
            self._set_combo_if_available("track_width", str(row["track_width"]))
            self._set_combo_if_available("track_layer", str(row["layer"]))
            self.filter_widgets["via_diameter"].set(ALL)
            self.filter_widgets["via_drill"].set(ALL)
            self.filter_widgets["via_layer_pair"].set(ALL)
            self.filter_widgets["via_type"].set(ALL)
        else:
            self.filter_widgets["track_width"].set(ALL)
            self.filter_widgets["track_layer"].set(ALL)
            self._set_combo_if_available("via_diameter", str(row["via_diameter"]))
            self._set_combo_if_available("via_drill", str(row["via_drill"]))
            self._set_combo_if_available("via_layer_pair", str(row["layer"]))
            self._set_combo_if_available("via_type", str(row["via_type"]))

        self._set_combo_if_available("net_class", str(row["net_class"]))
        self._set_combo_if_available("net", str(row["net"]))
        self._set_combo_if_available("locked", str(row["locked"]))
        self.update_matches()

    def _set_combo_if_available(self, name: str, value: str) -> None:
        combo = self.filter_widgets[name]
        values = combo.cget("values")
        if isinstance(values, str):
            values = combo.tk.splitlist(values)
        if value in values:
            combo.set(value)
        else:
            combo.set(ALL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Downselect selected KiCad PCB tracks and vias through the KiCad 10 IPC API."
    )
    parser.add_argument(
        "--socket",
        default=None,
        help="KiCad IPC socket. Defaults to KICAD_API_SOCKET or ipc:///tmp/kicad/api.sock.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=5000,
        help="KiCad IPC request timeout in milliseconds.",
    )
    args = parser.parse_args(argv)

    root = tk.Tk()
    DownselectorApp(root, socket_path=args.socket, timeout_ms=args.timeout_ms)
    root.mainloop()
    return 0
