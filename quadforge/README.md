# QuadForge 0.16.4

A Blender add-on (4.2 or newer, developed on 5.2) that remeshes meshes into clean **quads or triangles**, powered by
**FORGE**, its own dependency-free engine written in Rust. It reads heavy sculpts directly and also handles CAD / STL
parts, where it can analyse the whole model into surfaces and keep their real borders.

> The engine is validated most on Linux with Blender 5.2. The Windows and macOS binaries are built by CI and pass the
> engine tests, but have had little real-world use. Surface analysis and chamfer/bevel detection are previews.

## What it does

- **Count target** (ZRemesher style): `4000` gives roughly 4000 quads. Quads or triangles, 2K to 500K faces.
- **Two surface buttons**: **Hard Surface** (CAD, mechanical parts: hard edges from 30°, right angles, surface analysis on, result shaded **Flat**)
  and **Soft Surface / Organic** (sculpts: result shaded **Smooth**). They configure the add-on and stay marked.
- **Smart hard edges**: edge chains, noise smaller than two quads ignored, corners snapped onto the model's edge lines.
- **Surface analysis (preview, 0.16)**: for CAD meshes it splits the whole model into planes, cylinders, spheres, cones,
  surfaces of revolution and free-form patches, finds chamfers (orange) and bevels (blue), and lists every border.
  Hard Surface can keep those borders in the remesh (experimental option).
- **Exact symmetry** in X, Y, Z: cut, remesh one half, mirror and weld.
- **Isolate zones** by material, Face Set, mask or vertex group; **paintable density** through a vertex group; detail per zone.
- **Level ladder** (`20K, 15K, 10K, 5K` in one run), **Quick Preview**, **Subdivision Match**, flow guides.
- Carries over UV maps, Face Sets, mask and materials. Cancel with Esc.
- Interface in English and Spanish, with live tips and per-section help.

## Installation

Blender 4.2 or later. `Edit > Preferences > Get Extensions > ▾ > Install from Disk…` and choose the zip for your
system from the **Releases** page (`quadforge-<version>-all-platforms.zip` contains every engine).
The panel appears in the 3D Viewport sidebar (**N** key), **QuadForge** tab.

## Usage in 3 steps

1. Select the mesh.
2. Press **Hard Surface** or **Soft Surface / Organic**, then pick the target faces.
3. Press **Remesh**.

The original is kept (hidden). `<name>_quads` appears (or `<name>_quads_20K`, `_15K`, ... with several levels).
The *How to Use* panel inside the add-on explains everything step by step; see also `docs/USER_GUIDE.md`.

## How it is organized

```
docs/             user guide, architecture, engine, surface analysis, detector, status, studies and decisions
texts.py          all interface strings (English)
translations.py   the Spanish translation
presets.py        one-click starting points
planning.py       pure rules: levels and working-copy size
guidance.py       pure rules for the live tips
properties.py     settings stored in the scene
operators.py      buttons
ui.py             side panel
core/             the "engine room"
  geometry.py       mesh analysis with NumPy
  features.py       smart hard-edge detection
  mesh_io.py        fast Blender <-> NumPy transfer
  symmetry.py       cutting, mirroring, seam welding and face orientation
  detail.py         sculpt fitting, corner snapping, polish, face data
  bands.py          chamfer / bevel detector client (engine command `forge bands`)
  surfaces.py       whole-model surface analysis client (engine command `forge surfaces`)
  profiling.py      time and memory
  backends/         the engine behind a single interface (FORGE)
  pipeline.py       the remeshing recipe (starts at the run() function)
tests/            tests and benchmark
```

## How to test it

Inside Blender (headless, from the folder that contains `quadforge`), for example:

```
blender --factory-startup -b --python quadforge/tests/blender_integration_test.py
blender --factory-startup -b --python quadforge/tests/blender_surfaces_test.py
```

`quadforge/tests/run_in_bpy.py` runs the same files through the `bpy` module. Engine tests: `cargo test --release` in
`engine-rs/`. The benchmark and its protocol are in `tests/benchmark.py` and `docs/BENCHMARK.md`. The full list of
commands is in `docs/ARCHITECTURE_FOR_AI.md` (section 8) in the repository.

## Current status

See `docs/STATUS_AND_LIMITS.md`. In short: tested on Blender 4.2, 4.5, 5.1 and 5.2 (Linux); fast; better squares than
QuadriFlow on most test sculpts; remeshing is synchronous (Blender is busy while it works, Esc cancels); the GPU is not used.

## License

GPL-3.0-or-later. Original code, inspired by the behavior (not the code) of other tools.
Details in `docs/REFERENCES_AND_LICENSES.md`.
