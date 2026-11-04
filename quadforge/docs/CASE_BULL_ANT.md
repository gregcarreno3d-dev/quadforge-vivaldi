# Case study: bull ant (first real test, v0.3)

Material: two of the user's STL files, `PRINT_LP_Peg_Bull_Ants_tris_V01.stl` (triangulated input) and
`PRINT_LP_Peg_Bull_Ants_quads_V01.stl` (QuadForge 0.3 output, exported as triangles).
The numbers in this document come from an independent NumPy analysis of those files **[verified]**.
What the user saw on screen (odd shading, a visible line down the middle) comes from their description.

> A method error that was corrected during the analysis: the first face-angle calculation used an incorrect mapping
> from edges to faces and produced absurd values (86% of edges "hard"). It was caught and redone. Lesson:
> check your own analyses against known data before drawing conclusions.

## 1. The input (triangulated mesh)

| Item | Value |
|---|---|
| Triangles | 37,630 (≈ 18,815 equivalent quads) |
| Topology | Closed, a single shell, consistent face orientation |
| Surface area | ≈ 494 units² |
| Edges with angle > 20° / > 35° / > 60° | 10,916 / 5,634 / 2,336 |
| Hard-edge chains at 35° | 477; 58% have 3 edges or fewer (noise), the largest have 330–460 edges |

Observation: asking this mesh for 20,000 quads (≈ 40,000 triangles) **does not simplify it**, it makes it denser. v0.4 warns
when the target is not smaller than the detail the mesh already has (the comparison is against the quad-equivalent, not
the triangle count).

## 2. The v0.3 result (21,456 quads, X symmetry)

| Finding | Evidence | Interpretation |
|---|---|---|
| **One half of the model came out inside-out** | On the x < 0 half, 74% of faces point outward; on the x > 0 half only 26% do | The mirrored copy came out with its faces flipped |
| **Visible line down the middle** | ≈ 409 edges where both faces traverse the edge in the same direction; 418 edges with angle > 150° | This is the symmetry seam: opposite normals on each side. It explains the odd shading and why it improves (only partly) with Shade Flat |
| **Gap next to the seam** | 12 open edges with x between −0.04 and +0.04 (on both sides of the plane), y ≈ 1.5–2.1, z ≈ 2.5–3.0 | The engine left border vertices slightly off the plane, and the weld tolerance (diagonal × 1e-4) was too small |
| **Collapsed vertices** | 6 edges shared by 5 to 8 triangles, in a symmetric pair near (±1.18, −6.95, 2.28) | When fitting to the sculpt, several neighboring vertices landed on the same point (a corner or thin area). It appears on both sides because it happened before mirroring |
| Symmetry | With a tolerance of 2e-3 of the diagonal, 100% of vertices have a mirror partner in X | The geometric symmetry is correct |

**Cause of the inverted half:** the code only recomputed normals when the resulting mesh was completely closed.
Because there were gaps, that step was skipped and the orientation ended up mixed. A subtle defect: one failure (the gap)
hid another (the inversion).

## 3. What changed in v0.4 and why

| Defect / request | Change | Decision |
|---|---|---|
| Inverted mirrored half | After mirroring, orientation is checked edge by edge and the mirrored copy is flipped; if that is not enough, everything is oriented consistently | D-12 |
| Gaps at the seam | Open-border vertices closer than 0.4 times the typical edge length are moved onto the plane before mirroring | D-13 |
| Collapsed vertices | The fit to the sculpt limits displacement to 1.5 times the local size and leaves still the vertices of edges that would shrink below 15% | D-14 |
| Better detection of hard zones and right angles | Detection by chains with a quad-size filter and preservation of 90° corners | D-11 |
| Keep the shape of corners | Corner vertices move exactly onto the model's border lines | D-15 |
| Smooth shading with an odd line | By default: smooth faces with hard edges marked as *sharp* (Smooth and Flat options) | D-16 |
| Target larger than the existing detail | Warning that compares against the quad-equivalent | D-17 |
| More quality | **Detail 80K** and **Detail 120K** presets; the automatic working copy supports up to 600 K faces | D-03 |

## 4. Detection prototype (data that set the default values)

On the triangulated ant, with a 35° angle and a target of 20,000 quads (quad length ≈ 0.157):

| Variant | Hard edges | Chains |
|---|---|---|
| Angle only (v0.3) | 5,634 | 477 |
| Chains ≥ 2 quad lengths (**chosen**) | 5,234 | 215 |
| Chains ≥ 3 quad lengths | 5,076 | 177 |
| Hysteresis (weak edges at 0.6 × angle) | 9,073 | 152 |

- The size filter removes more than half of the chains but only ≈ 7% of the edges: it removes noise without touching the
  real borders.
- Hysteresis nearly doubles the constraints (from ≈ 10% to ≈ 16% of edges). It is implemented (`low_ratio`) but **turned off**
  and has no UI control, pending quality data.
- The filter adapts to the target: with 80,000 quads it leaves 5,422 edges (removing ≈ 4%), with 20,000 it leaves 5,234 (≈ 7%) and with 5,000 it leaves 4,960 (≈ 12%),
  because the fewer the quads, the larger each quad is and the more short chains stop being representable.

## 5. Questions this case leaves open

1. Are the hard edges the engine "loses" recovered well enough by the corner fitting? (measure distance to the border lines)
2. What minimum detail size (1, 2 or 3 quads) gives the best visual result on this model?
3. Does hysteresis improve edge continuity or just add constraints?
4. What about zones with gentle but flat angles (20°–30° chamfers)? They are not detected as hard edges.
