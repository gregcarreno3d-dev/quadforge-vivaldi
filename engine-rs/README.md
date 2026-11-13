# FORGE engine (Rust)

The Rust port of the FORGE engine of QuadForge. Same `.fmesh` files and same stdout protocol
(`P` progress, `R` repair report, `S` stats JSON, `OK`/`ERR`) as the former C++ engine in `../engine/` (reference only), so
the add-on runs any build (`FORGE_ENGINE=/path/to/forge` selects one for development). No dependencies: `cargo build --release`.

## Where things are (one module per stage)

| You want to change...                                  | Look in                                  |
|--------------------------------------------------------|------------------------------------------|
| order of stages, route choice, fallbacks, retries      | `src/pipeline.rs`                        |
| command line flags                                     | `src/cli.rs`                             |
| file format, stdout protocol, threads, RNG             | `src/io/`, `src/util/`                   |
| geometry helpers, BVH, corner-table mesh               | `src/math/`, `src/mesh/`                 |
| input repair (welding, slivers, orientation)           | `src/stages/repair.rs`                   |
| border capping (closing open borders for the grid)     | `src/stages/capping.rs`, `borderweld.rs` |
| thin-part / curvature / gap size fields                | `src/stages/sizefield.rs`                |
| isotropic triangle remesh                              | `src/stages/remesh.rs`                   |
| cross field (4-RoSy), creases, guides                  | `src/stages/field.rs`                    |
| triangle pairing route (`--quad-method pair`)          | `src/stages/quad.rs`, `quadopt.rs`       |
| integer-grid parametrization (the core of the grid)    | `src/stages/param.rs`, `amg.rs`          |
| cells, faces, holes from the parametrization           | `src/stages/extract/lattice.rs`          |
| all-quad cleanup (thin strips, dead ends, stars)       | `src/stages/extract/finish.rs`           |
| quad quality polish                                    | `src/stages/quadquality.rs`              |
| metrics JSON                                           | `src/stages/metrics.rs`                  |
| whole-model surface segmentation (`forge surfaces`)    | `src/stages/surfaces.rs`                 |
| chamfer / bevel detector (`forge bands`)               | `src/stages/bands.rs`, flags in `src/main.rs` |

## Status

Parity with the C++ engine on the engine test suites (`t07_robustness --quick`, `t08_hard_cases`: 29/29) and on the
Cylinder Ver-03 model at 120K (same quality, about 20% slower in the solver). Since 0.15.0 the Rust engine is the one the add-on ships
(the C++ engine is a frozen reference). Known open problems (the reason for the port): chamfer strips
thinner than one cell and the hole-filling stars, see `../quadforge/docs/`.

Not ported on purpose: experimental seam repair (`FORGE_SEAMFIX`) and the cell-size retry (`FORGE_RETRY`), both off by
default in C++.

## Tuning switches (development)

| Variable | Effect |
|---|---|
| `FORGE_THINFEAT=<cells>` | Marked lines closer than this (default 0.75 cell) to another run of marked edges: the shorter run is left unmarked (`pipeline.rs`, `long_feature_chains`). `0` disables. |
| `FORGE_NOLADDER=1` | Subdivide 6+ sided hole polygons as a centroid star again instead of a quad ladder (`stages/extract/finish.rs`, `subdivide_polygons`). |
| `FORGE_NOSTRIP`, `FORGE_STRIPPCT` | Closed thin-strip collapse off / threshold (percent of a cell). |
| `FORGE_NOSTAR`, `FORGE_STARMIN` | Star replacement off / minimum valence. |

Known limit: a few quads with coincident vertices remain on hard-surface plates (also present in the C++ engine).
Welding them after extraction was tried and rejected: it breaks the closed-mesh check and triggers the fallback.

## Chamfer and bevel detector

`forge bands --in part.fmesh [--json out.json] [--sharp 25] [--curved F] [--narrow 0.35] [--max-width W] [--min-width W] [--link 35] [--elong 2]`

`forge surfaces --in part.fmesh [--json out.json] [--sharp 25] [--max-width W] [--min-width W] [--tol 0.002] [--plane-deg 3] [--smooth 50]` splits the whole model into surfaces and borders (prints `SF <json>` and `OK`); see `quadforge/docs/SURFACE_ANALYSIS.md`.
finds chamfers and bevels (0.15.x preview; the remesher does not use it yet). Debug: `FORGE_BANDS_DEBUG=1`, `FORGE_DBG=1`.
Full description: `../quadforge/docs/CHAMFER_BEVEL_DETECTOR.md`.
