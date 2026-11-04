# QuadForge Vivaldi: architecture and behaviour, written for AI assistants

Version described: **0.10.31**. Licence: GPL-3.0-or-later. Everything here was checked against the code in this
repository. If this file and the code disagree, **the code wins**; please fix this file.

This document is meant to be loaded whole into a model's context. It is dense and flat on purpose: sections are
independent, names are exact, and each claim says where to verify it. A readable explanation with the mathematics is in
[`TECHNICAL_REPORT.md`](TECHNICAL_REPORT.md). The short index for crawlers is [`../llms.txt`](../llms.txt).

## 0. One-paragraph summary

QuadForge is a Blender add-on (Blender 4.2+, tested on 5.2) that remeshes a mesh to quads (or triangles) with a target
face count. It has two parts: a **Python add-on** (`quadforge/`) that reads the mesh, decides constraints and builds the
result objects, and **FORGE**, a dependency-free **C++17 command-line engine** (`engine/src/`) that does the geometry
and runs as a **separate process**. The default quad method is: repair → isotropic triangle remesh (about 6 triangles
per lattice cell) → smooth 4-direction cross field → seamless integer-grid parametrization → quad extraction by
vertex snapping → 2×2 subdivision and relaxation. If that fails, the engine falls back to triangle pairing. The add-on
also does exact symmetry (remesh the whole model with the plane as a guide line, cut, mirror, weld), hard-edge
detection by edge chains, zones (materials, Face Sets, mask, vertex groups), UV transfer, density by vertex group and
multi-level output.

## 1. Repository map

```
quadforge/                    the add-on (this folder is what Blender installs)
  __init__.py                 registration, version
  blender_manifest.toml       Blender extension manifest (id "quadforge", per-platform zips)
  properties.py               QuadForgeProperties: every user setting (bpy PropertyGroup)
  ui.py                       panels: QUADFORGE_PT_main, _hard_edges, _guides, _zones, _symmetry, _levels, _density,
                              _speed, _last_result, _how_to_use, _advanced
  operators.py                quadforge.remesh, quadforge.remesh_modal (Esc cancels), mark_guides, clear_guides,
                              apply_preset, set_surface, set_target, reset_settings
  presets.py                  PRESETS (Prop 2K ... Detail 120K, Ladder), SURFACE_PROFILES (Hard / Organic)
  planning.py                 target-list parsing ("20K, 15K, 10K"), working-copy sizing (pure Python, no bpy)
  guidance.py                 advice messages shown at the top of the panel (levels INFO/WARNING/ERROR)
  texts.py                    every UI string (English); translations.py: optional Spanish UI strings
  core/
    pipeline.py               THE orchestration: run_steps() generator; RemeshSettings; all stages
    features.py               hard-edge detection by chains (pure NumPy)
    symmetry.py               SymmetryPlan, cut/mirror/weld helpers (bmesh)
    geometry.py               MeshArrays, normals/areas, edge angles, zone labels from vertex groups, stats
    mesh_io.py                bulk read/write of Blender mesh data with foreach_get/foreach_set
    forge_io.py               .fmesh read/write
    detail.py                 snap/polish/copy face data (used by the QuadriFlow route and zone copying)
    uvtransfer.py             UV transfer by projection onto the original surface
    zonedata.py               Face Sets and sculpt-mask transfer
    profiling.py              stage timer + peak memory
    backends/
      base.py                 Backend, RemeshRequest, BackendError
      forge.py                runs the engine process, parses its output, writes the result into the mesh
      quadriflow.py           optional second backend: Blender's built-in object.quadriflow_remesh
  bin/<platform>/forge[.exe]  prebuilt engines: windows, linux, macos-arm64, macos-x64
  engine_src/                 copy of engine/src shipped inside the add-on
  tests/                      Blender integration tests (run with the bpy module) and pure-Python tests
  devtools/                   engine test suites (engine_tests/), QuadriFlow comparison, helpers
  docs/                       user guide, decisions, status and limits, benchmark protocol
engine/src/                   FORGE engine source (C++17, header-only modules + main.cpp)
tools/                        build.py (Zig cross-compiler), package.py, make_delivery.py, quality_metrics.py,
                              meshgen.py (synthetic meshes), fmesh.py, stlio.py, harness.py
tests/                        early engine tests t01..t05
docs/                         this documentation (technical report, this file, images/)
.github/workflows/ci.yml      builds the engine on 4 platforms and runs the tests
```

`engine/src` and `quadforge/engine_src` must stay identical (the second is the copy that ships in the add-on).

