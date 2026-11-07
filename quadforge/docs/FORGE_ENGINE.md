# FORGE engine (0.13.0)

QuadForge's own remeshing engine. C++17 program with no dependencies, built for Windows, Linux and macOS (arm64 and x64) with Zig. Source is in `engine_src/` (`python engine_src/build.py` rebuilds it; needs `pip install ziglang`).

## How it works
1. The add-on writes the mesh to an `.fmesh` file (positions, polygons, hard edges, optional density).
2. It runs `forge remesh --in A --out B --faces N --mode quad|tri` as a separate process and reads its progress (`P <percent> <message>`).
3. It reads the result and replaces the geometry of the working mesh.

## Engine stages
- **Repair:** triangulates, removes repeated faces, orients components, splits non-manifold edges and pinched vertices.
- **Isotropic remeshing:** split edges > 4/3 L, collapse < 4/5 L, flip toward balanced valences, tangential smoothing and reprojection onto the original surface (BVH). Hard edges are chains the vertices only slide along.
- **Quads, integer-grid method (default on closed surfaces without many marked edges):** the surface is remeshed (6 triangles per lattice cell, no features), a smooth cross field is solved on a coarser copy and transferred, and `param.hpp` computes a seamless integer-grid parametrization: face-to-face matching of the four directions, singular vertices, tree/cotree cut graph with a gauge per face, one (u, v) per corner sheet, least squares with gradients U/rho and V/rho, seams as stiff penalties with free translations (shared along a cut path), then the singular vertices and the translations are rounded step by step (the last translation of a junction follows from the others). Vertices round to lattice nodes, vertices with the same node merge, neighbouring nodes one step apart link, the cell graph's rotation system gives faces, every face is split into quads and the result is relaxed on the surface. The lattice spacing is corrected up to twice (warm-started) so the face count lands within a few percent. Source: `param.hpp`, `extract.hpp`. The 0.6 position-field lattice remains as `--no-param`.
- **Quads, pairing method (borders, many marked edges, density, fallback):** smooth cross field (constrained by hard and open edges) → triangle pairing with pole-aware annealing → subdivision 1 quad → 4 quads → field-aligned relaxation. Source: `quad.hpp`.
- `quadopt.hpp`: optional pole optimisation by chord rotations (off by default: ~10 % fewer poles but worse angles).
- CLI: `--quad-method auto|extract|pair`, `--ex-iters`, `--ex-density` (default 6), `--ex-subdiv`, `--no-param`, `--no-align`, `--no-adaptive`, `--adaptive-ratio` (default 1.3), `--adaptive-min` (default 0.5). The result statistics (`S` line) include `"method"`.
- **Density:** per-vertex edge-length multiplier, interpolated over the surface.

## What it does NOT do yet
- Hard edges and borders are not aligned by the parametrization yet (those models use the pairing path). Planned: integer constraints along features, a faster solver (preconditioner or multigrid), adaptive size for thin parts, guide curves.
- No cancellation midway.
- macOS binaries are unsigned: they may need to be allowed to run.


## Adaptive size and density (0.9)
- `sizefield.hpp` measures the local thickness of the shape with the shrinking-ball method (radius of the largest ball that touches the surface at a vertex and stays inside). Where the surface is also curved (tubes, not flat walls) the quad edge is limited to `adaptive-ratio` x that radius, never finer than `adaptive-min` of the global size, graded at 0.5 size growth per unit of distance. The result is multiplied into the density (edge-length multiplier) before remeshing. `FORGE_NOADAPT=1` or `--no-adaptive` switches it off; env `FORGE_ADAPT_RATIO` / `FORGE_ADAPT_MIN` override the values.
- The integer-grid parametrization reads the density per face (target gradient = field / (rho x density)), so painted density groups now go through the integer-grid method instead of the pairing path.
- A size variation the surface cannot follow (a smooth shape with few singularities, e.g. a sphere with a strong gradient) makes the fit fail. The engine checks the fit (`faceErr`), softens the density (power 1, 0.5, 0.25, 0), checks that the result is closed and that big-density areas really got bigger quads, and otherwise falls back to the pairing path. The fallback now always yields quads (before 0.9 it could return triangles).
- The cross field uses edge weights (edge length / distance between face centres), so it no longer depends on how uneven the triangles are.

## Open borders and quad quality (0.10)
- `capping.hpp`: every boundary loop gets a centroid fan; loops of at least 6 estimated quad edges become feature lines (so the grid aligns to the border) and their cap triangles are labelled. After extraction the quads whose vertices lie on the cap side are removed (vertices on the border line are neutral), then edges shorter than 0.2 x the mean edge are welded if the result stays manifold. `FORGE_NOCAP=1` disables it. If the extraction fails the original mesh is used for the pairing fallback.
- `quadquality.hpp`: every vertex that touches a quad with scaled Jacobian below 0.6 is moved along the surface to maximise the worst Jacobian of its quads (40 sweeps, candidates around the neighbour average, projected back on the surface). Vertices on marked edges stay. `FORGE_NOQQ=1` disables it. On the ant it removes inverted quads; hand and plate end with no quad below 0.5.
- Dead-end vertices: a vertex shared by exactly two quads that share both its edges is removed by merging the pair into one quad (`FORGE_NOMERGE2=1` disables it); on the hand at 20 000 quads this removes the last badly shaped quads.
- Experimental, off:  `FORGE_AMG=1` multigrid preconditioner; `FORGE_NOFW=1` switches the geometric field weights off.

## Progress and time limit (0.10.2)
- The grid solve reports progress at 44–77% of the engine bar (`param.hpp` phases). `--time-limit S` (default 900) aborts a solve that runs longer and the engine uses the pairing path.
- The first quad-size estimate is multiplied by 0.98 (the first pass gives about 3% too few quads), and for 30 000 or more quads a count error up to 6% is accepted so that a second full solve is not needed.

## Multi-core solver (0.10.2)
- `paramdetail::parallelCG` in `param.hpp` runs the whole Jacobi-CG iteration on a team of threads with a spinning barrier; rows are split in fixed chunks so reductions are deterministic for a given thread count. Used when the system has at least 40 000 unknowns and the multigrid preconditioner is off (default). The thread count is `--threads` (default: the CPU core count, at most 16).
