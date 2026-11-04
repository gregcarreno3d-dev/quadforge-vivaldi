"""Test harness: run the engine on a mesh, validate the result independently with NumPy, render it."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fmesh import read_fmesh, write_fmesh  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENGINE = Path(__import__("os").environ.get("FORGE_ENGINE", str(ROOT / "engine" / "bin" / "dev" / "forge")))


def run_engine(co, tris, feats=None, faces=2000, mode="tri", density=None, extra=(), check=True, timeout=30):
    tmp = Path(tempfile.mkdtemp(prefix="forge_"))
    fin, fout = tmp / "in.fmesh", tmp / "out.fmesh"
    tris = np.asarray(tris)
    write_fmesh(fin, co, np.full(len(tris), 3), tris.reshape(-1), feats, density)
    cmd = [str(ENGINE), "remesh", "--in", str(fin), "--out", str(fout), "--faces", str(faces),
           "--mode", mode, "--stats"] + (["--check"] if check else []) + list(extra)
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    wall = time.perf_counter() - t0
    info = {"returncode": proc.returncode, "wall": wall, "stderr": proc.stderr.strip()}
    for line in proc.stdout.splitlines():
        tag, _, rest = line.partition(" ")
        if tag in ("R", "S"):
            info[tag] = json.loads(rest)
        elif tag == "ERR":
            info["error"] = rest
    if proc.returncode != 0 or not fout.exists():
        return None, info
    co2, sizes, idx, feats2, dens2 = read_fmesh(fout)
    return (co2, sizes, idx), info


def polygons(sizes, idx):
    out, at = [], 0
    for s in sizes:
        out.append(idx[at:at + s])
        at += s
    return out


def independent_checks(co, sizes, idx):
    """Topology checks that do not trust the engine's own statistics."""
    polys = polygons(sizes, idx)
    edge_count = {}
    for p in polys:
        n = len(p)
        for k in range(n):
            a, b = int(p[k]), int(p[(k + 1) % n])
            edge_count[(a, b)] = edge_count.get((a, b), 0) + 1
    directed_dupes = sum(1 for v in edge_count.values() if v > 1)
    undirected = {}
    for (a, b), v in edge_count.items():
        k = (min(a, b), max(a, b))
        undirected[k] = undirected.get(k, 0) + v
    boundary = sum(1 for v in undirected.values() if v == 1)
    nonman = sum(1 for v in undirected.values() if v > 2)
    used = np.unique(idx)
    euler = len(used) - len(undirected) + len(polys)
    # inconsistent orientation: an undirected edge used twice in the same direction
    degenerate = 0
    for p in polys:
        pts = co[p]
        if len(set(p.tolist())) < len(p):
            degenerate += 1
    unused = len(co) - len(used)
    return dict(euler=int(euler), boundary_edges=boundary, nonmanifold_edges=nonman,
                same_direction_edges=directed_dupes, degenerate_faces=degenerate, unused_vertices=int(unused),
                nan=bool(np.isnan(co).any()))


def sample_surface(co, tris, n, rng):
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    pick = rng.choice(len(tris), size=n, p=area / area.sum())
    r1, r2 = rng.random(n), rng.random(n)
    s = np.sqrt(r1)
    return a[pick] * (1 - s)[:, None] + b[pick] * (s * (1 - r2))[:, None] + c[pick] * (s * r2)[:, None]


def triangulate(sizes, idx):
    tris = []
    for p in polygons(sizes, idx):
        for i in range(1, len(p) - 1):
            tris.append([p[0], p[i], p[i + 1]])
    return np.array(tris)


def hausdorff(co_a, tris_a, co_b, tris_b, n=60000, seed=1):
    """Symmetric sampled distance between two surfaces (max and mean), using SciPy KD-trees."""
    from scipy.spatial import cKDTree
    rng = np.random.default_rng(seed)
    pa = sample_surface(co_a, tris_a, n, rng)
    pb = sample_surface(co_b, tris_b, n, rng)
    d_ab = cKDTree(pb).query(pa)[0]
    d_ba = cKDTree(pa).query(pb)[0]
    return dict(max=float(max(d_ab.max(), d_ba.max())), mean=float((d_ab.mean() + d_ba.mean()) / 2))


def render(co, sizes, idx, path, title="", views=((22, -60), (70, 30)), feats=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    polys = polygons(sizes, idx)
    verts = [co[p] for p in polys]
    n = np.array([np.cross(v[1] - v[0], v[2] - v[0]) for v in verts])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    light = np.array([0.4, -0.5, 0.8])
    light /= np.linalg.norm(light)
    shade = 0.35 + 0.65 * np.clip(n @ light, 0, 1)
    colors = np.stack([shade * 0.62, shade * 0.74, shade * 0.95, np.ones_like(shade)], 1)
    fig = plt.figure(figsize=(5.2 * len(views), 5.2), dpi=110)
    lo, hi = co.min(0), co.max(0)
    centre, rad = (lo + hi) / 2, (hi - lo).max() / 2
    for i, (elev, azim) in enumerate(views):
        ax = fig.add_subplot(1, len(views), i + 1, projection="3d")
        coll = Poly3DCollection(verts, facecolors=colors, edgecolors=(0.05, 0.05, 0.1, 0.9), linewidths=0.35)
        ax.add_collection3d(coll)
        ax.set_xlim(centre[0] - rad, centre[0] + rad)
        ax.set_ylim(centre[1] - rad, centre[1] + rad)
        ax.set_zlim(centre[2] - rad, centre[2] + rad)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev, azim)
        ax.set_axis_off()
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
