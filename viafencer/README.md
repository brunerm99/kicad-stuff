# Via Fencer

KiCad 10 IPC tool for placing ground via fences around selected RF tracks.

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
uv run viafencer --socket ipc:///tmp/kicad/api.sock
```

Or:

```bash
KICAD_API_SOCKET=ipc:///tmp/kicad/api.sock uv run viafencer
```

Logs are written to the terminal. For a file log:

```bash
uv run viafencer --debug --log-file viafencer.log
```

The default IPC timeout is 60 seconds. Override it with `--timeout-ms` if KiCad
needs longer to create a large fence.

## Workflow

1. In KiCad PCB editor, select RF tracks or arc tracks.
2. Open `Via Fencer`.
3. Confirm the via net, via diameter, drill diameter, via edge spacing, and trace gap.
4. Choose the via start and stop layers.
5. Choose the collision mode.
6. Click `Create Fence`.

The trace gap is measured from RF trace copper edge to via pad copper edge.
Via edge spacing is measured from one via pad edge to the next via pad edge
along each fence row.
The start/stop layer choices come from the board's enabled copper layers. The
default is the full stack, which creates through vias. Shorter spans create
blind/buried vias.
Generated vias are grouped by default.
