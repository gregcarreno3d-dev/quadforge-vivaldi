# QuadForge — version 0.16.2

> Entry document for any person or AI receiving this add-on. Read it first; details are in `docs/`.

## Identity
| Field | Value |
|---|---|
| Name | **QuadForge** (extension id: `quadforge`) |
| Version | **0.16.2** |
| Author | GregCarreno3D |
| License | GPL-3.0-or-later |
| Requires | Blender 4.2 or newer |
| Status | Prototype. Validated inside Blender 4.2 to 5.2 with automated tests and the author's models. Fast, and better squares than QuadriFlow on most test sculpts; QuadriFlow still has fewer poles at 80K faces and above. CAD / hard-surface support (surface analysis) is in preview. See `docs/STATUS_AND_LIMITS.md`. |
| Language | The whole project (UI, code, docs) is in English. A Spanish UI translation is optional (`translations.py`). |

## Versioning scheme
`MAJOR.MINOR.PATCH`. 0.x means prototype. Each MINOR step brings a big capability (0.3 levels and working copy, 0.4 smart hard edges, 0.5 own FORGE engine, 0.7 integer-grid parametrization, 0.10 open borders, 0.15 Rust engine and chamfer/bevel detector, 0.16 whole-model surface analysis); PATCH steps are fixes and polish. 1.0 is reserved for a version validated in Blender on all platforms.

## What it does, in one sentence
Turns a mesh (a multi-million-face sculpt or a regular mesh) into a new mesh of **quads or triangles** with a target count, keeping hard edges, open borders, zones and symmetry, using its **own Rust engine (FORGE)**. For CAD / STL parts it can also analyse the whole model into surfaces (planes, cylinders, spheres, cones, chamfers, bevels) and keep their borders.

## Where everything is
```
blender_manifest.toml   extension metadata (name, version, license)
__init__.py             class registration in Blender
properties.py           settings stored in scene.quadforge
operators.py            buttons: Remesh, Apply Preset, Reset Settings
ui.py                   sidebar panels (N > QuadForge)
guidance.py planning.py presets.py texts.py translations.py   live advice, pure rules, presets, English texts, optional Spanish
core/pipeline.py        the full remesh recipe (stages 1-9)
core/bands.py           chamfer / bevel detector client (preview, 0.15+)
core/surfaces.py        whole-model surface analysis client (preview, 0.16+)
core/backends/          engines: forge.py (own engine)
core/forge_io.py        .fmesh format (exchange with the engine)
core/mesh_io.py geometry.py symmetry.py features.py detail.py profiling.py   NumPy/Blender utilities
bin/<system>/forge      engine executable: windows, linux, macos-arm64, macos-x64
engine_src/             original C++ engine source (reference only); the shipped Rust engine source is in engine_src_rs/ inside the zip (engine-rs/ in the repository)
devtools/               packager, engine tests, STL reader
docs/                   documentation (see below)
tests/                  tests (no Blender needed except blender_integration_test.py)
```

## Documents
- `docs/USER_GUIDE.md` — step-by-step guide for the artist: every option, when to use it, common problems.
- `docs/ARCHITECTURE.md` — how it works inside (add-on, pipeline, protocol, format, engine algorithms). For developers and AIs.
- `docs/FORGE_ENGINE.md` — engine summary.
- `docs/SURFACE_ANALYSIS.md` — whole-model surface analysis (0.16 preview) and its use in Hard Surface.
- `docs/CHAMFER_BEVEL_DETECTOR.md` — the chamfer and bevel detector (0.15 preview): what it does, how it works, command line, JSON, limits, roadmap.
- `docs/STATUS_AND_LIMITS.md` — what is tested, what is not, and the roadmap.
- `docs/PUBLISHING.md` — packages, Blender Extensions and Superhive notes, macOS signing (postponed).
- `CHANGELOG.md` — version history.
- Research documents from earlier versions: `docs/TECHNICAL_STUDY.md`, `DECISIONS.md`, `PRESETS_AND_WORKFLOW_STUDY.md`, `BENCHMARK.md`, `CASE_BULL_ANT.md`, `OPEN_RESEARCH.md`, `REFERENCES_AND_LICENSES.md`.

## Quick install
`Edit > Preferences > Get Extensions > ▾ > Install from Disk…` → choose the `.zip`. Panel: 3D Viewport, **N** key, **QuadForge** tab.
