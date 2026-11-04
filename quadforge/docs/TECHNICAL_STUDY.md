# Technical study: quad remeshing optimized for heavy sculpts

Study version: QuadForge 0.4.0 (the 0.3 base design plus the fixes from the first real-world test: see `CASE_BULL_ANT.md`)

Original study version: QuadForge 0.3.0 · Status: **design complete, validation pending** (see section 10)

> How to read this document: every claim is marked **[verified]** (checked against a source or a measurement),
> **[assumption]** (reasonable, but not checked) or **[to measure]** (resolved with `tests/benchmark.py`).

## 1. Goal and workflow

The use case that motivates the project (a real 3D sculptor's workflow):

```
sculpt of 1.7 – 3 million polygons
   → intermediate mesh of ~80 K
      → quad mesh of ~20 K
         → lower versions: 15 K, 10 K, 5 K, 4 K
```

Requirements: (1) preserve as much detail as possible, (2) exact symmetry, (3) "nice" topology that helps with later
modeling and sculpting, (4) balanced, stable use of CPU, GPU and RAM, (5) reuse what already exists
instead of rewriting everything, (6) leave the research documented so others can continue it.

## 2. What exists today

| Tool | What it is | License | What was learned |
|---|---|---|---|
| **Quad Remesher** (Blender bridge, v1.4) | The GPL bridge to a proprietary external engine | GPL v2+ bridge; proprietary engine | The bridge only exports FBX, writes a settings file, launches the `.exe` and reads a progress file. Useful as a **functional specification** (quad target, painted density, symmetry, materials, angle). The algorithm is not in those files. |
| **QRemeshify** (Blender extension) | Wraps the QuadWild libraries with `ctypes` | GPL-3.0-or-later | Three native stages (direction field, patch tracing, integer-optimization quadrangulation). Symmetry by **cut + mirror**. Exchanges OBJ over disk and blocks the interface. |
| **QuadriFlow** (built into Blender) | Quad remesher included in Blender | Part of Blender (GPL) | It is the engine QuadForge uses today. See section 3. |
| **AutoRemesher** | Open remesher with a Blender bridge | MIT (according to the press release cited) | Candidate for a future backend. OBJ only, no UVs. |

## 3. Known problems with Blender's engine (QuadriFlow)

**[verified]** in public reports (see `REFERENCES_AND_LICENSES.md`):

- With very dense meshes it is slow, uses a lot of RAM and does not respond well to cancelling with Esc (issue #70358).
- There were crashes with large meshes in specific versions (#69866, #105953).
- Users asked for symmetry to speed it up; the community solution is to cut in half, preserve borders and mirror.

**[assumption]** Those reports date from 2019 to 2023. No 2026 information was found on the state of things in Blender 5.x, so
**how much of this persists is [to measure]**. The design assumes that a quad engine **behaves better with small inputs**,
which is accepted in practice (hence the manual 3M → 80K → 20K workflow).

## 4. State of the art (research)

- **Quads guided by feature lines** (QuadWild / BiMDF line): guarantees that marked lines end up as patch
  borders. **[verified]** It is the best option for preserving corners; CPU-heavy.
- **Multiresolution** for meshes with millions of triangles: propagates the parameterization from coarse to fine levels to
  keep things interactive. **[verified]** An idea related to ours (working with lightweight versions), but
  QuadForge does **not** implement the paper's algorithm; it uses decimation + post-hoc fitting.
- **2026:** generative methods appear (diffusion for simple layouts, reinforcement learning with subregions run in
  parallel). **[verified]** that they exist; **[assumption]** that they are not yet production tools. They were not evaluated.
- **GPU:** the work found accelerates remeshing of **triangles** and decimation (for example, reducing most
  meshes in a benchmark to 1% in ~2 s). **[verified]** No GPU solver for quad layouts was found.

**Conclusion:** writing a new solver from scratch is a high-risk research project. The immediate practical gain
is in the **pipeline around the engine**.

## 5. v0.3 design (what was implemented)

```
original sculpt (millions of faces)
   │  evaluated copy + BVH tree of the original surface (kept until the end)
   ▼
[1] zone labels (materials / Face Sets / mask) computed on the full sculpt
   ▼
[2] WORKING COPY: Decimate (collapse) in place → ~4× the largest target (40 K – 600 K faces)
   ▼
[3] symmetry plan + cut by plane → only ONE half is remeshed (÷2, ÷4 or ÷8)
   ▼
[4] for each level (20 K → 15 K → 10 K → 5 K → 4 K):
       hard edges (angle + marked + seams + zone borders) → engine (QuadriFlow)
       → restore materials/zones → POLISH (relax + fit to the original sculpt ×N)
       → the polished result is the input of the next level
   ▼
[5] mirror + weld of each level → objects `<name>_quads_20K`, `_15K`, ...
```

Why each piece:

| Piece | Problem it addresses | Resource it saves |
|---|---|---|
| Working copy | The engine on millions of faces is slow and uses a lot of RAM | Engine CPU and RAM |
| Cut by symmetry | Less geometry for the engine and exact symmetry | CPU, RAM |
| Level cascade | Each level starts from ~20 K quads, not from the sculpt | CPU (lower levels almost free) |
| Fit to the original sculpt | The working copy loses detail | Quality (not expensive) |
| Polish (relax + fit) | Irregularly sized quads | Quality |
| Built-in profiling | Without numbers there is no demonstrable optimization | Method |

All mesh analysis uses **vectorized NumPy** and bulk reads (`foreach_get`/`foreach_set`). Distance queries use
**Blender's BVH tree (C++)**. Decimation is **Blender's Decimate modifier (C++)**.

## 6. Resource budget (CPU / RAM / GPU)

**Real measurement made during design [verified]:** the full NumPy analysis (normals, dihedral angles,
region borders and statistics) of a 1-million-quad mesh took ~1.5 s with a peak of ~890 MB
(including building the arrays). It is an order of magnitude for the cost of reading a mesh in NumPy.

| Stage | Dominant resource | Status |
|---|---|---|
| Evaluated copy of the sculpt (`new_from_object`) | RAM (one full copy) | **[to measure]** |
| BVH of the original | RAM, CPU (one-time build) | **[to measure]** |
| Decimate (collapse) on millions of faces | CPU (probably single-threaded) and RAM | **[assumption]** The modifier works internally with BMesh, which is expensive in RAM; **measure with 1.7 M and 3 M** |
| Engine on the half of ~80 K | CPU | **[to measure]** |
| Fit/polish (`find_nearest` per vertex, in Python) | Single-thread CPU; grows with the size of the **result**, not of the sculpt | **[to measure]** |

**GPU.** This version **does not use the GPU**, and that is a reasoned decision, not an omission:

1. The likely bottlenecks (decimation and quad solver) are CPU-bound and there is no ready-to-use open GPU version.
2. The operation that is "embarrassingly parallel" (closest-point queries) handles tens or hundreds of thousands of
   points in the result, which the CPU BVH resolves in seconds. The GPU would only pay off with **millions** of queries.
3. Using the GPU from an addon forces maintaining different paths per system (CUDA, Metal, Vulkan, OpenGL).

**Criterion for reopening the decision:** if the benchmark shows that fitting to the sculpt exceeds ~30% of total time
on results of more than ~200 K vertices, evaluate Blender's `gpu.compute` or a native worker.

## 7. Languages and ways of programming

| Layer | Current choice | Why | When to change |
|---|---|---|---|
| Orchestration and UI | Python (mandatory in Blender) | It is Blender's API | Never |
| Mesh analysis | Vectorized NumPy | C underneath; no per-vertex loops | If a profile contradicts it |
| Decimation, BVH, QuadriFlow | Blender's C++, used as is | Already exists, tested and optimized | If decimation with hard-edge constraints is needed |
| Per-vertex loops (fitting, transfer) | Python + C++ BVH | They scale with the **result** (≤ hundreds of thousands) | If the profile shows > 30% of the time |
| Alternative engine (QuadWild, Instant Meshes) | **Not yet** | Requires compiling and distributing binaries per platform | Roadmap: separate process (C++ or Rust) with binary exchange |

Writing compiled code now was ruled out: there is no way to test compilation on all of the user's platforms, and
the biggest gain (working copy + cascade) does not need it.

## 8. Preserving detail

A mesh of 4 K–20 K quads **cannot contain** the fine detail of a sculpt of millions of faces. What can be done is:

1. **Preserve the shape:** each level is **fitted to the original sculpt** (not to the working copy), so the
   shape error does not accumulate between levels. Corners are preserved as edge loops (hard edges).
2. **Recover the fine detail** outside the topology: *Multires + reshape* or *normal / displacement bake*. QuadForge
   **does not yet automate** this step (roadmap); the help panel explains it.
3. **Measure it:** distance from the result's vertices to the original surface, relative to its diagonal (`tests/benchmark.py`).

## 9. What makes a topology "nice"

Criteria used for the design **[assumption: usual modeling heuristics, to be validated with the user]**:
almost all quads; few poles; regular quad size; loops that follow the form; exact symmetry; corners with loops.

What QuadForge does: relaxes quad size (polish), preserves hard edges and zone borders, exact symmetry.
What it **does not** do yet: guide loops around joints, place poles away from deformation areas, guide
curves or painted density. See `OPEN_RESEARCH.md`.

## 10. Validation status and risks (important)

**No part of v0.3 has been run inside Blender yet.** Only syntax and text consistency were checked.
Blender API assumptions that must be confirmed with `tests/blender_integration_test.py`:

1. `modifier_apply` with `temp_override` applies Decimate in place on a single-user mesh.
2. The BVH created with `BVHTree.FromObject` returns the **polygon** index (used to transfer zones; if not, the
   code detects it and warns).
3. QuadriFlow respects edges marked as *sharp* and the open border of the cut.
4. `bmesh.ops.mirror` with `merge_dist` welds the central seam; `mesh.attributes.new(..., 'INT', 'FACE')` works.
5. That the reads of `polygon_offset_indices` match the design of the arrays (there is fallback code).

Quality risks **[to measure]**: the engine may ignore some of the hard edges; polishing may flatten very
fine details; the cascade may accumulate small deformations (mitigated by always fitting to the original).