## 2. Runtime data flow

`core/pipeline.py: run_steps(context, original, settings, progress)` is a generator that yields often so the modal
operator can keep Blender responsive. `run()` simply drives it to the end. Closing the generator (Esc) raises
`GeneratorExit` at a `yield`; `finally` blocks kill the engine process and delete temporary data (`TemporaryData`).

Stages, in order (names are the profiler stage names):

1. **Read source** `_read_source`: evaluated mesh (modifiers applied), BVH tree of the surface. The original object is
   never modified.
2. **Density** `_attach_density` (FORGE only): vertex group → point attribute `quadforge_density`,
   `multiplier = 1 / (1 + (ratio - 1) * weight)`.
3. **Zone labels** `_compute_zone_labels` (if any zone option is on): face attribute `quadforge_zone` (integer).
   Sources: materials, Face Sets, mask (threshold), vertex groups (mean weight per face, argmax, `-1` below
   `group_cutoff`).
4. **Working copy**: for the QuadriFlow route only. With FORGE, `RemeshSettings` is replaced by a copy with
   `use_proxy=False, use_reproject=False, snap_features=False, polish_passes=0` (the engine projects onto the source
   itself).
5. **Symmetry plan** `_plan_symmetry`. FORGE + QUADS + active symmetry → `plan.cut_after = True`: remesh the whole model,
   with the plane edges marked as guide lines. Otherwise cut first (`_clean_and_cut_to_half`).
   `FORGE_HALF_FIRST=1` (environment) forces cut-first.
   **Why remesh the whole model:** cutting first leaves a flat open cap on the plane, which disturbs the engine's
   field and grid near the seam. With the plane marked as guide lines the quad rows align to it, so the later cut passes
   between rows of quads and the mirrored seam welds cleanly.
6. **For each level** in `planning.parse_level_targets`:
   - `_mark_hard_edges_and_zones`: writes `use_edge_sharp` on the working mesh. Union of: smart or plain angle
     detection (`features.detect_hard_edges`), symmetry-plane edges (if `cut_after`), existing sharp marks, UV seams,
     zone borders (`geo.region_boundary_edges`). Warns if > 25 % of the edges are hard.
   - `collect_guides`: edges marked with Mark Flow Guide (attribute `quadforge_guide`) → segments.
   - `_remesh_with_engine`: builds a `RemeshRequest`, calls the backend, then **sliver cleanup** (below).
   - `sym.cut_result_to_half` if `cut_after`.
   - `detail.copy_face_data` (materials and zone labels carried from the reference mesh).
7. **Build results** `_mirror_halves_into_final_mesh` (mirror + weld), `_create_result_object`, UV transfer
   (`uvtransfer.transfer_uvs`), sculpt data (`zonedata.copy_sculpt_data`), statistics (`geo.mesh_stats`), quality
   warnings (`_quality_warnings`).
8. `_finish_selection`: result selected, original hidden if `hide_original`; component selection cleared on the result
   (`_clear_component_selection`).

Result object names: `<original>_quads` / `<original>_tris`, plus `_<target>` when several levels are requested.

### 2.1 Sliver cleanup (after the engine, QUADS output only, when hard edges are preserved)

Constants in `pipeline.py`: `SLIVER_EDGE_FRACTION = 0.01`, `SLIVER_MIN_SHARE = 0.015`, `SLIVER_RETARGET_FRACTION = 0.05`.
`_collapse_slivers(mesh)` collapses (bmesh) edges shorter than 1 % of the median edge **only if** such edges are at
least 1.5 % of all edges; it returns the share of faces that vanished. If that share exceeds 5 %, the engine is run
again once with `target_faces / max(0.4, 1 - vanished)`. Do **not** delete zero-area faces here: an earlier version did
and opened edges (it broke the matrix tests). Do not collapse when there is no sliver problem: it adds triangles.

### 2.2 Constants worth knowing

`MIN_FACES_PER_PIECE = 8`, `MAX_HARD_EDGE_FRACTION = 0.25`, `TARGET_MISS_WARNING_FRACTION = 0.15` (warn if the quad
count is 15 % off), `SYMMETRY_MATCH_WARNING_THRESHOLD = 0.9`, `CURVATURE_ANGLES = {OFF: 0, LOW: 0.35, MEDIUM: 0.22,
HIGH: 0.15}` radians, `ENGINE_TIMEOUT_SECONDS = 3 h` (a safety net in `backends/forge.py`).

