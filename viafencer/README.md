# KiCad Via Tools

KiCad 10 IPC tools for placing RF via fences, conservative zone stitch vias,
and RF solder-mask opening zones.

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

From this directory, run the RF track fencer:

```bash
uv run viafencer --socket ipc:///tmp/kicad/api.sock
```

Or the zone stitcher:

```bash
uv run zonestitcher --socket ipc:///tmp/kicad/api.sock
```

Or the RF mask expander:

```bash
uv run maskexpander --socket ipc:///tmp/kicad/api.sock
```

Environment-variable socket selection also works:

```bash
KICAD_API_SOCKET=ipc:///tmp/kicad/api.sock uv run viafencer
```

Logs are written to the terminal. For a file log:

```bash
uv run viafencer --debug --log-file viafencer.log
```

The default IPC timeout is 60 seconds. Override it with `--timeout-ms` if KiCad
needs longer to create a large fence.

## Via Fencer Workflow

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

## Zone Stitcher Workflow

1. In KiCad PCB editor, select one filled copper zone.
2. Open `Zone Stitcher`.
3. Set X spacing, Y spacing, and whether alternate rows are staggered.
4. Optionally enable `Run DRC diff before/after`.
5. Click `Stitch Zone`.

The stitcher uses the selected zone net class for via diameter, drill diameter,
and clearance. It only generates candidates inside the selected zone's actual
filled copper on every stitched layer. Candidates are rejected if they would
overlap generated vias, pads, vias, tracks, arcs, or other-net filled zone copper
on any layer touched by the via.

The DRC diff option defaults off. When enabled, the tool saves temporary board
copies before and after stitching, runs `kicad-cli pcb drc` on both copies, and
reports whether violations changed. `kicad-cli` must be on `PATH` or in a
standard KiCad application bundle location.

## RF Mask Expander Workflow

1. In KiCad PCB editor, select RF tracks or arc tracks.
2. Open `RF Mask Expander`.
3. Set the trace-edge offset.
4. Leave the mask layer on `Auto from trace layer`, or force `F.Mask` or `B.Mask`.
5. Click `Create Mask Zones`.

The trace-edge offset is measured from the outside edge of the selected trace.
The generated zone covers the trace plus that side offset. External track ends
use square caps with no radius growth past the selected trace endpoint, so mask
openings do not expand into IC pads. Corners shared by selected tracks are
overlapped automatically to avoid small gaps at bends.
