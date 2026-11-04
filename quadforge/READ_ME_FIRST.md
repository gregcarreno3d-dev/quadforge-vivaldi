# QuadForge Vivaldi — version 0.10.31

> Entry document for any person or AI receiving this add-on. Read it first; details are in `docs/`.

## Identity
| Field | Value |
|---|---|
| Name | **QuadForge Vivaldi** (internal extension id: `quadforge`; it does not change, so upgrades keep working) |
| Version | **0.10.31** (series name: *Vivaldi*) |
| Author | GregCarreno3D |
| License | GPL-3.0-or-later |
| Requires | Blender 4.2 or newer |
| Status | Prototype. Validated inside Blender 5.2.2 with automated tests and the author's models; speed is excellent, quad quality (poles) is still below QuadriFlow; see `docs/STATUS_AND_LIMITS.md`. |
| Language | The whole project (UI, code, docs) is in English. A Spanish UI translation is optional (`translations.py`). |

## Versioning scheme
`MAJOR.MINOR.PATCH` plus a series name. 0.x means prototype. Each MINOR step brings a big capability (0.3 levels and working copy, 0.4 smart hard edges, **0.5 own FORGE engine**; 0.5.x = fixes and polish). The series name (Vivaldi = 0.5.x) identifies a generation. 0.6 brought the lattice method and **0.7 the integer-grid parametrization (far fewer poles)**; the next big engine step is hard-edge alignment (0.8); 1.0 is reserved for a version validated in Blender on all platforms.

## What it does, in one sentence
Turns a mesh (a multi-million-face sculpt or a regular mesh) into a new mesh of **quads or triangles** with a target count, keeping hard edges, open borders, zones and symmetry, using its **own C++ engine (FORGE)** or, optionally, Blender's QuadriFlow.

## Where everything is
```
blender_manifest.toml   extension metadata (name, version, license)
__init__.py             class registration in Blender
properties.py           settings stored in scene.quadforge
operators.py            buttons: Remesh, Apply Preset, Reset Settings
ui.py                   sidebar panels (N > QuadForge)
guidance.py planning.py presets.py texts.py translations.py   live advice, pure rules, presets, English texts, optional Spanish
core/pipeline.py        the full remesh recipe (stages 1-9)
core/backends/          engines: forge.py (own) and quadriflow.py (Blender)
core/forge_io.py        .fmesh format (exchange with the engine)
core/mesh_io.py geometry.py symmetry.py features.py detail.py profiling.py   NumPy/Blender utilities
bin/<system>/forge      engine executable: windows, linux, macos-arm64, macos-x64
engine_src/             C++ engine source + build.py (rebuild with Zig)
devtools/               packager, engine tests, comparison with QuadriFlow, STL reader
docs/                   documentation (see below)
tests/                  tests (no Blender needed except blender_integration_test.py)
```

## Documents
- `docs/USER_GUIDE.md` — step-by-step guide for the artist: every option, when to use it, common problems.
- `docs/ARCHITECTURE.md` — how it works inside (add-on, pipeline, protocol, format, engine algorithms). For developers and AIs.
- `docs/FORGE_ENGINE.md` — engine summary.
- `docs/STATUS_AND_LIMITS.md` — what is tested, what is not, and the roadmap.
- `docs/PUBLISHING.md` — packages, Blender Extensions and Superhive notes, macOS signing (postponed).
- `CHANGELOG.md` — version history.
- Research documents from earlier versions: `docs/TECHNICAL_STUDY.md`, `DECISIONS.md`, `PRESETS_AND_WORKFLOW_STUDY.md`, `BENCHMARK.md`, `CASE_BULL_ANT.md`, `OPEN_RESEARCH.md`, `REFERENCES_AND_LICENSES.md`.

## Quick install
`Edit > Preferences > Get Extensions > ▾ > Install from Disk…` → choose the `.zip`. Panel: 3D Viewport, **N** key, **QuadForge** tab.
