# Status and limits — QuadForge Vivaldi 0.10.31

## Tested
- FORGE engine (Linux): sphere, torus, box with 12 exact hard edges, hemisphere with border, cylinder, density (length ratio 2.99 vs 3 requested), meshes with defects, 120 random cases with no failures, 1.3 M tris → 40 K in ~7 s (peak ~390 MB).
- Python backend with the real engine through a simulated mesh.
- Windows and macOS binaries compiled with Zig; **not executed**.

## Tested inside Blender (5.2.2, `bpy` module, headless)
- 28 original scenarios + 10 FORGE scenarios (`tests/blender_integration_test.py`, `tests/blender_forge_test.py`): pipeline, symmetry, levels, zones, density vertex group, quads/triangles output, QuadriFlow route, operator, cancel (engine process stops, no leftovers), and installation of the real extension zip.
- Models by the author: hand (119K tris), bull ant with peg (119K), hard-surface plate (8K, 4 through holes): all produce valid, closed, consistently oriented meshes.

## Measured: FORGE vs QuadriFlow (2 CPU cores)
Target 4000 quads (`devtools/compare_engines.py` for QuadriFlow):
| Model | Engine (method) | Poles / 1000 quads | Regular vertices | Time |
|---|---|---|---|---|
| Bull ant | FORGE 0.7 (integer grid) | 40 | 96 % | 2.3 s |
| Bull ant | FORGE 0.5.1 (pairing) | 103 | 89 % | 1.6 s |
| Bull ant | QuadriFlow | 54 | 95 % | 4.9 s |
| Hard-surface plate (no marked edges) | FORGE 0.7 (integer grid) | 15 | 98 % | 1.0 s |
| Hard-surface plate (12 % marked edges) | FORGE 0.7 (pairing path) | 58–80 | 92 % | 0.4 s |
| Hard-surface plate | QuadriFlow | 20 | 98 % | 5.2 s |
| Hand | FORGE 0.7 (integer grid) | 11–14 | 98.6 % | 1.7 s |
| Hand | FORGE 0.5.1 (pairing) | 94 | 90 % | 1.4 s |
| Hand | QuadriFlow | 17 | 98 % | 4.9 s |

At higher counts FORGE 0.7: hand 20 000 quads 2.7 poles/1000 (13 s), 80 000 quads 2.8 (35 s); ant 20 000 quads 12, 80 000 quads 6.7 (67 s). QuadriFlow on your machine: about 1 pole per 1000 at 80–120 K quads, 30–100 s.
FORGE is faster at every size and now has fewer poles at 4000 quads; QuadriFlow still has roughly 2–3x fewer at 80 K quads and aligns better to hard edges.

## Benchmark against Blender's QuadriFlow (0.10.31, 20 000 faces, 2 CPU cores, no symmetry)

Scaled Jacobian (1 = perfect square, mean / worst 1 %), share of irregular vertices, and time. QuadriFlow = `bpy.ops.object.quadriflow_remesh`; FORGE default = Detail on Curves Off; FORGE face = Medium.

| Model | Method | Faces | Jacobian mean | Jacobian worst 1% | Irregular % | Seconds |
|---|---|---|---|---|---|---|
| head | QuadriFlow | 20236 | 0.988 | 0.841 | 0.31 | 16 |
| head | FORGE default | 19558 | 0.985 | 0.771 | 0.32 | 28 |
| head | FORGE face | 19966 | 0.980 | 0.797 | 0.60 | 32 |
| fairy | QuadriFlow | 17079 | 0.966 | 0.565 | 0.46 | 12 |
| fairy | FORGE default | 19877 | 0.983 | 0.714 | 0.73 | 20 |
| fairy | FORGE face | 20006 | 0.983 | 0.785 | 0.72 | 24 |
| skull | QuadriFlow | 20119 | 0.980 | 0.763 | 0.51 | 25 |
| skull | FORGE default | 19751 | 0.987 | 0.886 | 0.41 | 8 |
| hand | QuadriFlow | 18803 | 0.971 | 0.739 | 0.50 | 29 |
| hand | FORGE default | 20500 | 0.993 | 0.918 | 0.24 | 11 |
| ant | QuadriFlow | 19451 | 0.951 | 0.483 | 1.40 | 28 |
| ant | FORGE default | 19936 | 0.975 | 0.758 | 1.08 | 13 |

Reading: FORGE has better squares on 4 of 5 models, hits the face target (QuadriFlow missed it by 15 % on the fairy) and is faster on 3 of 5. QuadriFlow is a little better on the plain head (shape quality) and has fewer irregular vertices on the fairy. Detail on Curves raises the share of irregular vertices on purpose (more detail and rows that follow the creases).

## Triangle output against Blender's Decimate (Collapse), same triangle count

