# Design decision log

Format: context → decision → consequences → status. A decision marked **"to validate"** means it was made with
reasoning but not yet with data measured in Blender; it is confirmed or reverted with `tests/benchmark.py`.

---

### D-01 · Do not write a new solver; wrap existing engines
- **Context:** there are open, proven engines (QuadriFlow, QuadWild, Instant Meshes). A new solver would be a research project
  of months or years, with a high risk of not matching Quad Remesher.
- **Decision:** improve the **pipeline** (prepare, constrain, polish) and leave the engine behind an interface (`core/backends/`).
- **Consequences:** the engine can be swapped without touching the rest; the maximum quality is set by the chosen engine.
- **Status:** in force.

### D-02 · Python + NumPy + Blender's C++; no compiled code of our own in 0.3
- **Context:** Blender requires Python; NumPy vectorizes; decimation, the BVH and QuadriFlow are already optimized C++.
- **Decision:** do not add our own Rust/C++ yet.
- **Consequences:** a single cross-platform zip install; performance is limited by those pieces.
- **Revisit if:** the profile shows a Python stage taking > 30% of the total time.
- **Status:** in force.

### D-03 · Working copy with the Decimate modifier (collapse), applied in place
- **Context:** we need to go from millions of faces to ~4× the target before the engine.
- **Decision:** `modifier_apply` on a temporary object that uses a copy of the mesh; automatic size = 4× the largest
  target (40 K–600 K).
- **Consequences:** simple and dependency-free; collapse does not distinguish hard edges or zones (hence it is fitted to the original
  afterwards and zones are transferred separately). Decimate memory with 3 M faces **to measure**.
- **Status:** to validate.

### D-04 · Fit each level to the original sculpt with a BVH (closest point)
- **Context:** the working copy loses detail; the engine reprojects only onto it.
- **Decision:** `find_nearest` for each vertex on the original's BVH tree; vertices on the symmetry plane are
  returned to the plane.
- **Consequences:** the shape error does not accumulate between levels and symmetry is preserved; Python cost proportional
  to the size of the result.
- **Status:** to validate (measure time and error).

### D-05 · Symmetry by cut + remesh one half + mirror + weld
- **Context:** a symmetry flag inside the engine does not guarantee exact symmetry.
- **Decision:** cut along the plane, preserve the open border, remesh the half, mirror and weld.
- **Consequences:** exact symmetry by construction; divides the engine's work by 2, 4 or 8; if the model is not
  truly symmetric one half is discarded (a warning is shown).
- **Status:** in force (technique already used by QRemeshify).

### D-06 · Level cascade (each level starts from the previous one)
- **Context:** the user goes down from 20 K to 15 K, 10 K, 5 K and 4 K.
- **Decision:** the polished result of one level is the input of the next.
- **Consequences:** very cheap lower levels with edge flow consistent between them; **[assumption]** quality is at
  least equal to always starting from the sculpt. Alternative to compare: independent levels.
- **Status:** to validate (open question Q2).

### D-07 · Zones computed on the full sculpt and transferred by closest face
- **Context:** decimation does not guarantee preserving Face Sets or masks.
- **Decision:** per-face labels on the full sculpt; they are transferred to the working copy with the BVH and carried
  through each level as an internal attribute (`quadforge_zone`, removed from the final result).
- **Consequences:** zones robust to decimation; depends on the BVH returning polygon indices (this is checked).
- **Status:** to validate.

### D-08 · Polish = Laplacian relaxation + fitting, with fixed vertices
- **Context:** the engine's quads can have irregular size.
- **Decision:** N passes; each one moves the free vertices toward the average of their neighbors and refits them to the sculpt.
  Vertices on hard edges and on open borders (including the symmetry cut) do not move.
- **Consequences:** more regular quads without losing corners or the central seam; relaxation shrinks slightly, which is why it
  is always followed by fitting (polish is only offered with "Preserve sculpt detail" enabled).
- **Status:** to validate.