### 2.3 Detail on Curves safeguard (in `backends/forge.py`)

If Detail on Curves is on, output is QUADS and the result has at least 5,000 vertices, and more than 0.9 % of the
vertices are irregular (`CURVATURE_RETRY_FRACTION = 0.009`), the backend runs the engine a second time without it and
keeps that result when it has fewer than 60 % of the irregular vertices. The pipeline then warns that Detail on Curves
was left out (`engine_info["curv_dropped"]`).

## 3. The contract between Python and the engine

### 3.1 Command line

```
forge remesh --in A.fmesh --out B.fmesh --faces N [--mode tri|quad] [--threads N] [--stats]
             [--no-project] [--no-retry] [--curv-adapt RADIANS] [--corner-angle DEG] [--iterations N]
             [--quad-method auto|extract|pair] [--ex-density 6] [--ex-subdiv 0|1] [--ex-iters 60]
             [--no-param] [--no-align] [--no-adaptive] [--adaptive-ratio 1.3] [--adaptive-min 0.5]
             [--time-limit 900] [--check]
```

`build_command` in `backends/forge.py` builds the real command: `--mode quad|tri`, `--stats`, `--no-retry` when only
symmetry guide lines are marked (`fast_seams`), `--curv-adapt` for Detail on Curves (quads only), `--threads`.
In quad mode `--faces` is the **final quad count**.

### 3.2 `.fmesh` binary format (little endian; `core/forge_io.py`, `engine/src/meshio.hpp`)

```
char[4]  "FMSH"
u32      version = 1
u32      nV, nPoly, nIdx, nFeat, flags
f32      positions[3*nV]
i32      polygon sizes[nPoly]
i32      polygon vertex indices[nIdx]
i32      feature (hard) edges[2*nFeat]        vertex index pairs
f32      density[nV]                          only if flags & 1  (bit value 1: edge-length multiplier, 1 = normal)
f32      field[3*nV]                          only if flags & 2  (bit value 2: reserved, unused)
u32 nGuide; f32 guides[6*nGuide]              only if flags & 4  (bit value 4: segments x0 y0 z0 x1 y1 z1)
```

### 3.3 Engine standard output, one flushed line per message

```
P <percent> <message>     progress
R <json>                  repair report (input_polygons, duplicate_faces, nonmanifold_edges, ...)
S <json>                  result statistics (see below)
OK                        finished
ERR <message>             failure, exit code 2
```

`S` contains: `verts, faces, tris, quads, other, edges, boundary_edges, nonmanifold_edges, edge_len_min/mean/max,
angle_min, angle_mean, bad_angle_corners, valence[], regular_vertices, dist_mean, dist_max` and the engine's own
fields `method` (`extract` | `pair` | `tri`), `fallback` (reason text when it fell back), `closed_holes`,
`removed_slivers`, `density_note` (`partial` when a density gradient was only partly followed), `seconds`, `edge_length`,
`rounds`. The add-on turns `method == "pair"` into a visible warning that includes `fallback`.

### 3.4 Add-on attributes and names

Mesh attributes: `quadforge_density` (float, point), `quadforge_zone` (int, face), `quadforge_guide` (edge flag).
Edge flag handed to the engine: `use_edge_sharp` (falls back to a boolean `sharp_edge` attribute on newer Blender).
Temporary objects: `QuadForge_work`, `QuadForge_proxy`; modifier `QuadForge_Decimate`.

## 4. Engine internals

All engine code is in namespace `forge`, header-only modules included by `main.cpp`.

| File | Role |
|---|---|
| `vec.hpp` | 3D vector math |
| `bvh.hpp` | BVH: closest point on a triangle surface (`SurfaceIndex`) and on line segments (`SegmentIndex`) |
| `meshio.hpp` | `.fmesh` I/O, `PolyMesh` |
| `repair.hpp` | polygon soup → manifold triangle mesh (`TriSoup`), `RepairReport` |
| `cmesh.hpp` | corner-table triangle mesh with safe flip/split/collapse |
| `remesh.hpp` | `Remesher`: isotropic remeshing, face-count steering, projection |
| `sizefield.hpp` | `thicknessSizeField` (shrinking ball), `curvatureSizeField`, `gapSizeField`, grading |
| `capping.hpp` | `capBorders`: close open boundary loops with centroid fans; `stitchSlits` |
| `field.hpp` | `buildFrames`, `solveCrossField` (complex z = exp(4iθ)) |
| `param.hpp` | `IntegerParam`: seamless integer-grid parametrization (+ parallel CG) |
| `amg.hpp` | smoothed-aggregation AMG preconditioner (experimental, off; `FORGE_AMG=1`) |
| `extract.hpp` | `Extractor`: field transfer, parametrization call, snapping, face tracing, 1→4 subdivision, relaxation |
| `quadquality.hpp` | `improveQuads`: raise the worst scaled Jacobian by local vertex moves |
| `quadopt.hpp` | chord-rotation pole optimiser (off by default) |
| `quad.hpp` | `Quadifier`: pairing fallback |
| `metrics.hpp` | statistics JSON |
| `parallel.hpp` | `parallelFor`, `defaultThreads()` = min(cores, 16) |
| `main.cpp` | command line and the decision flow below |

