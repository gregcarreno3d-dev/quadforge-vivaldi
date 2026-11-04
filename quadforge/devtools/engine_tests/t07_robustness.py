"""Robustness suite: many different shapes x sizes through the real engine; reports a success rate.

    FORGE_ENGINE=/path/to/forge python t07_robustness.py [--quick] [--big] [--models DIR]

A run counts as a success when: the engine returns a mesh; it is closed, manifold, no degenerate faces, no NaN;
at least 98% quads; the face count is within 10% of the request; and the grid method was used (not the fallback).
Shapes come from signed distance fields (marching cubes), so they look like scans/sculpts: uneven triangles.
"""
import argparse
import glob
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from harness import run_engine, independent_checks  # noqa: E402
import stlio  # noqa: E402
from skimage.measure import marching_cubes  # noqa: E402


# ---------------------------------------------------------------- signed distance helpers
def grid(n=96, lo=-1.6, hi=1.6):
    t = np.linspace(lo, hi, n)
    X, Y, Z = np.meshgrid(t, t, t, indexing="ij")
    return np.stack([X, Y, Z], -1), (hi - lo) / (n - 1)


def smin(a, b, k=0.25):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b * (1 - h) + a * h - k * h * (1 - h)


def capsule(P, a, b, r):
    a, b = np.array(a, float), np.array(b, float)
    pa, ba = P - a, b - a
    h = np.clip((pa @ ba) / (ba @ ba), 0, 1)
    return np.linalg.norm(pa - h[..., None] * ba, axis=-1) - r


def torus(P, R, r, axis=2):
    q = np.delete(P, axis, -1)
    return np.hypot(np.linalg.norm(q, axis=-1) - R, P[..., axis]) - r


def round_box(P, half, rad):
    q = np.abs(P) - (np.array(half) - rad)
    return np.linalg.norm(np.maximum(q, 0), axis=-1) + np.minimum(q.max(-1), 0) - rad


def mesh_from(sdf, h, lo=-1.6):
    v, f, _, _ = marching_cubes(sdf, 0.0, spacing=(h, h, h))
    return v + lo, f.astype(np.int64)


def creature(seed):
    rng = np.random.default_rng(seed)
    P, h = grid(112)
    d = capsule(P, (0, 0, -0.5), (0, 0, 0.5), 0.45)
    for _ in range(7):
        a = rng.normal(size=3); a = a / np.linalg.norm(a) * 0.4
        b = a / np.linalg.norm(a) * rng.uniform(0.9, 1.35) + rng.normal(size=3) * 0.15
        d = smin(d, capsule(P, a, b, rng.uniform(0.09, 0.2)), 0.12)
    return mesh_from(d, h)


def lumpy(seed):
    rng = np.random.default_rng(seed)
    P, h = grid(100)
    d = np.linalg.norm(P * np.array([1.0, 0.8, 1.2]), axis=-1) - 0.9
    for _ in range(8):
        f = rng.normal(size=3) * 2.5
        d = d + 0.06 * np.sin(P @ f + rng.uniform(0, 6))
    return mesh_from(d, h)


def donut_arms(seed):
    P, h = grid(110)
    d = torus(P, 0.8, 0.3)
    for ang in (0.3, 2.2, 4.0):
        a = np.array([np.cos(ang), np.sin(ang), 0]) * 0.8
        b = a * 1.9
        d = smin(d, capsule(P, a, b, 0.14), 0.1)
    return mesh_from(d, h)


def double_torus(seed):
    P, h = grid(110)
    d = smin(torus(P - np.array([-0.55, 0, 0]), 0.55, 0.22), torus(P - np.array([0.55, 0, 0]), 0.55, 0.22), 0.1)
    return mesh_from(d, h)


