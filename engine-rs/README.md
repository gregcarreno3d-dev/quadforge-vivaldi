# FORGE engine (Rust)

The Rust port of the FORGE engine of QuadForge Vivaldi. Same `.fmesh` files and same stdout protocol
(`P` progress, `R` repair report, `S` stats JSON, `OK`/`ERR`) as the C++ engine in `../engine/`, so the add-on runs
either one (`FORGE_ENGINE=/path/to/forge` selects a build for development). No dependencies: `cargo build --release`.

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

## Status

Parity with the C++ engine on the engine test suites (`t07_robustness --quick`, `t08_hard_cases`: 29/29) and on the
Cylinder Ver-03 model at 120K (same quality, about 20% slower in the solver). The C++ engine remains the one the add-on
ships until the Rust engine is better, not just equal. Known open problems (the reason for the port): chamfer strips
thinner than one cell and the hole-filling stars, see `../quadforge/docs/`.

Not ported on purpose: experimental seam repair (`FORGE_SEAMFIX`) and the cell-size retry (`FORGE_RETRY`), both off by
default in C++.

## Tuning switches (development)

| Variable | Effect |
|---|---|
| `FORGE_THINFEAT=<cells>` | Marked lines closer than this (default 1.0 cell) to another run of marked edges: the shorter run is left unmarked (`pipeline.rs`, `long_feature_chains`). `0` disables. |
| `FORGE_NOLADDER=1` | Subdivide 6+ sided hole polygons as a centroid star again instead of a quad ladder (`stages/extract/finish.rs`, `subdivide_polygons`). |
| `FORGE_NOSTRIP`, `FORGE_STRIPPCT` | Closed thin-strip collapse off / threshold (percent of a cell). |
| `FORGE_NOSTAR`, `FORGE_STARMIN` | Star replacement off / minimum valence. |

Known limit: a few quads with coincident vertices remain on hard-surface plates (also present in the C++ engine).
Welding them after extraction was tried and rejected: it breaks the closed-mesh check and triggers the fallback.