### 4.1 Decision flow in `main.cpp` (`runMain`)

```
read .fmesh → repairMesh → stitchSlits
quadMode = (--mode quad)
hasBorder = any edge used once
alignMode = useParam && alignFeat && !FORGE_NOPARAM
if quadMode && hasBorder && alignMode && method in {auto, extract} && !FORGE_NOCAP:
    capBorders(loop threshold 6*rho0)
      only small holes  → close them (surface becomes closed)
      removable loops   → cap, mark loop edges as features, remember cap faces ("capped")
extract = quadMode && (method == extract ||
          (method == auto && (no density || alignMode) && !hasBorder && (alignMode || featureShare < 0.01)))
if extract && adaptive:  density *= thicknessSizeField (and curvatureSizeField if curv-adapt > 0)
keep only chains of marked edges longer than 6*rho0 (rho0 = sqrt(area / (N/4)))
remesh to  extractTarget = N * 6 / 4  triangles (grid method)   or  N / 2 (pairing method)  or  N (tri mode)
if extract:
    up to 3 passes: Extractor.run → (drop cap quads, weld short border edges) → reject if open edges
                    (rho *= 0.97) → accept if |count/N - 1| < 4 % (6 % when N >= 30000) else rho *= sqrt(ratio)
    fall back to pairing if: nothing usable, capped result > 25 % off, grid solve failed
    if the failure happened with thickness refinement active: exit code 3 → main() reruns without it
    density follow check → density_note = "partial"
if !extract && quadMode: Quadifier (pairing)
write .fmesh, print S line, print OK
```

Cross-field resolution: when the working mesh has more than 12,000 faces the field is solved on a coarser copy
(`max(2000, faces/4)`) and transferred. The parametrization attempts up to 5 times, softening the density power
(1, 0.5, 0.25, 0) when the fit error exceeds 0.45; above 0.5 the result is rejected. Time limit `--time-limit`
(default 900 s) aborts a solve.

### 4.2 Extraction in one paragraph

`Extractor::run`: (1) cross field (`solveCrossField`, coarse-transferred if large); per-vertex frame; (2)
`IntegerParam::run(rho, ...)` gives (u, v) per triangle corner; (3) every vertex snaps to `round(u), round(v)`;
triangles with a parameter edge > 0.8 are virtually subdivided (up to 3 rounds); vertices with the same node merge
into cells; neighbouring nodes create edges; dangling cells are pruned; (4) faces are traced in the rotation system;
4-loops are quads; holes are filled; high-valence stars become regular patches; (5) `subdivide`: each polygon → quads
(a quad → 4), relax 15 sweeps (factor 0.6, reproject onto the surface), snap to marked lines within 0.3·rho,
merge valence-2 dead ends (≤ 6 rounds), `improveQuads(..., 40 sweeps, threshold 0.6)`.

## 5. Settings → behaviour (Python `RemeshSettings`)

| Setting | Effect |
|---|---|
| `target_quads` | engine `--faces`; 16 minimum; the target menu offers 2K to 500K (150K and above are flagged as heavy) |
| `extra_levels` | additional targets ("15K, 10K"); each is a separate engine run from the source; entries that are invalid, duplicated, not lower than the previous level or below 16 are ignored and reported |
| `backend` | `FORGE` (default) or `QUADRIFLOW` |
| `output` | `QUADS` or `TRIS` (FORGE only; QuadriFlow always quads) |
| `use_sharp_angle`, `sharp_angle_deg` (35 default) | hard edges by dihedral angle |
| `smart_features`, `min_feature_size` (2), `keep_right_angles` | chain filter of `features.py` |
| `use_existing_sharp`, `use_seams` | extra hard edges |
| `use_materials`, `use_face_sets`, `use_mask`, `mask_threshold`, `use_vertex_groups`, `group_cutoff` | zones; borders become hard edges |
| `sym_x/y/z`, `sym_side`, `sym_center`, `sym_tolerance`, `check_symmetry` | symmetry |
| `density_group`, `density_ratio` (4) | vertex-group density |
| `curvature_detail` | OFF/LOW/MEDIUM/HIGH → `--curv-adapt`; off by default (it raises irregular vertices on purpose) |
| `use_flow_guides` | pass Mark Flow Guide edges to the engine |
| `transfer_uvs`, `transfer_zones` | UV maps, Face Sets and mask copied to the result |
| `preserve_boundary` | keep open borders as lines |
| `shading` | AUTO / SMOOTH / FLAT |
| `seed` | QuadriFlow only |

