# Open research and roadmap

For anyone who wants to continue or improve this work. Each question states **what to measure** and **what the result would decide**.
All of them rely on `tests/benchmark.py` and `BENCHMARK.md`.

## A. Validation questions (first things first)

| Id | Question | How to answer it | Decides |
|---|---|---|---|
| Q0 | Does v0.3 work in Blender 5.x? (assumptions in section 10 of the study) | `tests/blender_integration_test.py` | What to fix before anything else |
| Q1 | What working-copy size is enough? (2×, 4×, 8× the target) | Config. C with variable `proxy_faces`: time versus distance to the sculpt | The automatic `PROXY_FACTOR` |
| Q2 | Level cascade or independent levels? | Compare E (cascade) with 5 separate runs: time, poles and visual coherence of the flow | Decision D-06 |
| Q3 | Where do time and RAM actually go with 1.7–3 M? | Per-stage profile on D4 | What to optimize first |

## B. Performance improvements

| Id | Idea | Hypothesis | Success criterion |
|---|---|---|---|
| Q4 | **Engine in a separate process** with binary exchange (`.npy`/shared memory) | An engine failure does not close Blender; real cancellation; no text OBJ | Working cancellation; 0 crashes on D3 |
| Q5 | **Decimation that respects hard edges and zones** (quadric error with constraints) in C++ or Rust | The working copy preserves corners better, the engine receives better input | Smaller maximum distance and fewer lost hard edges at the same size |
| Q6 | **Process independent zones in parallel** (one process per zone) and stitch borders | Scales with CPU cores | Time with 8 threads / time with 1 thread |
| Q7 | **GPU** for closest-point queries or for polishing | Only pays off with millions of queries | Apply the criterion in section 6 of the study before starting |
| Q8 | Reduce RAM: read only what is needed from the sculpt, free earlier, `float32` and `int32` everywhere | Lower RAM peak without changing results | Memory peak on D3 |

## C. Topology quality improvements

| Id | Idea | Why it matters |
|---|---|---|
| Q9 | **Guide curves and painted density** (like Quad Remesher) | Loop control at joints, face, hands |
| Q10 | **Pole placement**: move poles away from deformation zones | Better deformation in Unreal/Maya |
| Q11 | **QuadWild backend** (feature-line constraints) versus QuadriFlow | Better respect for corners; more CPU-expensive |
| Q12 | **Formal "pleasing topology" metric** (regularity, poles, alignment of loops with principal curvature) | Today it is a heuristic; without a metric it cannot be optimized |
| Q13 | **Automatic detail transfer** (Multires reshape or normal/displacement bake) | Closes the "preserve detail" loop |
| Q14 | **UVs and color attributes** through the remesh | Workflow toward Unreal/Maya |

## D. Suggested roadmap

1. **0.3.x** · Validate in Blender 5.2 and fix (Q0). Run the benchmark on D1–D4 (Q1, Q3).
2. **0.4** · Decide D-03 and D-06 with data. Engine in a separate process with cancellation (Q4).
3. **0.5** · Optional QuadWild backend (Q11) and guide curves (Q9).
4. **0.6** · Automatic detail transfer (Q13), UVs (Q14).
5. **Research** · Constrained decimation (Q5), zone-level parallelism (Q6), topology metric (Q12).

## E. How to contribute

1. Read `TECHNICAL_STUDY.md` and `DECISIONS.md`.
2. Run `tests/blender_integration_test.py` and `tests/benchmark.py` on your machine; add your row to `BENCHMARK.md`.
3. Pick a question from above. If you change a decision, add a new entry in `DECISIONS.md` with data.
4. Keep the code rules: strings in `texts.py`, pure logic without `bpy` in `planning.py`/`guidance.py`,
   NumPy analysis in `core/geometry.py`, long and clear names, one function = one job.
5. License: GPL-3.0-or-later. Do not include code from proprietary engines.
