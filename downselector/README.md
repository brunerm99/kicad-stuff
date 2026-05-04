# Downselector

*Yeah it's ugly, but it works.*

![downselector screenshot](../static/downselector_screenshot.png)

## Requirements

- KiCad 10 with the IPC API enabled
- `uv`
- Python with Tkinter

This directory includes a `plugin.json` and can be installed as a KiCad IPC
plugin. It can also be run directly while KiCad is open.

## Socket

The default socket is:

```bash
ipc:///tmp/kicad/api.sock
```

The lookup order is:

1. `--socket` command-line option
2. `KICAD_API_SOCKET` environment variable
3. `ipc:///tmp/kicad/api.sock`

## Run Directly

From this directory:

```bash
uv run downselector --socket ipc:///tmp/kicad/api.sock
```

Or:

```bash
KICAD_API_SOCKET=ipc:///tmp/kicad/api.sock uv run downselector
```

## Workflow

1. In KiCad PCB editor, select tracks and vias that should become the search
   scope.
2. Open `Downselector`.
3. Click `Refresh Selection`.
4. Inspect the variations table. Each row is a unique combination of selected
   track/via properties.
5. Choose filters manually, or click a variation row to fill matching filters.
   The unit selector controls whether size values are shown in millimeters or
   mils across the filters and table.
6. Click `Select Matches In KiCad`.

The new KiCad selection is the subset of the original refreshed selection that
matches the active filters.