Surface profiles (`presets.SURFACE_PROFILES`): **Hard Surface** (hard edges from about 30°, right angles on, snapping
on, Detail on Curves off) and **Organic**. Presets set target and angle and never touch symmetry or zones.

## 6. Invariants and rules for anyone changing the code

1. **The original object is never modified.** All work happens on copies registered in `TemporaryData`.
2. **The engine runs out of process.** Never load engine code into Blender. Never block the UI: use the generator.
3. **No `bpy`, `bmesh` or `mathutils` import at module level** in `core/backends/forge.py`, `core/features.py`,
   `core/geometry.py`, `core/mesh_io.py`, `core/forge_io.py`, `planning.py` and `guidance.py`, so they stay testable
   without Blender (`pipeline.py`, `symmetry.py`, `detail.py` and the UI do import it).
4. **Every result must be a valid mesh**: closed if the input was closed, manifold, consistently oriented, no open seam
   after symmetry. The matrix test (`blender_matrix_test.py`, 67 scenarios) enforces this.
5. **UI text is English** in `texts.py`; Spanish lives only in `translations.py`. No hard-coded user strings elsewhere.
6. **Clean-room policy:** code was written from observable behaviour and published papers; do not copy code from
   proprietary tools. GPL code reused from another project must keep its notice in the file header.
7. **`engine/src` and `quadforge/engine_src` stay identical.**
8. Prefer a clear warning to a silent change of behaviour. Fallbacks always report their reason.

## 7. Lessons already paid for (do not repeat)

- A leftover component selection on the result looked like an "orange half" after symmetry → selection is cleared.
- Thin bevels create tiny-edge quads → sliver cleanup with a gate (section 2.1); deleting zero-area faces opened edges.
- Zone tests are flaky by nature when a zone is smaller than a quad; accept "kept, or reported by name".
- The multigrid preconditioner is implemented but slower than Jacobi-CG on current meshes.
- Distance rings around creases and the chord-rotation pole optimiser were tried and not adopted (poles got worse or
  barely improved); they remain behind environment switches.
- Parametrization time dominates at 120K+ quads; this is the main open performance problem.

## 8. Tests and how to run them

| Suite | Where | What |
|---|---|---|
| Engine robustness | `quadforge/devtools/engine_tests/t07_robustness.py` (`--quick`: 41 cases) | generated shapes, odd targets, hard-surface parts; independent validity checks |
| Engine hard cases | `.../t08_hard_cases.py` (25 cases) | open surfaces, parts, dirty input, odd scales, dense scans, density, triangles |
| Engine basics | `t01`–`t06` | sphere, suite, repair fuzz, pairing, extraction |
| Add-on in Blender | `quadforge/tests/blender_integration_test.py` (29), `blender_matrix_test.py` (67), `blender_forge_test.py`, `blender_guides_test.py`, `blender_uv_test.py`, `blender_zones_test.py`, `blender_vgzones_test.py`, `blender_surface_test.py`, `blender_hard_test.py` | run with the `bpy` module (`pip install bpy numpy scikit-image`); the Linux engine must be in `quadforge/bin/linux/` |
| Pure Python | `test_features.py`, `test_geometry.py`, `test_planning.py`, `test_guidance.py`, `test_forge_backend.py` | no Blender needed (the last one runs the real engine) |
| Metrics | `tools/quality_metrics.py` | scaled Jacobian, irregular share, distance to source |

Run the engine tests like this (from the repository root, after `python3 tools/build.py linux`):

```bash
export FORGE_ENGINE=$PWD/engine/bin/linux/forge
python3 quadforge/devtools/engine_tests/t08_hard_cases.py
python3 quadforge/devtools/engine_tests/t07_robustness.py --quick
```