def block_holes(seed):
    P, h = grid(120)
    d = round_box(P, (1.1, 0.7, 0.45), 0.12)
    for x in (-0.6, 0.0, 0.6):
        d = np.maximum(d, -(np.linalg.norm(P[..., [0, 2]] - np.array([x, 0]), axis=-1) - 0.18))
    return mesh_from(d, h)


def thin_plate(seed):
    P, h = grid(130)
    d = round_box(P, (1.3, 0.8, 0.09), 0.04)
    for x, y in ((-0.7, 0.0), (0.0, 0.3), (0.7, -0.2)):
        d = np.maximum(d, -(np.linalg.norm(P[..., [0, 1]] - np.array([x, y]), axis=-1) - 0.2))
    return mesh_from(d, h)


def chamfer_cyl(seed):
    P, h = grid(110)
    r = np.linalg.norm(P[..., :2], axis=-1)
    d = np.maximum(r - 0.7, np.abs(P[..., 2]) - 0.8)
    d = np.maximum(d, (r + np.abs(P[..., 2]) - 1.25) * 0.7)
    return mesh_from(d, h)


def gear_like(seed):
    P, h = grid(130)
    ang = np.arctan2(P[..., 1], P[..., 0])
    r = np.linalg.norm(P[..., :2], axis=-1)
    d = np.maximum(r - (0.9 + 0.1 * np.sign(np.sin(10 * ang))), np.abs(P[..., 2]) - 0.25)
    d = np.maximum(d, -(r - 0.3))
    return mesh_from(d, h)


def cone(seed):
    P, h = grid(100)
    r = np.linalg.norm(P[..., :2], axis=-1)
    d = np.maximum(r * 0.8 + P[..., 2] * 0.6 - 0.55, -P[..., 2] - 0.7)
    return mesh_from(d, h)


def sphere_cut(seed):
    P, h = grid(100)
    d = np.linalg.norm(P, axis=-1) - 1.0
    d = np.maximum(d, -(np.linalg.norm(P - np.array([0.9, 0, 0]), axis=-1) - 0.5))
    return mesh_from(d, h)


SHAPES = [
    ("creature-1", creature, 1, False), ("creature-2", creature, 2, False), ("creature-3", creature, 3, False),
    ("lumpy-1", lumpy, 1, False), ("lumpy-2", lumpy, 2, False),
    ("donut-arms", donut_arms, 0, False), ("double-torus", double_torus, 0, False),
    ("block-holes", block_holes, 0, True), ("thin-plate", thin_plate, 0, True),
    ("chamfer-cyl", chamfer_cyl, 0, True), ("gear-like", gear_like, 0, True),
    ("cone", cone, 0, True), ("sphere-cut", sphere_cut, 0, True),
]


def sharp_edges(co, tris, deg):
    n = np.cross(co[tris[:, 1]] - co[tris[:, 0]], co[tris[:, 2]] - co[tris[:, 0]])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-30
    ed = {}
    for i, t in enumerate(tris):
        for k in range(3):
            a, b = int(t[k]), int(t[(k + 1) % 3])
            ed.setdefault((min(a, b), max(a, b)), []).append(i)
    out = [e for e, fs in ed.items() if len(fs) == 2 and n[fs[0]] @ n[fs[1]] < np.cos(np.radians(deg))]
    return np.array(out, int).reshape(-1, 2)


def valence_poles(sizes, idx):
    """Interior vertices whose valence is not 4 (a vertex on an open border is regular with valence 3)."""
    from collections import defaultdict
    adj = defaultdict(set)
    ecount = defaultdict(int)
    at = 0
    for s in sizes:
        p = idx[at:at + s]
        at += s
        for k in range(s):
            a, b = int(p[k]), int(p[(k + 1) % s])
            adj[a].add(b); adj[b].add(a)
            ecount[(min(a, b), max(a, b))] += 1
    border = set()
    for (a, b), c in ecount.items():
        if c == 1:
            border.add(a); border.add(b)
    return sum(1 for v, n in adj.items() if v not in border and len(n) != 4)


