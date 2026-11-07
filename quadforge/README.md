# QuadForge Vivaldi 0.13.1

> The FORGE engine is validated in Blender 5.2 on Linux; the Windows and macOS binaries are built but not yet widely tested.

**New in 0.5:** our own **FORGE** remeshing engine (see `docs/FORGE_ENGINE.md`, `READ_ME_FIRST.md`): outputs **quads or triangles**, reads heavy sculpts without a working copy and supports **paintable density** through a vertex group. QuadriFlow is still available in the *Engine* selector.

Quad remesher for heavy sculpts: you set a target count (and, if you want, a ladder of levels) and get clean, symmetric
quad meshes that fit your sculpt.

- **Count target** (ZRemesher style): `4000` → roughly 4000 quads.
- **Level ladder**: `20K` plus `15K, 10K, 5K, 4K` in a single run, each level built from the previous one.
- **Multi-million-face sculpts**: uses a lightweight *working copy* (for example 3 M → 80 K) and then **fits each level
  to your original sculpt** to preserve the shape.
- **Polish** of quads (more regular sizes) without moving corners or the symmetry seam.
- **Smart hard edges**: analyzes edge chains, ignores noise smaller than two quads, keeps straight corners and
  snaps the result's corners exactly onto the model's edge lines.
- **Isolate zones** by materials, Face Sets or mask.
- **Automatic shading**: smooth with the hard edges marked (Smooth and Flat options).
- **Two surface buttons** (Hard Surface, Soft Surface / Organic) that configure the add-on and stay marked, and a Target Faces list from 2K to 500K. The 20K to 4K ladder is in Extra Levels.
- **Exact symmetry** in X, Y, Z: cuts, remeshes one half, mirrors and welds.
- **Guided interface** (live tips, presets, per-section help) in English and Spanish.
- **Built-in measurement**: time per stage and Blender's peak memory.

## Installation

Blender 4.2 or later. `Edit > Preferences > Get Extensions > ▾ > Install from Disk…` and choose `quadforge-vivaldi-0.13.1-all-platforms.zip`.
The panel appears in the 3D Viewport, sidebar (**N** key), **QuadForge** tab.

## Usage in 3 steps

1. Select the mesh.
2. Choose a preset (or type the target quads). The **Ladder 20K to 4K** preset builds the 5 levels.
3. Press **Remesh**.

The original is kept (hidden). `<name>_quads` appears (or `<name>_quads_20K`, `_15K`, ... with several levels).
The *How to Use* panel inside the addon explains everything step by step.

## How it is organized

```
docs/             technical study, decisions, benchmark, open research, references and licenses
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
  profiling.py      time and memory
  backends/         engines behind a single interface (today: QuadriFlow)
  pipeline.py       the 9-stage remeshing recipe (starts at the run() function)
tests/            tests and benchmark
```

## How to test it

**1. Check that it works** (inside Blender, from the folder that contains `quadforge`):

```
blender --factory-startup -b --python quadforge/tests/blender_integration_test.py
```

28 scenarios: target, symmetry on 1 and 3 axes, hard edges, materials, working copy with 328 K triangles, fit to the
original, polish, level ladder, profile and buttons. v0.4 adds 7 that reproduce the defects found in the real
test: inverted mirrored half, gaps at the seam, collapsed vertices, corner snapping, smart detection,
shading and 80K/120K presets. Prints `PASS` or `FAIL` with the reason.

**2. Measure** (time, memory, detail, symmetry), with your real sculpt:

```
blender yourfile.blend -b --python quadforge/tests/benchmark.py -- --object ObjectName \
    --targets 20000,15000,10000,5000,4000 --sym X --out my_sculpt.json
```

Full protocol and results template in `docs/BENCHMARK.md`.

**3. Without Blender (pure rules, fast):**

```
python tests/test_geometry.py
python tests/test_guidance.py
python tests/test_planning.py
python tests/test_features.py
```

## Current status (read this)

- **v0.4 has not been run inside Blender.** Only the syntax and the consistency of texts and translations were checked.
  The fixes are based on the analysis of the STL files from a real v0.3 test (`docs/CASE_BULL_ANT.md`). Section 10 of `docs/TECHNICAL_STUDY.md` lists the API assumptions that must be confirmed.
- The engine is the **built-in QuadriFlow**; no guide curves or painted density yet.
- Remeshing is synchronous: Blender stays busy while it works.
- It does not yet automate recovery of fine detail (Multires reshape or normal bake); UVs and color attributes
  are not transferred either.
- The GPU is not used: this is a reasoned decision in `docs/TECHNICAL_STUDY.md` (section 6).

## License

GPL-3.0-or-later. Original code, inspired by the behavior (not the code) of other tools.
Details in `docs/REFERENCES_AND_LICENSES.md`.