Engine tests read the engine path from `FORGE_ENGINE`. Debug output: `FORGE_DBG=1` (engine stderr),
`FORGE_DBG_ECHO=1` (add-on echoes it). Switches for experiments: `FORGE_NOPARAM`, `FORGE_NOCAP`, `FORGE_NOADAPT`,
`FORGE_NOQQ`, `FORGE_NOMERGE2`, `FORGE_AMG`, `FORGE_POLEMOVES`.

Build and package: `python3 tools/build.py [platform]` (Zig; no other toolchain), copy `engine/src` into
`quadforge/engine_src`, copy binaries into `quadforge/bin/<platform>/`, `python3 tools/package.py` for the per-platform
zips (they pass Blender's `blender_ext.py validate`).

## 9. Glossary

- **Quad / pole / valence.** A quad has four corners. Valence is the number of edges at a vertex. 4 is regular; 3 and 5
  are poles (irregular vertices).
- **Cross field (4-RoSy field).** At every face, four directions 90° apart. Defined up to a 90° rotation.
- **Singularity.** A vertex around which the cross field does not close; a pole of the final mesh sits there.
- **Integer-grid parametrization.** A map (u, v) of the surface into the plane whose integer lines are the quad edges.
- **Cut graph / seam.** Edges where the map jumps by a 90° rotation and an integer translation.
- **rho (ρ).** Lattice spacing: surface length of one cell edge; about `sqrt(A / (N/4))` at first.
- **Scaled Jacobian.** For a quad, the minimum over its corners of |sin(corner angle)|: 1 is a square, 0 degenerate.
- **Hard edge / marked edge / feature.** An edge the result must follow; `use_edge_sharp` in Blender.
- **Zone.** A region (material, Face Set, mask, group) that must stay separate; its border becomes hard edges.
- **Guide line (symmetry).** The edges where the symmetry plane crosses the mesh, marked so quad rows align to them.
- **Pairing path.** Fallback: merge triangle pairs into quads, then subdivide.
- **Level.** One requested face count; several can be produced in one run.

## 10. Questions an AI is likely to be asked, with the answers

**Does FORGE need a GPU, Python packages or an internet connection?** No. The engine is a static, dependency-free
binary; it uses CPU threads (default: cores, at most 16).

**Is it a neural network?** No. It is classical geometry processing (see the technical report).

**Is it an implementation of QuadriFlow / Instant Meshes / QuadWild?** No. It follows the same family of ideas
(cross field + integer-grid parametrization) with its own code. Extraction is simpler than QEx. See report section 9.

**Why quad counts that are multiples of 4?** The lattice is built for N/4 cells and each cell is then split 2×2.

**Why can a result have fewer or more quads than requested?** Count control accepts ±4 % (±6 % from 30K quads); the
add-on warns beyond 15 %. Very small targets on parts with many small features can be far off.

**Why did the add-on say "the grid method could not be used"?** The engine fell back to pairing; the warning contains the
reason (open borders, density gradient too strong, time limit, failed solve). Please report it.

**Why does symmetry remesh the whole model instead of one half?** A cut-first half has a flat open cap on the plane that
disturbs the engine near the seam. Remeshing the whole model with the plane as a guide line makes the rows align to the
plane, so cutting, mirroring and welding leaves a clean seam. Cut-first remains for open models, triangle output and
QuadriFlow.

**Does symmetry change the shape?** It mirrors one half, so an asymmetric model becomes symmetric. The add-on measures
how symmetric the input is and warns below 90 % match.

**Does it keep UVs / materials / Face Sets?** UVs are approximated by projection (exact except on faces crossing old
seams); materials, Face Sets and mask are copied by closest face.

**How does it compare with Blender's QuadriFlow?** At 20K faces, better squares on 4 of 5 test sculpts and usually
faster; at 80K+ QuadriFlow has fewer poles. Numbers in report section 7.

**Which platforms?** Windows x64, Linux x64, macOS arm64 and x64 binaries are built in CI. Most validation is on Linux
with Blender 5.2; the add-on declares Blender 4.2+.

**Where is the weakest part?** Speed at 120K+ quads (parametrization), thin structures, and extraction being a heuristic.

**How do I add a new engine backend?** Subclass `Backend` in `core/backends/`, register it in `_REGISTRY`
(`core/backends/__init__.py`), add the option to `properties.backend`. The pipeline marks hard edges as `use_edge_sharp`
on the working mesh before calling `remesh_steps(context, obj, request)`.

**How do I add UI text?** A constant in `texts.py`; optional Spanish entry in `translations.py`.