### D-09 · Interface: texts in `texts.py` and translation using the same keys
- **Decision:** a single source of texts; the translation uses the constants as keys, so it cannot get out of sync.
- **Status:** in force.

### D-10 · GPL-3.0-or-later license and "clean" development
- **Context:** Quad Remesher's engine is proprietary; QRemeshify and QuadWild are GPL.
- **Decision:** our own code inspired by the **behavior** and ideas of existing tools; no third-party code is copied or
  decompiled. A license compatible with Blender so that others can reuse it.
- **Status:** in force. See `REFERENCES_AND_LICENSES.md`.

---

## v0.4 decisions (from the first real-world test, see `CASE_BULL_ANT.md`)

### D-11 · Hard edges as chains, with a quad-size filter and straight corners
- **Context:** with only "angle between faces > threshold", noise generates short chains that quads can never follow, and a
  corner smaller than two quads of the target cannot be represented.
- **Decision:** `core/features.py`. Hard edges are grouped into connected chains; a chain is kept if it has at least
  one edge sharper than the angle and is at least 2 quad lengths of the **target** long, or if it is a stretch of ≥ 1 quad length
  made up of 90° ± 20° angles. Hysteresis is implemented but turned off.
- **Data:** on the ant (target 20 K) it removes 55% of the chains and 7% of the edges; hysteresis almost doubles the
  constraints.
- **Consequences:** fewer useless constraints; the "Minimum Feature Size" parameter allows tuning. **Status:** to validate.

### D-12 · Consistent face orientation after the mirror
- **Context:** in the real test one half ended up with inverted faces (74% versus 26% outward-facing faces).
- **Decision:** after `bmesh.ops.mirror`, edges whose two faces traverse it in the same direction are counted; if there are any,
  the mirrored copy is flipped (undone if it gets worse) and, as a last resort, everything is oriented consistently. The metric
  `inconsistent_edges` and a warning in the report were added.
- **Consequences:** fixes the shading with a center line. **Status:** to validate.

### D-13 · Seam welding with a tolerance relative to the quads
- **Context:** the engine leaves vertices of the cut border slightly off the plane; with a tolerance of 1e-4 × diagonal they did not join.
- **Decision:** vertices of the **open border** closer than 0.4 times the median length of those edges are moved onto the plane before mirroring.
- **Consequences:** closed seams; low risk of moving a real border that sits very close to the plane. **Status:** to validate.

### D-14 · Fitting to the sculpt with safeguards against collapses
- **Context:** when fitting to the sculpt, several neighboring vertices landed on the same point (edges with 5 to 8 triangles).
- **Decision:** maximum displacement of 1.5 times the local size; if a move would shrink an edge below 15% of its
  length, both vertices stay where they were.
- **Consequences:** no collapses; fitting may end up somewhat incomplete in very thin areas. **Status:** to validate.

### D-15 · Corners fitted exactly onto the hard-edge lines
- **Context:** the engine respects hard edges only approximately and fitting to the surface leaves a vertex on one side or the other of a sharp edge,
  which rounds right angles.
- **Decision:** vertices already running along a corner of the result are moved to the closest point of the model's edge lines
  (sampled every half quad length and searched with a KD tree), if they are within 0.6 quad lengths. Vertices of the
  open border (seam) are never touched. The lines are computed once and reused across all levels.
- **Consequences:** sharper corners; the cost grows with the number of lines. **Status:** to validate.

### D-16 · Automatic shading: smooth with marked hard edges
- **Context:** everything smooth made corners look odd; the user had to apply Shade Flat.
- **Decision:** by default faces are smooth and the result's hard edges are marked as *sharp*; Smooth and Flat options.
- **Status:** in force.

### D-17 · The target warning uses the quad equivalent
- **Context:** 37,630 triangles are ≈ 18,815 quads; asking for 20,000 quads does not simplify.
- **Decision:** the target is compared with `(corners − 2 × polygons) / 2`, which counts each triangle as half a quad.
- **Status:** in force.
