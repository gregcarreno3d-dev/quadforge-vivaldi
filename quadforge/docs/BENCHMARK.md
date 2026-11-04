# Benchmark protocol

Goal: turn "optimized" into numbers that can be compared across machines and versions. Everything marked **[to measure]** in
`TECHNICAL_STUDY.md` is answered here.

## 1. How to run it

From the folder that contains `quadforge/`:

```
# Synthetic mesh (no files): icosphere of ~1.3 million triangles
blender --factory-startup -b --python quadforge/tests/benchmark.py -- --synthetic-subdivisions 9 --out sintetica_1M.json

# Your real sculpt
blender tuarchivo.blend -b --python quadforge/tests/benchmark.py -- --object NombreDelObjeto \
    --targets 20000,15000,10000,5000,4000 --sym X --polish 3 --out mi_sculpt.json
```

Useful options for comparing configurations: `--no-proxy` (no working copy), `--no-snap` (no fit to the sculpt),
`--polish N`. The script prints the stages with time and memory, a table ready to paste below, and saves a full JSON.

## 2. Datasets

| Id | Mesh | Faces | Purpose |
|---|---|---|---|
| D1 | Synthetic `--synthetic-subdivisions 8` | ~330 K | Quick test |
| D2 | Synthetic `--synthetic-subdivisions 9` | ~1.3 M | Medium scale |
| D3 | Synthetic `--synthetic-subdivisions 10` | ~5.2 M | Memory stress (watch your RAM) |
| D4 | User's real sculpt | 1.7 M – 3 M | Real use case |

## 3. Configurations to compare

| Id | Working copy | Fit to sculpt | Polish | Levels | What it answers |
|---|---|---|---|---|---|
| A | No | No | 0 | 1 | Baseline (engine directly). May be very slow or fail on D3/D4 |
| B | Yes | No | 0 | 1 | How much does the working copy save? |
| C | Yes | Yes | 0 | 1 | How much detail does the fit recover and what does it cost? |
| D | Yes | Yes | 3 | 1 | What does polishing improve and cost? |
| E | Yes | Yes | 3 | 5 (20K→4K) | Is the cascade cheap? Is it coherent? |
| F | Yes | Yes | 3 | 1, with X symmetry | How much does it save and how much symmetry error remains? |

## 4. Metrics

| Metric | How it is measured | Direction |
|---|---|---|
| Total time and per-stage time | `profile.stages` | lower is better |
| Blender peak memory | `profile.peak_memory_mb` (Linux `VmHWM`, Windows `PeakWorkingSetSize`) | lower is better |
| Quad percentage | `topology.quad_percent` | higher |
| Poles | `poles_3 + poles_5plus` | lower |
| Edge length variation | `edge_length_variation` (deviation / mean) | lower = more regular quads |
| Distance to the original sculpt | mean, p95 and maximum, relative to the diagonal | lower |
| Symmetry error | worst vertex-to-mirror distance / diagonal | lower |
| Open edges at the seam | `border_edges` (0 on a closed model) | 0 |

## 5. **Provisional** acceptance criteria

These are a starting point, not a truth: they will be adjusted after the first data.

- Quad percentage ≥ 95%.
- Mean distance to the sculpt ≤ 1e-3 of the diagonal (with fit enabled).
- Symmetry error ≤ 1e-4 of the diagonal and 0 open edges at the seam.
- Face count within ±15% of the target.
- No Blender crashes on D1–D4.
- Configuration C/D/E must be **clearly faster and use less memory** than A on D2–D4 (threshold to be defined with the data).

## 6. Results template

One table per run. Copy the environment header that the JSON prints.

**Environment:** Blender ___ · OS ___ · CPU ___ (___ threads) · RAM ___ GB · GPU ___ (not used in 0.3)

**Configuration:** ___ · **Mesh:** ___ (___ faces) · **Total:** ___ s · **Peak memory:** ___ MB

| Stage | Seconds | Memory after (MB) |
|---|---|---|
| Read source | | |
| Zone labels | | |
| Working copy | | |
| Symmetry plan | | |
| Clean and cut | | |
| Level 20K: engine | | |
| Level 20K: polish and snap | | |
| ... | | |

| target | faces | % quads | poles | edge var. | mean dist. | max dist. | symmetry error |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## 7. Recorded results

_(empty: add each run here with date, QuadForge version and attached JSON file)_

| Date | Version | Machine | Mesh | Config. | Total (s) | Peak RAM (MB) | Notes |
|---|---|---|---|---|---|---|---|
| | | | | | | | |
