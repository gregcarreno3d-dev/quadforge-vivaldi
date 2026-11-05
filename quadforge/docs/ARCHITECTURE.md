# Architecture — QuadForge Vivaldi 0.11.0

Technical document for developers and AIs. Everything described exists in this package.

## 1. Overview
```
Blender (Python)                                   Separate process (C++)
────────────────                                   ──────────────────────
UI/properties → pipeline.run()                     bin/<system>/forge remesh ...
   stages 1-9  → backends/forge.py ──.fmesh──►      repair → isotropic remesh → (quads)
                                   ◄──.fmesh──      stdout: P / R / S / OK / ERR
```
The engine runs as a **subprocess**: a failure cannot close Blender and the run can be killed. No external dependencies.

## 2. Pipeline (`core/pipeline.py`, function `run`)
1. Read the source (modifiers applied; the original is untouched) and build a BVH of the surface.
2. (FORGE) turn the density vertex group into the `quadforge_density` attribute (length multiplier = 1/(1+(ratio−1)·weight)).
3. Zone labels (material / Face Set / mask).
4. Working copy (decimation) — **skipped with FORGE**.
5. Symmetry plan. FORGE with quads on a closed model remeshes the WHOLE model with the symmetry planes marked as guide lines, then cuts at the planes and mirrors (no flat cap disturbs the engine). QuadriFlow, triangles and open models cut the half first (`FORGE_HALF_FIRST=1` forces this).
6. For every level: mark hard edges as `use_edge_sharp` → engine → copy face data → (QuadriFlow: corner snap and polish).
7. Mirror and weld.
8. Create result objects (`<name>_quads|_tris[_20K]`), same transform as the original.
9. Quality warnings.
With FORGE, `RemeshSettings` is replaced by a copy with `use_proxy=False, use_reproject=False, snap_features=False, polish_passes=0`, and every level starts from a copy of the cut source.
Rules: the original is never modified; temporary data is always deleted (`TemporaryData`); bulk work uses NumPy (`mesh_io`, `geometry`).

### Cancellation
`pipeline.run_steps` is a generator that yields after every stage and while the engine works; `pipeline.run` just drives it to the end. The panel button starts `quadforge.remesh_modal`, a modal operator with a timer that advances the generator for ~30 ms per tick and checks Esc. Closing the generator raises `GeneratorExit` at the current `yield`; the `finally` blocks kill the engine process (`run_engine_steps`) and `TemporaryData` deletes the temporary datablocks. The engine's stdout is read by a helper thread (no Blender calls from threads).

## 3. Backends (`core/backends/`)
`Backend.remesh(context, obj, RemeshRequest)` modifies `obj.data` in place. `RemeshRequest`: `target_faces, preserve_sharp, preserve_boundary, seed, output (QUADS|TRIS), threads, progress`. Registry in `__init__.py` (`FORGE`, `QUADRIFLOW`).
`forge.py`: finds the binary (`bin/<windows|linux|macos-arm64|macos-x64>/forge[.exe]`, adds the execute permission if missing, or copies it to a temp folder), reads the mesh with `foreach_get`, writes `.fmesh`, launches the process (no window on Windows), reads progress, and rewrites the geometry with `foreach_set` (with a `from_pydata` fallback).

## 4. Engine command line
```
forge remesh --in A.fmesh --out B.fmesh --faces N [--mode tri|quad] [--threads N]
             [--no-project] [--corner-angle DEG] [--iterations N] [--relax N]
             [--field-align X] [--no-field] [--stats] [--check]
```
stdout, one line at a time: `P <percent> <message>`, `R <repair report json>`, `S <statistics json>`, `OK`, `ERR <message>` (exit code 2). In `quad` mode, `--faces` is the final quad count (the engine asks for N/2 triangles).

## 5. `.fmesh` format (little endian; `core/forge_io.py`, `engine_src/meshio.hpp`)
`"FMSH"`, u32 version=1, nV, nPoly, nIdx, nFeat, flags (bit0 = density present); then f32 positions[3·nV], i32 polygon sizes[nPoly], i32 indices[nIdx], i32 hard edges[2·nFeat], f32 density[nV] if flags&1.

## 6. Engine (`engine_src/`, C++17)
| File | Role |
|---|---|
| `vec.hpp`, `bvh.hpp` | Vectors, closest point on triangle, surface and segment BVH (reprojection). |
| `meshio.hpp` | `.fmesh` read/write. |
| `repair.hpp` | Triangulates; removes faces with repeated vertices and duplicates; orients components; splits non-manifold edges and pinched vertices; compacts. |
| `cmesh.hpp` | Corner-table mesh with safe flip/split/collapse (link condition, valences, normals) and `check()`. |
| `remesh.hpp` | Isotropic remeshing (Botsch–Kobbelt): split > 4/3·L, collapse < 4/5·L, flip toward valence 6 (4 on borders), tangential smoothing + reprojection with a normal filter; crease vertices slide along hard chains; per-vertex density; the count is steered with feedback rounds. |
| `field.hpp` | Per-face 4-RoSy cross field (z=exp(i4θ)), constraints on hard/open edges, conjugate gradient with Jacobi preconditioner. |
| `param.hpp` | Seamless integer-grid parametrization (field matching, singular vertices, tree/cotree cut graph, sheets, penalty least squares solved by conjugate gradient, integer rounding of singular vertices and seam translations). |
| `extract.hpp` | Lattice method (uses `param.hpp` for the grid when it can, otherwise a position field): vertex 4-direction field from the transferred face field, position field (Gauss–Seidel, BFS start), snap + merge into cells, rotation-system face tracing, hole filling, 1→4 subdivision, surface relaxation. `main.cpp` decides the method (`--quad-method auto`) and corrects the lattice spacing to hit the face count. |
| `quadopt.hpp` | Optional pole optimisation of a quad mesh by chord rotations (annealing). |
| `quad.hpp` | Triangle pairing (greedy + augmenting paths), subdivision (quad→4, leftover triangle→3, all quads), relaxation toward the nearest square turned to the field, reprojection. |
| `quad.hpp` (pairing refinement) | After the greedy pairing, `refineMatching` anneals over alternating cycles of the dual graph to make `k(v) - d(v)` equal to the ideal valence (k = triangles at v, d = removed diagonals at v). Parameters `--pole-weight`, `--pole-sweeps`, `--cycle-pairs`. |
| `metrics.hpp`, `parallel.hpp`, `main.cpp` | Statistics, parallelism, CLI. |
Rebuild: `pip install ziglang && python engine_src/build.py [linux|windows|macos-arm64|macos-x64]` (writes to `bin/`).

## 7. Tests
- `tests/blender_forge_test.py` and `tests/blender_integration_test.py`: run the add-on inside Blender (`pip install bpy==5.2.2` works headless: `python tests/blender_forge_test.py <models folder>`).
- `devtools/`: engine tests, STL reader, QuadriFlow comparison, packager.
- `tests/test_forge_backend.py`: real backend + real binary with a simulated Blender mesh (sphere, box with hard edges, density, controlled error). Needs `meshgen.py` from the development repository.
- `tests/test_guidance.py`, `test_planning.py`, `test_geometry.py`, `test_features.py`: pure rules.
- `tests/blender_integration_test.py`: scenarios to run inside Blender (not yet validated).
- During development the engine was validated with 60 random triangle cases and 60 random quad cases (0 invalid), meshes with defects, and meshes of 1.3 M triangles.

## 8. Extending
New engine: add a `Backend` class in `core/backends/`, register it in `_REGISTRY`, add its option to `properties.backend`. New UI text: a constant in `texts.py` (English) + an optional entry in `translations.py`.