Smallest triangle angle (mean / worst 1 %), share of triangles under 20 degrees, worst distance from the source surface to the result (% of model size). Measured on the 297k-triangle sculpts, 2 CPU cores.

| Model | Tris | Method | Min angle mean | Worst 1% | Under 20 deg | Worst distance | Time |
|---|---|---|---|---|---|---|---|
| head | 20000 | FORGE | 53.0 | 41.1 | 0 % | 0.69 % | 2.6 s |
| head | 20000 | Decimate | 34.4 | 10.8 | 12.1 % | 1.57 % | 1.9 s |
| head | 5000 | FORGE | 53.0 | 41.0 | 0 % | 1.57 % | 2.4 s |
| head | 5000 | Decimate | 33.9 | 8.2 | 14.4 % | 3.03 % | 2.4 s |
| hand | 5000 | FORGE | 52.9 | 40.7 | 0 % | 1.27 % | 0.9 s |
| helmet | 30000 | FORGE | 52.8 | 40.5 | 0 % | 0.95 % | 4.7 s |
| helmet | 30000 | Decimate | 27.0 | 2.4 | 36.6 % | 2.23 % | 2.8 s |
| hand | 5000 | Decimate | 28.7 | 8.0 | 28.3 % | 3.91 % | 0.7 s |

Reading: FORGE triangles are close to equilateral and follow the surface about twice as closely. Decimate keeps the original vertices and FORGE does not: FORGE rebuilds the mesh. Since 0.10.31 the UV maps of the original are copied onto the result (Copy UV Maps), accurate except on faces that cross an old seam. Not adopted: curvature / thickness adaptive density in triangle mode (8x slower and the worst distance got worse).

## Tried and not adopted (0.10.31 work)

- Chord-rotation pole optimiser on the final quads (`FORGE_POLEMOVES`): fairy 145 -> 140 poles, about 3 %. The poles come from the field singularities, not from local moves.
- Distance rings around creases (`FORGE_RING`, `FORGE_RINGR`): eyes and nostrils wrap a little better, but with symmetry X the fairy went from 149 to 298 poles and the seam opened in a lighter setting. Off by default; kept as an experiment switch.

## Which quad method runs (automatic)
- **Integer-grid parametrization (0.7)**: closed surface, no density group, fewer than 1 % of the edges marked hard. Quads come from the iso-lines of a seamless (u, v) map; every face is then split into quads and relaxed on the surface. Fallback: the 0.6 position-field lattice (`--no-param`).
- **Triangle pairing (0.5.x)**: only as a fallback, when the integer-grid method fails or cannot follow a strong density gradient. Marked hard edges (0.8), painted density (0.9) and open borders (0.10) go through the integer-grid method. Pairing keeps hard edges and borders exactly.
Open borders: the engine closes every border loop with a cap, treats the loop as a hard-edge line, runs the integer-grid method and removes the quads on the cap. Loops shorter than about 6 quad edges are left closed (the hole disappears). The border may get a few triangles where very short edges were welded.

## Not tested
- Blender 4.2 to 5.1 (only 5.2.2 is available for automated tests); Windows and macOS on real machines.

## Limitations
- Cells thinner than the quad size (very thin legs, antennae) can come out ragged or open.
- Hard-surface models with density groups on a smooth shape with very few singularities may fall back to the older pairing path (~60–80 poles/1000 quads). Caps of non-convex or very deep holes can intersect the model; then the engine falls back to pairing.
- **Finger tips, 120 000+ quads (improved in 0.10.13)**: where the parametrization stretches the grid (3–5× at thin tips) the isotropic mesh had fewer vertices than lattice points, so some cells were lost and filled with irregular patches. 0.10.13 subdivides stretched triangles virtually before extracting cells (hand at 118K: poles 280 → ~60, holes 13 → 5). A few holes can remain in the thinnest tips; they are filled and the result stays valid. Debug switch: `FORGE_NOSUBDIV=1`.
- Large counts take tens of seconds (a plain conjugate-gradient solver); QuadriFlow is slower still.
- QuadriFlow route cannot be cancelled (Blender's operator is blocking); FORGE can (Esc).
- No guide curves and no directly painted density (vertex group only).
- Robustness suite (`t07_robustness.py`): 114/115 cases pass (0.10.31). Known miss: very small targets on hard-surface parts with many small features (a gear asked for 600 faces gives 372).
- Symmetry with FORGE quads on closed models: the whole model is remeshed with the planes as guide lines, then cut and mirrored (0.10.13; ant: poles 570 → 324, no open seam edges). The other cases still cut first.
- With symmetry the result is mirrored: if the model is not symmetric the shape changes (a warning is shown).

## Suggested roadmap
0.5.x: fixes after the author's tests. 0.6: lattice extraction. 0.7: integer-grid parametrization. 0.8: alignment to hard edges. Next: borders, a faster solver, thin-part handling. Then the Rust port (parallel version) and publishing.
