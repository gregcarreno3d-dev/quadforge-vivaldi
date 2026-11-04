#!/usr/bin/env python3
"""Quality metrics of a quad mesh, as used in docs/TECHNICAL_REPORT.md (section 7).

    python3 tools/quality_metrics.py result.fmesh [--source model.stl]

Prints, for the quads of the result:

  faces            number of polygons
  quad_percent     share of polygons that are quads
  sj_mean          mean scaled Jacobian (1 = perfect square, 0 = degenerate); for each quad the smallest
                   |sin(corner angle)| over its four corners
  sj_worst1        1st percentile of the scaled Jacobian (the worst 1 % of the quads)
  bad_percent      share of quads with a scaled Jacobian below 0.3
  irregular_percent  share of interior vertices whose valence is not 4 (border vertices are ignored)

and, if a source STL is given (needs SciPy), how far the result is from it:

  dev_max_percent  largest distance from a result vertex to the source surface, in % of the source's bounding-box
                   diagonal; dev_p99_percent is the 99th percentile.

The source is sampled with 400,000 random points, so the distance is accurate to a small fraction of an edge.
Needs NumPy; SciPy only for --source.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fmesh import read_fmesh  # noqa: E402


def scaled_jacobian(co, sizes, idx):
    """Scaled Jacobian of every quad (array, one value per quad) and the indices of those quads."""
    offsets = np.concatenate([[0], np.cumsum(sizes)])
    quads = np.flatnonzero(sizes == 4)
    if quads.size == 0:
        return np.zeros(0), quads
    corners = np.stack([idx[offsets[quads] + k] for k in range(4)], axis=1)
    p = co[corners].astype(np.float64)
    normal = np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1])
    normal /= np.linalg.norm(normal, axis=1, keepdims=True) + 1e-30
    worst = np.full(len(quads), np.inf)
    for k in range(4):
        e1 = p[:, (k + 1) % 4] - p[:, k]
        e2 = p[:, (k - 1) % 4] - p[:, k]
        sine = np.einsum("ij,ij->i", np.cross(e1, e2), normal) / (
            np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1) + 1e-30)
        worst = np.minimum(worst, np.abs(sine))
    return worst, quads


def irregular_share(co, sizes, idx):
    """Percentage of interior vertices whose edge valence is not 4."""
    offsets = np.concatenate([[0], np.cumsum(sizes)])
    edge_count = Counter()
    for i in range(len(sizes)):
        face = idx[offsets[i]:offsets[i + 1]]
        for a, b in zip(face, np.roll(face, -1)):
            edge_count[(min(a, b), max(a, b))] += 1
    valence = np.zeros(len(co), dtype=int)
    border = np.zeros(len(co), dtype=bool)
    for (a, b), count in edge_count.items():
        valence[a] += 1
        valence[b] += 1
        if count == 1:
            border[a] = border[b] = True
    used = np.unique(idx)
    interior = used[~border[used]]
    return 100.0 * float(np.mean(valence[interior] != 4)) if interior.size else 0.0


def surface_distance(co, sizes, idx, source_path):
    from scipy.spatial import cKDTree
    from stlio import read_stl
    src_co, src_tris = read_stl(source_path)[:2]
    tri = src_co[src_tris]
    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    rng = np.random.default_rng(0)
    pick = rng.choice(len(tri), 400_000, p=area / area.sum())
    r1 = np.sqrt(rng.random(len(pick)))
    r2 = rng.random(len(pick))
    t = tri[pick]
    points = (1 - r1)[:, None] * t[:, 0] + (r1 * (1 - r2))[:, None] * t[:, 1] + (r1 * r2)[:, None] * t[:, 2]
    diagonal = float(np.linalg.norm(src_co.max(0) - src_co.min(0)))
    distance, _ = cKDTree(points).query(co[np.unique(idx)])
    return 100 * float(distance.max()) / diagonal, 100 * float(np.percentile(distance, 99)) / diagonal


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("result", help="quad mesh in .fmesh format (the engine's output)")
    parser.add_argument("--source", help="source mesh (STL) to measure the distance to")
    args = parser.parse_args()

    co, sizes, idx, _, _ = read_fmesh(args.result)
    jacobian, quads = scaled_jacobian(co, sizes, idx)
    report = {
        "faces": int(len(sizes)),
        "quad_percent": round(100.0 * len(quads) / max(1, len(sizes)), 2),
        "sj_mean": round(float(jacobian.mean()), 4) if jacobian.size else None,
        "sj_worst1": round(float(np.percentile(jacobian, 1)), 4) if jacobian.size else None,
        "bad_percent": round(100.0 * float((jacobian < 0.3).mean()), 3) if jacobian.size else None,
        "irregular_percent": round(irregular_share(co, sizes, idx), 3),
    }
    if args.source:
        dev_max, dev_p99 = surface_distance(co, sizes, idx, args.source)
        report["dev_max_percent"] = round(dev_max, 3)
        report["dev_p99_percent"] = round(dev_p99, 3)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