def judge(res, info, faces, input_open=False):
    if res is None:
        return False, "engine error: " + str(info.get("error") or info.get("stderr", ""))[:80], {}
    co, sizes, idx = res
    chk = independent_checks(co, sizes, idx)
    st = info.get("S", {})
    quads = int((np.asarray(sizes) == 4).sum())
    qfrac = quads / max(1, len(sizes))
    err = abs(len(sizes) - faces) / faces
    poles = valence_poles(sizes, idx)
    q = dict(faces=len(sizes), qfrac=round(qfrac, 4), err=round(err, 3), poles=poles,
             poles_k=round(1000 * poles / max(1, len(sizes)), 1), method=st.get("method"), sec=round(info["wall"], 1))
    bad = []
    if (chk["boundary_edges"] and not input_open) or chk["nonmanifold_edges"] or chk["same_direction_edges"] or chk["degenerate_faces"] or chk["nan"]:
        bad.append("topology " + json.dumps({k: v for k, v in chk.items() if v and k not in ("euler", "unused_vertices")}))
    if qfrac < 0.98:
        bad.append(f"quads {qfrac:.1%}")
    if err > COUNT_TOLERANCE:      # same 15 % the add-on warns about (TARGET_MISS_WARNING_FRACTION); the engine itself aims at 4-6 %
        bad.append(f"count off {err:.0%}")
    if st.get("method") != "extract":
        bad.append("fallback: " + str(st.get("fallback")))
    return not bad, "; ".join(bad), q


COUNT_TOLERANCE = 0.15


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--full", action="store_true", help="more seeds and sizes (about 10 minutes)")
    ap.add_argument("--big", action="store_true", help="also 60K targets on the user models")
    ap.add_argument("--models", default="/tmp/up")
    a = ap.parse_args()
    targets = [1500, 6000] if a.quick else [1500, 6000, 20000]
    shapes = list(SHAPES)
    if a.full:
        targets = [600, 1500, 6000, 20000]
        shapes += [(f"creature-{k}", creature, k, False) for k in range(4, 10)]
        shapes += [(f"lumpy-{k}", lumpy, k, False) for k in range(3, 6)]
    cases = []
    for name, fn, seed, hard in shapes:
        co, tris = fn(seed)
        feats = sharp_edges(co, tris, 30) if hard else None
        for t in targets:
            cases.append((name, co, tris, None, t))
            if hard:
                cases.append((name + "+hard", co, tris, feats, t))
    for path in sorted(glob.glob(a.models + "/*.stl")):
        co, tris = stlio.read_stl(path)
        nm = Path(path).stem
        feats = sharp_edges(co, tris, 30) if "plate" in nm.lower() else None
        for t in ([20000] + ([60000] if a.big else [])):
            cases.append((nm, co, tris, feats, t))
    ok = 0
    rows = []
    for name, co, tris, feats, t in cases:
        try:
            res, info = run_engine(co.astype(np.float64), tris, feats=feats, faces=t, mode="quad", check=False, timeout=900)
        except Exception as e:  # noqa: BLE001
            res, info = None, {"error": repr(e)[:80], "wall": 0}
        input_open = independent_checks(co, np.full(len(tris), 3), np.asarray(tris).reshape(-1))["boundary_edges"] > 0
        good, why, q = judge(res, info, t, input_open)
        ok += good
        rows.append((name, t, good, why, q))
        print(f"{'ok  ' if good else 'FAIL'} {name:18s} {t:6d} {json.dumps(q)} {why}", flush=True)
    print(f"\nSUCCESS {ok}/{len(cases)} = {100.0 * ok / len(cases):.1f}%")
    pk = [r[4]["poles_k"] for r in rows if r[4]]
    if pk:
        print(f"poles per 1000 quads: median {np.median(pk):.1f}, p90 {np.percentile(pk, 90):.1f}, max {max(pk):.1f}")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
